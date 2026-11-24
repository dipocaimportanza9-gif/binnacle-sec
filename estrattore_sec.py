#!/usr/bin/env python3
"""Estrattore SEC (sola lettura sulla SEC; scrive solo un file JSON e un riepilogo nella cartella corrente).
Uso:  SEC_UA="Nome Cognome tua@email.it" python3 estrattore_sec.py AMZN
Per il ticker produce sec_<TICKER>.json con, per ogni 'concetto' del catalogo, il valore per anno (milioni di dollari) e il tag usato.
1) tag standard us-gaap da 'companyfacts' (con alternative in ordine di priorita');
2) tag PROPRI dell'azienda (estensioni, es. financing obligations) letti dagli ultimi 10-K in formato iXBRL.
Stampa una tabella concetto x anno da confrontare col Convertitore Leasing."""
import os, re, sys, json, gzip, time, datetime, urllib.request

UA = os.environ.get("SEC_UA", "").strip()
if "@" not in UA:
    sys.exit('Imposta SEC_UA, ad esempio: SEC_UA="Nome Cognome tua@email.it" python3 estrattore_sec.py AMZN')
TK = (sys.argv[1] if len(sys.argv) > 1 else "AMZN").upper()
ANNI = list(range(2015, 2026))

# concetto -> (descrizione, [tag us-gaap in ordine di priorita'])
CAT = [
 ("leasing", "LEASING OPERATIVI (dal 2019)"),
 ("opLiab",   "Leasing operativi: debito totale",               ["OperatingLeaseLiability"]),
 ("opCur",    "Leasing operativi: quota corrente",              ["OperatingLeaseLiabilityCurrent"]),
 ("opNon",    "Leasing operativi: quota non corrente",          ["OperatingLeaseLiabilityNoncurrent"]),
 ("opCost",   "Leasing operativi: costo (affitti) dell'anno",   ["OperatingLeaseCost", "OperatingLeasesRentExpenseNet"]),
 ("leasing", "LEASING FINANZIARI / CAPITAL LEASE"),
 ("finLiab",  "Leasing finanziari: debito totale",              ["FinanceLeaseLiability"]),
 ("finCur",   "Leasing finanziari: quota corrente",             ["FinanceLeaseLiabilityCurrent"]),
 ("finNon",   "Leasing finanziari: quota non corrente",         ["FinanceLeaseLiabilityNoncurrent"]),
 ("capCur",   "Capital lease (prima del 2019): quota corrente", ["CapitalLeaseObligationsCurrent"]),
 ("capNon",   "Capital lease (prima del 2019): non corrente",   ["CapitalLeaseObligationsNoncurrent"]),
 ("capTot",   "Capital lease (prima del 2019): totale",         ["CapitalLeaseObligations"]),
 ("finRep",   "Rimborsi capitale leasing finanziari",           ["FinanceLeasePrincipalPayments", "RepaymentsOfLongTermCapitalLeaseObligations"]),
 ("capNew",   "Beni acquisiti con capital lease (non cash)",    ["CapitalLeaseObligationsIncurred", "NoncashOrPartNoncashAcquisitionFixedAssetsAcquired1"]),
 ("leasing", "IMPEGNI LEASING OPERATIVI (prima del 2019, ASC 840)"),
 ("imp1",     "Impegni: totale",                                ["OperatingLeasesFutureMinimumPaymentsDue"]),
 ("imp1y",    "Impegni: entro 1 anno",                          ["OperatingLeasesFutureMinimumPaymentsDueCurrent"]),
 ("imp2y",    "Impegni: anno 2",                                ["OperatingLeasesFutureMinimumPaymentsDueInTwoYears"]),
 ("imp3y",    "Impegni: anno 3",                                ["OperatingLeasesFutureMinimumPaymentsDueInThreeYears"]),
 ("imp4y",    "Impegni: anno 4",                                ["OperatingLeasesFutureMinimumPaymentsDueInFourYears"]),
 ("imp5y",    "Impegni: anno 5",                                ["OperatingLeasesFutureMinimumPaymentsDueInFiveYears"]),
 ("impOltre", "Impegni: oltre il quinto anno",                  ["OperatingLeasesFutureMinimumPaymentsDueThereafter"]),
 ("debito", "DEBITO FINANZIARIO"),
 ("dCur",     "Debito a lungo termine: quota corrente",         ["LongTermDebtCurrent", "DebtCurrent"]),
 ("dNon",     "Debito a lungo termine: non corrente",           ["LongTermDebtNoncurrent"]),
 ("dTot",     "Debito a lungo termine: totale",                 ["LongTermDebt"]),
 ("dBrev",    "Debiti finanziari a breve",                      ["ShortTermBorrowings", "CommercialPaper"]),
 ("inv", "INVESTIMENTI NON OPERATIVI e OCI"),
 ("eqPriv",   "Equity in societa' non quotate (valore contabile)", ["EquitySecuritiesWithoutReadilyDeterminableFairValueAmount"]),
 ("eqMeth",   "Partecipazioni a patrimonio netto (equity method)", ["EquityMethodInvestments"]),
 ("afsDeb",   "Titoli di debito disponibili per la vendita (valore equo)", ["AvailableForSaleSecuritiesDebtSecurities"]),
 ("mktNon",   "Titoli di mercato non correnti",                 ["MarketableSecuritiesNoncurrent", "AvailableForSaleSecuritiesDebtSecuritiesNoncurrent"]),
 ("ltInv",    "Investimenti a lungo termine (voce di bilancio)", ["LongTermInvestments"]),
 ("aoci",     "OCI accumulato (utili/perdite non realizzati)",  ["AccumulatedOtherComprehensiveIncomeLossNetOfTax"]),
]
RE_CUSTOM = re.compile(r"Financing|BuildToSuit|Construction|Lease|Debt|Investment|Warrant|Convertible", re.I)

