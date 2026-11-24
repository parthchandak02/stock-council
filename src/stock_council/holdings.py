"""Shareholding history like Screener's: promoters, FIIs, DIIs, government and public over ~12 quarters,
each with its named holders over 1%, from NSE's quarterly shareholding-pattern filings (XBRL).

"Pure public" = public (non-institutional) minus the named public holders over 1% — suspense
accounts, IEPF, large individual investors, companies — i.e. what is left with small shareholders.
Filings never change once published, so each one is parsed once and cached for good.
"""
from __future__ import annotations

import html
import re
import time

from .nse_api import NSE
from .store import cache_get, cache_put, connect

TOTALS = {
    "promoter": "ShareholdingOfPromoterAndPromoterGroup_ContextI",
    "fii": "InstitutionsForeign_ContextI",
    "dii": "InstitutionsDomestic_ContextI",
    "public": "NonInstitutions_ContextI",
}
QUARTER_ENDS = {"31-MAR", "30-JUN", "30-SEP", "31-DEC"}
GOVT_HINTS = ("CentralGovernment", "StateGovernment", "Government")
PROMOTER_PREFIXES = ("IndividualsOrHUF", "OthersIndianShareholders", "IndividualsOrHinduUndividedFamily",
                     "ForeignIndividualsOrNRI", "IndividualsNonResidentIndividualsOrForeignIndividuals",
                     "ForeignPromoter", "PromoterGroup", "OtherForeignShareholders", "ForeignShareholders")
FII_HINTS = ("Foreign", "FPI", "PortfolioInvestor")
DII_PREFIXES = ("MutualFunds", "AlternativeInvestmentFunds", "InsuranceCompanies", "Banks", "ProvidentFunds",
                "PensionFunds", "OtherFinancialInstitutions", "NBFC", "VentureCapital", "AssetReconstruction",
                "SovereignWealthFundsDomestic", "InstitutionsDomestic", "FinancialInstitutions", "OtherInstitutions")

PCT = re.compile(r'<in-bse-shp:ShareholdingAsAPercentageOfTotalNumberOfShares contextRef="([^"]+)"[^>]*>([^<]+)<')
NAME = re.compile(r'<in-bse-shp:NameOfTheShareholder contextRef="D_([^"]+)"[^>]*>([^<]+)<')
HOLDERS_N = re.compile(r'<in-bse-shp:NumberOfShareholders contextRef="ShareholdingPattern_ContextI"[^>]*>([^<]+)<')


def _group(ctx: str) -> str:
    prefix = ctx.split("_Context")[0]
    if prefix.startswith(PROMOTER_PREFIXES):
        return "promoter"
    if any(h in prefix for h in GOVT_HINTS):
        return "govt"
    if prefix.startswith("Institutions") or prefix.startswith(DII_PREFIXES) or "Foreign" in prefix and "Portfolio" in prefix:
        return "fii" if any(h in prefix for h in FII_HINTS) else "dii"
    return "public"   # bodies corporate, individuals over ₹2 lakh, IEPF, suspense accounts, NRIs, trusts…


def parse(xml: str) -> dict:
    raw = {c: float(v) for c, v in PCT.findall(xml)}
    # newer filings write 0.7265, some older BSE ones 72.65: detect the scale from the file itself
    scale = 1.0 if raw and max(raw.values()) > 1.0001 else 100.0
    pct = {c: v * scale for c, v in raw.items()}
    out = {k: round(pct[c], 2) if c in pct else None for k, c in TOTALS.items()}
    if out["dii"] is None and "Institutions_ContextI" in pct:   # older format: one institutions total
        out["dii"] = round(pct["Institutions_ContextI"] - (out["fii"] or 0), 2)
    if out["fii"] is None:
        out["fii"] = 0.0
    if out["dii"] is None:
        out["dii"] = 0.0
    # the government total; older filings spell it "Goverments"
    govt = pct.get("Governments_ContextI", pct.get("Goverments_ContextI", 0.0))
    out["govt"] = round(govt, 2)
    holders = []
    for ctx, name in NAME.findall(xml):
        v = pct.get(ctx)
        if v is None or v < 1.0:     # named holders are disclosed from 1% upwards
            continue
        holders.append({"name": html.unescape(name).strip(), "group": _group(ctx), "pct": round(v, 2)})
    out["holders"] = holders
    m = HOLDERS_N.search(xml)
    out["shareholders"] = int(float(m.group(1))) if m else None
    named_public = sum(h["pct"] for h in holders if h["group"] == "public")
    out["pure_public"] = round(out["public"] - named_public, 2) if out["public"] is not None else None
    return out


