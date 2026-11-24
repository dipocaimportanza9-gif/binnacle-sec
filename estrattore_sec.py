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
TICKERS = [t.upper() for t in sys.argv[1:]] or ["AMZN"]
OUT = os.environ.get("OUT_DIR", ".")
os.makedirs(OUT, exist_ok=True)
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
 ("dCur",     "Debito a lungo termine: quota corrente",         ["LongTermDebtCurrent", "DebtCurrent", "LongTermDebtAndCapitalLeaseObligationsCurrent", "LongTermDebtAndFinanceLeaseObligationsCurrent"]),
 ("dNon",     "Debito a lungo termine: non corrente",           ["LongTermDebtNoncurrent", "LongTermDebtAndCapitalLeaseObligations", "LongTermDebtAndFinanceLeaseObligationsNoncurrent", "LongTermNotesPayable"]),
 ("dTot",     "Debito a lungo termine: totale",                 ["LongTermDebt", "DebtLongtermAndShorttermCombinedAmount", "LongTermDebtAndCapitalLeaseObligationsIncludingCurrentMaturities"]),
 ("dBrev",    "Debiti finanziari a breve",                      ["ShortTermBorrowings", "CommercialPaper", "LoansPayableCurrent", "NotesPayableCurrent", "OtherShortTermBorrowings"]),
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


FAM = {"d": re.compile(r"Debt|Borrowing|NotesPayable|LoansPayable|CommercialPaper", re.I),
       "l": re.compile(r"Lease", re.I),
       "i": re.compile(r"Investment|Securities|EquityMethod", re.I)}
def famiglia(k):
    return FAM["d"] if k[0] == "d" else FAM["l"] if k[:2] in ("op", "fi", "ca", "im") else FAM["i"]

def carica_alias():
    try: return json.load(open("cik_alias.json"))
    except Exception: return {}

def unisci(cfs):
    """unisce i companyfacts di piu' CIK (azienda che ha cambiato CIK): gli elenchi di fatti si sommano"""
    out = {}
    for cf in cfs:
        for tg, o in cf["facts"].get("us-gaap", {}).items():
            t = out.setdefault(tg, {"units": {"USD": []}})
            t["units"]["USD"] += o["units"].get("USD", [])
    return out

def candidati(gaap, k, noti):
    """tag che non sono nel catalogo ma hanno valori annuali recenti: aiutano a completare il catalogo"""
    out = []
    for tg, o in gaap.items():
        if tg in noti or not famiglia(k).search(tg): continue
        v = annuali(o)
        rec = {y: round(x / 1e6) for y, x in v.items() if y >= 2023}
        if rec: out.append((max(abs(x) for x in rec.values()), tg, rec))
    return [{"tag": t, "valori": r} for _, t, r in sorted(out, reverse=True)[:6]]

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

MP = None
def main(TK):
    global MP
    t0 = time.time()
    if MP is None: MP = get("https://www.sec.gov/files/company_tickers.json")
    mp = MP
    cik = next(("%010d" % v["cik_str"] for v in mp.values() if v["ticker"].upper() == TK), None)
    if not cik: raise RuntimeError("Ticker non trovato: " + TK)
    ciks = [cik] + [c for c in carica_alias().get(TK, []) if c != cik]
    cfs = []
    for c in ciks:
        try: cfs.append(get("https://data.sec.gov/api/xbrl/companyfacts/CIK%s.json" % c))
        except Exception as e: print("  %s: companyfacts non disponibile (%s)" % (c, e))
    gaap = unisci(cfs)
    res = {"ticker": TK, "cik": cik, "cik_usati": ciks, "concetti": {}, "tag_usato": {}, "custom": {}, "avvisi": [], "candidati": {}}
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
        if val and max(val) >= 2022 and max(val) < 2025 and min(val) <= 2022:
            res["avvisi"].append("%s: ultimo anno %d (mancano anni recenti)" % (desc, max(val)))
        if k in ("dCur", "dNon", "dTot", "dBrev", "opLiab", "finLiab", "eqMeth", "ltInv", "afsDeb", "eqPriv") and (not val or max(val) < 2025):
            c = candidati(gaap, k, set(tags))
            if c: res["candidati"][k] = c
    # tag propri: ultimi 10-K in iXBRL
    ks = []
    for c in ciks:
        try:
            sub = get("https://data.sec.gov/submissions/CIK%s.json" % c)["filings"]["recent"]
            ks += [(sub["filingDate"][i], sub["accessionNumber"][i], sub["primaryDocument"][i], c) for i, f in enumerate(sub["form"]) if f == "10-K"]
        except Exception as e: print("  %s: elenco depositi non disponibile (%s)" % (c, e))
    ks = sorted(ks, reverse=True)[:6]
    for d, acc, doc, cc in ks:
        try: html = get("https://www.sec.gov/Archives/edgar/data/%d/%s/%s" % (int(cc), acc.replace("-", ""), doc), True)
        except Exception as e: print("10-K %s non scaricato: %s" % (d, e)); continue
        for tag, byy in custom_da_ixbrl(html).items():
            t = res["custom"].setdefault(tag, {})
            for y, v in byy.items():
                if y not in t: t[y] = round(v / 1e6, 1)
    if not any(res["concetti"].values()): res["avvisi"].append("NESSUN DATO: possibile cambio di CIK (aggiungere l'alias in cik_alias.json)")
    json.dump(res, open(os.path.join(OUT, "sec_%s.json" % TK), "w"), indent=1, ensure_ascii=False)
    out = ["%s (CIK %s) - valori in milioni di dollari" % (TK, cik), "%-62s" % "concetto" + " ".join("%8d" % y for y in ANNI)]
    for desc, val in righe:
        if desc == "§": out.append("\n--- " + val + " ---"); continue
        out.append("%-62s" % desc[:62] + " ".join(("%8s" % format(round(val[y]), ",")) if y in val else "%8s" % "." for y in ANNI))
    out.append("\n--- TAG PROPRI DELL'AZIENDA (estensioni, dai 10-K in iXBRL) ---")
    for tag, byy in sorted(res["custom"].items()):
        out.append("%-62s" % tag[:62] + " ".join(("%8s" % format(round(byy[y]), ",")) if y in byy else "%8s" % "." for y in ANNI))
    if res["avvisi"]:
        out.append("\n--- AVVISI ---"); out += res["avvisi"]
    if res["candidati"]:
        out.append("\n--- TAG CANDIDATI (non in catalogo, con valori 2023+) ---")
        for k, lst in res["candidati"].items():
            for c in lst: out.append("%s: %s %s" % (k, c["tag"], c["valori"]))
    out.append("\nTempo: %d s. File scritto: sec_%s.json" % (time.time() - t0, TK))
    txt = "\n".join(out); print(txt); open(os.path.join(OUT, "sec_%s_tabella.txt" % TK), "w", encoding="utf-8").write(txt)

if __name__ == "__main__":
    ko = []
    for t in TICKERS:
        try: main(t); print("OK", t)
        except Exception as e: ko.append(t); print("ERRORE", t, e)
    print("Falliti:", ko or "nessuno")
    if len(ko) == len(TICKERS): sys.exit(1)