def get(url, testo=False):
    time.sleep(0.2)
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Encoding": "gzip"})
    with urllib.request.urlopen(req, timeout=180) as r:
        b = r.read()
        if r.headers.get("Content-Encoding") == "gzip": b = gzip.decompress(b)
    return b.decode("utf-8", "replace") if testo else json.loads(b)

def annuali(o):
    """anno -> valore in USD (ultimo deposito 10-K; per i flussi solo periodi di circa 12 mesi)"""
    best = {}
    for x in o["units"].get("USD", []):
        if x.get("form") != "10-K" or x.get("fp") != "FY": continue
        if "start" in x:
            d = (datetime.date.fromisoformat(x["end"]) - datetime.date.fromisoformat(x["start"])).days
            if not 350 <= d <= 380: continue
        y = x["end"][:4]
        if y not in best or x["filed"] > best[y]["filed"]: best[y] = x
    return {int(y): v["val"] for y, v in best.items()}

def custom_da_ixbrl(html):
    """fatti con tag PROPRI dell'azienda (prefisso diverso da us-gaap/dei), senza dimensioni: {tag: {anno: valore_in_USD}}"""
    ctx = {}
    for m in re.finditer(r'<xbrli:context id="([^"]+)">(.*?)</xbrli:context>', html, re.S):
        body = m.group(2)
        if "explicitMember" in body or "typedMember" in body: continue
        d = re.search(r"<xbrli:instant>([^<]+)</xbrli:instant>", body)
        if d: ctx[m.group(1)] = d.group(1).strip()
    out = {}
    for m in re.finditer(r"<ix:nonFraction([^>]*)>(.*?)</ix:nonFraction>", html, re.S):
        a, txt = m.group(1), re.sub(r"<[^>]+>", "", m.group(2))
        nm = re.search(r'name="([^"]+)"', a); cr = re.search(r'contextRef="([^"]+)"', a)
        if not nm or not cr or cr.group(1) not in ctx: continue
        pref, _, tag = nm.group(1).partition(":")
        if pref in ("us-gaap", "dei", "srt", "ecd", "ffd") or not RE_CUSTOM.search(tag): continue
        try: v = float(txt.replace(",", "").strip())
        except ValueError: continue
        sc = re.search(r'scale="(-?\d+)"', a); v *= 10 ** int(sc.group(1)) if sc else 1
        if 'sign="-"' in a: v = -v
        out.setdefault(pref + ":" + tag, {})[int(ctx[cr.group(1)][:4])] = v
    return out

def main():
    t0 = time.time()
    mp = get("https://www.sec.gov/files/company_tickers.json")
    cik = next(("%010d" % v["cik_str"] for v in mp.values() if v["ticker"].upper() == TK), None)
    if not cik: sys.exit("Ticker non trovato: " + TK)
    cf = get("https://data.sec.gov/api/xbrl/companyfacts/CIK%s.json" % cik)
    gaap = cf["facts"].get("us-gaap", {})
    res = {"ticker": TK, "cik": cik, "concetti": {}, "tag_usato": {}, "custom": {}}
    righe, sez = [], ""
    for c in CAT:
        if len(c) == 2: righe.append(("§", c[1])); continue
        k, desc, tags = c
        val, usato = {}, {}
        for tg in tags:
            if tg in gaap:
                for y, v in annuali(gaap[tg]).items():
                    if y not in val: val[y] = round(v / 1e6, 1); usato[y] = tg
        res["concetti"][k] = val; res["tag_usato"][k] = usato
        righe.append((desc, val))
    # tag propri: ultimi 10-K in iXBRL
    sub = get("https://data.sec.gov/submissions/CIK%s.json" % cik)["filings"]["recent"]
    ks = [(sub["filingDate"][i], sub["accessionNumber"][i], sub["primaryDocument"][i]) for i, f in enumerate(sub["form"]) if f == "10-K"][:6]
    for d, acc, doc in ks:
        try: html = get("https://www.sec.gov/Archives/edgar/data/%d/%s/%s" % (int(cik), acc.replace("-", ""), doc), True)
        except Exception as e: print("10-K %s non scaricato: %s" % (d, e)); continue
        for tag, byy in custom_da_ixbrl(html).items():
            t = res["custom"].setdefault(tag, {})
            for y, v in byy.items():
                if y not in t: t[y] = round(v / 1e6, 1)
    json.dump(res, open("sec_%s.json" % TK, "w"), indent=1, ensure_ascii=False)
    out = ["%s (CIK %s) - valori in milioni di dollari" % (TK, cik), "%-62s" % "concetto" + " ".join("%8d" % y for y in ANNI)]
    for desc, val in righe:
        if desc == "§": out.append("\n--- " + val + " ---"); continue
        out.append("%-62s" % desc[:62] + " ".join(("%8s" % format(round(val[y]), ",")) if y in val else "%8s" % "." for y in ANNI))
    out.append("\n--- TAG PROPRI DELL'AZIENDA (estensioni, dai 10-K in iXBRL) ---")
    for tag, byy in sorted(res["custom"].items()):
        out.append("%-62s" % tag[:62] + " ".join(("%8s" % format(round(byy[y]), ",")) if y in byy else "%8s" % "." for y in ANNI))
    out.append("\nTempo: %d s. File scritto: sec_%s.json" % (time.time() - t0, TK))
    txt = "\n".join(out); print(txt); open("sec_%s_tabella.txt" % TK, "w", encoding="utf-8").write(txt)

if __name__ == "__main__": main()