MONTH_END = {"March": "31-MAR", "June": "30-JUN", "September": "30-SEP", "December": "31-DEC"}


def _bse_filings(code: str):
    """(quarter label like 30-JUN-2024, XBRL url) newest first, the latest revision of each quarter."""
    from curl_cffi import requests
    s = requests.Session(impersonate="chrome")
    s.get("https://www.bseindia.com/", timeout=30)
    time.sleep(1)
    h = {"Referer": "https://www.bseindia.com/", "Origin": "https://www.bseindia.com"}
    rows = s.get(f"https://api.bseindia.com/BseIndiaAPI/api/SHPQNewFormat/w?scripcode={code}&qtrid=",
                 headers=h, timeout=30).json().get("Table") or []
    best: dict[str, dict] = {}
    for r in rows:
        m = re.fullmatch(r"(March|June|September|December) (\d{4})", (r.get("qtr") or "").strip())
        if not m or not (r.get("xbrlurl") or r.get("XbrlFile")):
            continue
        label = f"{MONTH_END[m.group(1)]}-{m.group(2)}"
        when = r.get("revised_date_time") or r.get("filing_date_time") or ""
        if label not in best or when > best[label]["_when"]:
            best[label] = {**r, "_when": when}
    out = []
    for label, r in best.items():
        # the raw XBRL is always at /XBRL1/<file>; "xbrlurl" is sometimes an HTML rendering
        path = f"/XBRL1/{r['XbrlFile']}" if r.get("XbrlFile") else r["xbrlurl"]
        out.append((label, path if path.startswith("http") else f"https://www.bseindia.com{path}"))
    out.sort(key=lambda x: (x[0][-4:], ["MAR", "JUN", "SEP", "DEC"].index(x[0][3:6])), reverse=True)
    return out, s


def history(symbol: str, bse_code: str | None = None, quarters: int = 12, nse: NSE | None = None) -> list[dict]:
    """Oldest first, like Screener's table. BSE's filings when the company is on BSE (longer history,
    revisions included), otherwise NSE's."""
    conn = connect()
    key = f"holdings:{symbol}"
    cached = cache_get(conn, key, max_age_days=6)
    if cached is not None:
        return cached
    filings: list[tuple[str, str]] = []
    getter = None
    if bse_code:
        try:
            filings, sess = _bse_filings(bse_code)
            getter = lambda u: sess.get(u, headers={"Referer": "https://www.bseindia.com/"}, timeout=60).text
        except Exception:
            filings = []
    if len(filings) < 4 and symbol:
        nse = nse or NSE()
        rows = nse._get_json(f"corporate-share-holdings-master?index=equities&symbol={symbol}")
        seen = set()
        filings = []
        for r in rows:   # quarter-end filings only; companies also file around splits, mergers, etc.
            d = (r.get("date") or "").upper()
            if d[:6] in QUARTER_ENDS and d not in seen and r.get("xbrl"):
                seen.add(d)
                filings.append((d, r["xbrl"]))
        getter = lambda u: nse.s.get(u, timeout=60).text
    out = []
    for label, url in filings[:quarters]:
        q = cache_get(conn, f"shp:{url}", max_age_days=100_000)   # a filing never changes
        if q is None:
            time.sleep(0.6)
            try:
                q = parse(getter(url))
            except Exception:
                continue
            if q.get("promoter") is None and q.get("public") is None:
                continue                    # not a readable filing (error page, other format): don't keep it
            cache_put(conn, f"shp:{url}", q)
        out.append({"quarter": label, **q})
    out.reverse()
    cache_put(conn, key, out)
    return out
