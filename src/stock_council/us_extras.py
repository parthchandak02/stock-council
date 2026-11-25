"""US-only fact-sheet sections from SEC EDGAR (free, official): insider trades (Form 4) and
8-K announcements. Ownership percentages and short interest come with the Yahoo fundamentals."""
from __future__ import annotations

import logging
import re
import time
from datetime import date, timedelta
from xml.etree import ElementTree as ET

import httpx

from .store import cache_get, cache_put
from .us_data import SEC_UA

log = logging.getLogger(__name__)

ITEMS = {"1.01": "Material agreement", "1.02": "Agreement ended", "1.03": "Bankruptcy", "2.01": "Acquisition or sale",
         "2.02": "Results", "2.03": "New debt", "2.05": "Restructuring costs", "2.06": "Impairment",
         "3.01": "Delisting notice", "3.02": "Share sale (unregistered)", "4.01": "Auditor change",
         "4.02": "Restatement warning", "5.01": "Change of control", "5.02": "Director or officer change",
         "5.03": "Bylaws change", "5.07": "Shareholder vote", "7.01": "Investor disclosure", "8.01": "Other event",
         "9.01": "Exhibits"}


def _sec(url: str) -> httpx.Response:
    from .us_data import sec_wait
    sec_wait()   # shared across the parallel stocks: SEC allows 10 requests a second
    return httpx.get(url, headers={"User-Agent": SEC_UA}, timeout=30, follow_redirects=True)


def submissions(cik: str) -> dict:
    r = _sec(f"https://data.sec.gov/submissions/CIK{cik}.json")
    r.raise_for_status()
    return r.json()


def _recent(sub: dict) -> list[dict]:
    rec = sub.get("filings", {}).get("recent", {})
    keys = ["form", "filingDate", "acceptanceDateTime", "items", "accessionNumber", "primaryDocument"]
    return [dict(zip(keys, vals)) for vals in zip(*(rec.get(k, []) for k in keys))]


def announcements(cik: str, sub: dict, days: int = 30, limit: int = 12) -> list[dict]:
    since = (date.today() - timedelta(days=days)).isoformat()
    out = []
    for f in _recent(sub):
        if f["form"] not in ("8-K", "8-K/A", "6-K") or f["filingDate"] < since:
            continue
        items = [i.strip() for i in (f.get("items") or "").split(",") if i.strip() and i.strip() != "9.01"]
        names = ", ".join(ITEMS.get(i, f"Item {i}") for i in items) or ("Foreign issuer report" if f["form"] == "6-K" else "Filing")
        acc = f["accessionNumber"].replace("-", "")
        out.append({"at": (f.get("acceptanceDateTime") or f["filingDate"])[:16], "subject": f["form"],
                    "text": names, "url": f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{acc}/{f['primaryDocument']}"})
    return out[:limit]


def insider_trades(cik: str, sub: dict, days: int = 90, max_filings: int = 25) -> dict:
    """Open-market buys (code P) and sales (code S) by officers, directors and 10% owners."""
    since = (date.today() - timedelta(days=days)).isoformat()
    forms = [f for f in _recent(sub) if f["form"] == "4" and f["filingDate"] >= since][:max_filings]
    buys = sells = 0.0
    buyers, sellers, trades = set(), set(), []
    failed = 0
    for f in forms:
        acc = f["accessionNumber"].replace("-", "")
        doc = re.sub(r"^xsl[^/]+/", "", f["primaryDocument"])   # raw XML, not the rendered page
        try:
            x = ET.fromstring(_sec(f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{acc}/{doc}").content)
        except Exception:
            failed += 1
            continue
        who = (x.findtext(".//reportingOwner/reportingOwnerId/rptOwnerName") or "").strip()
        role = x.findtext(".//reportingOwnerRelationship/officerTitle") or (
            "Director" if (x.findtext(".//reportingOwnerRelationship/isDirector") or "").strip() in ("1", "true") else "")
        for t in x.findall(".//nonDerivativeTable/nonDerivativeTransaction"):
            code = t.findtext("transactionCoding/transactionCode")
            if code not in ("P", "S"):
                continue   # skip grants, option exercises, gifts, tax withholding
            try:
                sh = float(t.findtext("transactionAmounts/transactionShares/value") or 0)
                px = float(t.findtext("transactionAmounts/transactionPricePerShare/value") or 0)
            except ValueError:
                continue
            val = sh * px
            if code == "P":
                buys += val; buyers.add(who)
            else:
                sells += val; sellers.add(who)
            trades.append({"date": t.findtext("transactionDate/value"), "who": who.title(), "role": role,
                           "type": "Buy" if code == "P" else "Sell", "shares": int(sh), "price": round(px, 2)})
    trades.sort(key=lambda t: t["date"] or "", reverse=True)
    return {"days": days, "buys_usd": round(buys), "sells_usd": round(sells), "net_usd": round(buys - sells),
            "buyers": len(buyers), "sellers": len(sellers), "trades": trades[:10], "form4_filings": len(forms), "failed": failed}


def extras(conn, ident: dict, f: dict) -> dict:
    cik = ident.get("cik")
    if not cik:
        row = conn.execute("SELECT cik FROM us_symbols WHERE ticker=?", (ident.get("nse_symbol"),)).fetchone()
        cik = row["cik"] if row else None
    out = {"owners_us": {k: f.get(k) for k in ("insiders_pct", "institutions_pct", "short_pct_float", "short_ratio_days")}}
    if not cik:
        return out
    key = f"sec:{cik}"
    cached = cache_get(conn, key, max_age_days=0)
    if cached is None:
        try:
            sub = submissions(cik)
            cached = {"announcements": announcements(cik, sub), "insiders": insider_trades(cik, sub)}
            if not (cached["insiders"] or {}).get("failed"):   # a partial read is shown but not kept
                cache_put(conn, key, cached)
        except Exception as e:
            log.warning("SEC extras for %s failed: %s", cik, e)
            cached = {}
    out.update(cached)
    return out
