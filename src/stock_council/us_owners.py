"""US ownership changes for the Screener, from the SEC's free bulk data (the US side of India's
"promoter change" and "FII + DII change").

Insiders: open-market buys (code P) and sales (code S) on Form 4, from the SEC's quarterly insider data sets
plus each day's new filings (daily index), so the last 3 months are always covered. Grants, option exercises
and shares withheld for tax are left out: they say nothing about conviction.
  insiders_chg = (shares bought - shares sold, last 91 days) / shares outstanding, in percentage points.

Institutions: Form 13F holdings, quarterly data sets. Adding up every filer double counts (sub-advisers,
combination reports), so we only compare funds that filed a holdings report for BOTH quarters and take the
change in their total shares.
  institutions_chg = (their shares at the latest quarter end - at the one before) / shares outstanding, pts.
13F reports arrive up to 45 days after the quarter and the SEC posts its data set later still, so this is
the latest quarter available (the page says which one).

CUSIP (13F) -> ticker comes from OpenFIGI (free, no key, 25 requests a minute), cached for good.
"""
from __future__ import annotations

import csv
import io
import json
import logging
import re
import time
import xml.etree.ElementTree as ET
import zipfile
from datetime import date, datetime, timedelta
from pathlib import Path

import httpx

from .config import DATA_DIR
from .store import ensure_schema

log = logging.getLogger(__name__)
SEC_UA = "Stock Council screen@parthchandak.info"
SEC = "https://www.sec.gov"

SCHEMA = """
CREATE TABLE IF NOT EXISTS insider_tx (
    acc TEXT NOT NULL, sk TEXT NOT NULL, cik TEXT NOT NULL, day TEXT, code TEXT, shares REAL, price REAL,
    PRIMARY KEY (acc, sk));
CREATE INDEX IF NOT EXISTS insider_tx_cik ON insider_tx(cik, day);
CREATE TABLE IF NOT EXISTS insider_filers (cik TEXT PRIMARY KEY, last TEXT);
CREATE TABLE IF NOT EXISTS owners_loaded (what TEXT PRIMARY KEY, at TEXT, note TEXT);
CREATE TABLE IF NOT EXISTS cusip_map (cusip TEXT PRIMARY KEY, ticker TEXT, at TEXT);
CREATE TABLE IF NOT EXISTS inst_change (
    ticker TEXT PRIMARY KEY, period TEXT, prev_period TEXT, shares REAL, prev_shares REAL, filers INTEGER);
"""


def _db(conn):
    ensure_schema(conn, "us_owners", SCHEMA)
    return conn


def _get(url: str, timeout: int = 120) -> httpx.Response:
    from .us_data import sec_wait
    sec_wait()
    r = httpx.get(url, headers={"User-Agent": SEC_UA}, timeout=timeout, follow_redirects=True)
    r.raise_for_status()
    return r


def _loaded(conn, what: str) -> bool:
    return conn.execute("SELECT 1 FROM owners_loaded WHERE what=?", (what,)).fetchone() is not None


def _mark(conn, what: str, note: str = "") -> None:
    conn.execute("INSERT OR REPLACE INTO owners_loaded VALUES (?,?,?)", (what, datetime.now().isoformat(timespec="seconds"), note))
    conn.commit()


def _our_ciks(conn) -> dict[str, str]:
    """Our companies' CIKs (no leading zeros) -> ticker."""
    return {str(int(r[1])): r[0] for r in conn.execute("SELECT isin, bse_code FROM universe WHERE bse_code GLOB '[0-9]*'")}


def _links(page: str, pat: str) -> list[str]:
    html = _get(f"{SEC}/data-research/sec-markets-data/{page}", 60).text
    return list(dict.fromkeys(re.findall(r'href="([^"]*' + pat + r'[^"]*\.zip)"', html)))


def _tsv(z: zipfile.ZipFile, name: str):
    return csv.DictReader(io.TextIOWrapper(z.open(name), encoding="utf-8", errors="replace"), delimiter="\t",
                          quoting=csv.QUOTE_NONE)


def _download(url: str) -> Path:
    path = DATA_DIR / ("tmp-" + url.rsplit("/", 1)[-1])
    from .us_data import sec_wait
    sec_wait()
    with httpx.stream("GET", url if url.startswith("http") else SEC + url, headers={"User-Agent": SEC_UA},
                      timeout=600, follow_redirects=True) as r:
        r.raise_for_status()
        with open(path, "wb") as f:
            for chunk in r.iter_bytes(1 << 20):
                f.write(chunk)
    return path


# ---------------------------------------------------------------- insiders

def _d(s: str) -> str | None:
    try:
        return datetime.strptime(s.strip(), "%d-%b-%Y").date().isoformat()
    except ValueError:
        return None


def load_insider_quarter(conn, url: str) -> int:
    ciks = _our_ciks(conn)
    path = _download(url)
    n = 0
    try:
        z = zipfile.ZipFile(path)
        sub = {r["ACCESSION_NUMBER"]: str(int(r["ISSUERCIK"])) for r in _tsv(z, "SUBMISSION.tsv")
               if r.get("DOCUMENT_TYPE") in ("4", "4/A") and (r.get("ISSUERCIK") or "").isdigit()
               and str(int(r["ISSUERCIK"])) in ciks}
        # every company whose insiders file Form 4 at all (foreign issuers don't): only these can read "0"
        conn.executemany("INSERT OR REPLACE INTO insider_filers VALUES (?,?)", [(c, url) for c in set(sub.values())])
        rows = []
        for r in _tsv(z, "NONDERIV_TRANS.tsv"):
            cik = sub.get(r["ACCESSION_NUMBER"])
            if not cik or r.get("TRANS_CODE") not in ("P", "S"):
                continue
            try:
                sh, px = float(r.get("TRANS_SHARES") or 0), float(r.get("TRANS_PRICEPERSHARE") or 0)
            except ValueError:
                continue
            rows.append((r["ACCESSION_NUMBER"], r["NONDERIV_TRANS_SK"], cik, _d(r.get("TRANS_DATE") or ""), r["TRANS_CODE"], sh, px))
        conn.executemany("INSERT OR REPLACE INTO insider_tx VALUES (?,?,?,?,?,?,?)", rows)
        conn.commit()
        n = len(rows)
    finally:
        path.unlink(missing_ok=True)
    return n


def _parse_form4(text: str) -> tuple[str | None, list[tuple]]:
    m = re.search(r"<XML>\s*(.*?)\s*</XML>", text, re.S | re.I)
    if not m:
        return None, []
    x = ET.fromstring(m.group(1).strip().encode())
    cik = (x.findtext(".//issuer/issuerCik") or "").strip()
    out = []
    for i, t in enumerate(x.findall(".//nonDerivativeTable/nonDerivativeTransaction")):
        code = t.findtext("transactionCoding/transactionCode")
        if code not in ("P", "S"):
            continue
        try:
            sh = float(t.findtext("transactionAmounts/transactionShares/value") or 0)
            px = float(t.findtext("transactionAmounts/transactionPricePerShare/value") or 0)
        except ValueError:
            continue
        out.append((str(i), (t.findtext("transactionDate/value") or "")[:10], code, sh, px))
    return (str(int(cik)) if cik.isdigit() else None), out


def load_insider_day(conn, day: date, ciks: dict[str, str]) -> int | None:
    """One day's Form 4s from the EDGAR daily index. None when there is no index (weekend, holiday, not yet)."""
    q = (day.month - 1) // 3 + 1
    try:
        idx = _get(f"{SEC}/Archives/edgar/daily-index/{day.year}/QTR{q}/form.{day:%Y%m%d}.idx", 60).text
    except httpx.HTTPStatusError:
        return None
    files: dict[str, bool] = {}
    for line in idx.splitlines():
        if not (line.startswith("4 ") or line.startswith("4/A ")):
            continue
        p = line.split()
        if len(p) < 4:
            continue
        files[p[-1]] = files.get(p[-1], False) or p[-3] in ciks     # listed under the issuer and each owner
    n = 0
    for path, ours in files.items():
        if not ours:
            continue
        acc = path.rsplit("/", 1)[-1].removesuffix(".txt")
        try:
            cik, tx = _parse_form4(_get(f"{SEC}/Archives/{path}", 60).text)
        except Exception as e:
            log.debug("form 4 %s: %s", path, e)
            continue
        if cik not in ciks:
            continue
        conn.execute("INSERT OR REPLACE INTO insider_filers VALUES (?,?)", (cik, day.isoformat()))
        if tx:      # one short write per filing: the website shares this database and must never wait on us
            conn.executemany("INSERT OR REPLACE INTO insider_tx VALUES (?,?,?,?,?,?,?)",
                             [(acc, sk, cik, d, c, sh, px) for sk, d, c, sh, px in tx])
            conn.commit()
        n += len(tx)
    conn.commit()
    return n


def sync_insiders(conn, max_days: int = 100) -> dict:
    """The last two quarterly data sets, then every day since the newest one ends."""
    _db(conn)
    out = {"quarters": [], "days": 0, "trades": 0}
    links = [u for u in _links("insider-transactions-data-sets", "form345")][:2]
    newest_end = None
    for u in links:
        m = re.search(r"(\d{4})q(\d)", u)
        y, qn = int(m.group(1)), int(m.group(2))
        end = (date(y + (qn == 4), 1 if qn == 4 else qn * 3 + 1, 1) - timedelta(days=1))
        newest_end = max(newest_end or end, end)
        key = f"form345:{y}q{qn}"
        if not _loaded(conn, key):
            out["trades"] += load_insider_quarter(conn, u)
            _mark(conn, key)
            out["quarters"].append(key)
    ciks = _our_ciks(conn)
    start = max((newest_end or date.today() - timedelta(days=max_days)) + timedelta(days=1), date.today() - timedelta(days=max_days))
    d = start
    while d <= date.today():
        key = f"form4:{d.isoformat()}"
        if d.weekday() < 5 and (not _loaded(conn, key) or d >= date.today() - timedelta(days=1)):
            n = load_insider_day(conn, d, ciks)
            if n is not None:
                out["trades"] += n
                out["days"] += 1
                if d < date.today():
                    _mark(conn, key)
        d += timedelta(days=1)
    return out


# ---------------------------------------------------------------- institutions (13F)

def _period(s: str) -> str:
    try:
        return datetime.strptime(s.strip(), "%d-%b-%Y").date().isoformat()
    except ValueError:
        return ""


def _figi(cusips: list[str]) -> dict[str, str | None]:
    """CUSIP -> US ticker via OpenFIGI (10 per request, 25 requests a minute without a key)."""
    out = {}
    for i in range(0, len(cusips), 10):
        part = cusips[i:i + 10]
        for attempt in range(4):
            r = httpx.post("https://api.openfigi.com/v3/mapping", timeout=60,
                           json=[{"idType": "ID_CUSIP", "idValue": c, "exchCode": "US"} for c in part])
            if r.status_code == 429:
                time.sleep(15 * (attempt + 1))
                continue
            r.raise_for_status()
            for c, res in zip(part, r.json()):
                data = res.get("data") or []
                eq = [d for d in data if d.get("marketSector") == "Equity"] or data
                out[c] = eq[0].get("ticker") if eq else None
            break
        time.sleep(2.5)
    return out


def sync_institutions(conn) -> dict:
    """Latest quarter vs the one before, from the two newest 13F data sets (re-done when a new one appears)."""
    _db(conn)
    links = _links("form-13f-data-sets", "form13f")[:2]
    key = "13f:" + "|".join(u.rsplit("/", 1)[-1] for u in links)
    if _loaded(conn, key):
        return {"skipped": "already have " + key}
    # pass 1: every holdings report (not amendments) -> (filer, quarter end)
    acc_info: dict[str, tuple[str, str]] = {}
    paths = []
    try:
        for u in links:
            p = _download(u)
            paths.append(p)
            z = zipfile.ZipFile(p)
            cover = {r["ACCESSION_NUMBER"]: (r.get("ISAMENDMENT") or "", r.get("REPORTTYPE") or "") for r in _tsv(z, "COVERPAGE.tsv")}
            for r in _tsv(z, "SUBMISSION.tsv"):
                a = r["ACCESSION_NUMBER"]
                amend, rtype = cover.get(a, ("", ""))
                if r.get("SUBMISSIONTYPE") == "13F-HR" and amend != "Y" and "HOLDINGS" in rtype.upper():
                    acc_info[a] = (r["CIK"], _period(r.get("PERIODOFREPORT") or ""))
        counts: dict[str, int] = {}
        for _, per in acc_info.values():
            counts[per] = counts.get(per, 0) + 1
        top = sorted((p for p in counts if p), key=lambda p: -counts[p])[:2]
        if len(top) < 2:
            return {"error": "need two quarters", "counts": counts}
        cur, prev = sorted(top, reverse=True)
        filers = {per: {f for f, p in acc_info.values() if p == per} for per in (cur, prev)}
        both = filers[cur] & filers[prev]
        keep = {a: p for a, (f, p) in acc_info.items() if f in both and p in (cur, prev)}
        # pass 2: which CUSIPs are held in both quarters (cheap counts), mapped to tickers once (cached)
        common: dict[str, int] = {}
        for p in paths:
            for r in _tsv(zipfile.ZipFile(p), "INFOTABLE.tsv"):
                if r["ACCESSION_NUMBER"] in keep and r.get("SSHPRNAMTTYPE") == "SH" and not (r.get("PUTCALL") or "").strip():
                    c = (r.get("CUSIP") or "").upper()
                    if len(c) == 9:
                        common[c] = common.get(c, 0) + 1
        have = {r[0]: r[1] for r in conn.execute("SELECT cusip, ticker FROM cusip_map")}
        todo = [c for c, n in common.items() if n >= 6 and c not in have]
        if todo:
            got = _figi(todo)
            conn.executemany("INSERT OR REPLACE INTO cusip_map VALUES (?,?,?)",
                             [(c, t, date.today().isoformat()) for c, t in got.items()])
            conn.commit()
            have.update(got)
        ours = {r[0] for r in conn.execute("SELECT isin FROM universe")}
        tick = {c: t for c, t in have.items() if t and t in ours}
        # pass 3: each fund's position in each of our stocks, both quarters. Funds' filings contain errors
        # (one pension fund reported 7 billion Nvidia shares in June 2026 vs 37 million in March), and some
        # list a stock under a different CUSIP one quarter, so: count only positions held in BOTH quarters
        # and drop any that moved more than 5x either way.
        per = {cur: 0, prev: 1}
        pos: dict[tuple, list] = {}
        for p in paths:
            for r in _tsv(zipfile.ZipFile(p), "INFOTABLE.tsv"):
                q = keep.get(r["ACCESSION_NUMBER"])
                t = tick.get((r.get("CUSIP") or "").upper())
                if q is None or not t or r.get("SSHPRNAMTTYPE") != "SH" or (r.get("PUTCALL") or "").strip():
                    continue
                try:
                    v = float(r.get("SSHPRNAMT") or 0)
                except ValueError:
                    continue
                k = (acc_info[r["ACCESSION_NUMBER"]][0], t)
                x = pos.get(k)
                if x is None:
                    x = pos[k] = [0.0, 0.0]
                x[per[q]] += v
    finally:
        for p in paths:
            p.unlink(missing_ok=True)
    by_t: dict[str, list] = {}
    for (f, t), (c, pv) in pos.items():
        if c > 0 and pv > 0 and 0.2 < c / pv < 5:
            a = by_t.setdefault(t, [0.0, 0.0, 0])
            a[0] += c; a[1] += pv; a[2] += 1
    conn.execute("DELETE FROM inst_change")
    conn.executemany("INSERT INTO inst_change VALUES (?,?,?,?,?,?)",
                     [(t, cur, prev, a[0], a[1], a[2]) for t, a in by_t.items()])
    conn.commit()
    _mark(conn, key, json.dumps({"cur": cur, "prev": prev, "filers": len(both), "tickers": len(by_t)}))
    return {"quarter": cur, "vs": prev, "funds_in_both": len(both), "tickers": len(by_t), "figi_lookups": len(todo)}


def sync(conn) -> dict:
    out = {}
    for name, fn in (("insiders", sync_insiders), ("institutions", sync_institutions)):
        try:
            out[name] = fn(conn)
        except Exception as e:
            log.exception("%s: %s", name, e)
            out[name] = {"error": str(e)[:200]}
    return out


# ---------------------------------------------------------------- for the Screener and fact sheet

def changes(conn):
    """Per ticker: insiders_chg, insiders_net (USD M, 91 days), institutions_chg (pts), inst_period."""
    import pandas as pd
    _db(conn)
    since = (date.today() - timedelta(days=91)).isoformat()
    u = pd.read_sql_query("SELECT isin, bse_code, shares_cr FROM universe WHERE bse_code GLOB '[0-9]*'", conn)
    u["cik"] = u["bse_code"].map(lambda c: str(int(c)))
    ins = pd.read_sql_query(
        "SELECT cik, SUM(CASE WHEN code='P' THEN shares ELSE -shares END) net_sh, "
        "SUM(CASE WHEN code='P' THEN shares*price ELSE -shares*price END) net_usd FROM insider_tx "
        "WHERE day >= ? GROUP BY cik", conn, params=(since,))
    inst = pd.read_sql_query("SELECT ticker isin, shares, prev_shares, period FROM inst_change", conn)
    filers = {r[0] for r in conn.execute("SELECT cik FROM insider_filers")}
    df = u.merge(ins, on="cik", how="left").merge(inst, on="isin", how="left").set_index("isin")
    files4 = df["cik"].isin(filers) | df["net_sh"].notna()
    for k in ("shares_cr", "net_sh", "net_usd", "shares", "prev_shares"):   # empty tables come back untyped
        df[k] = pd.to_numeric(df[k], errors="coerce").astype(float)
    sh = (df["shares_cr"] * 1e6).where(df["shares_cr"] > 0)
    covered = _loaded_any(conn, "form4:")
    out = pd.DataFrame(index=df.index)
    # a company with no insider trades in the window had a change of 0, once the window is covered
    fill = covered & files4          # files Form 4 but traded nothing in the window -> 0; never files -> blank
    out["insiders_chg"] = df["net_sh"].where(~fill, df["net_sh"].fillna(0)) / sh * 100
    out["insiders_net"] = df["net_usd"].where(~fill, df["net_usd"].fillna(0)) / 1e6
    out["institutions_chg"] = (df["shares"] - df["prev_shares"]) / sh * 100
    out["inst_period"] = df["period"]
    out = out.replace([float("inf"), float("-inf")], float("nan"))
    # beyond these the share count is wrong (reverse splits, foreign share classes): blank, not nonsense
    out["insiders_chg"] = out["insiders_chg"].where(out["insiders_chg"].abs() <= 25)
    out["institutions_chg"] = out["institutions_chg"].where(out["institutions_chg"].abs() <= 40)
    return out


def _loaded_any(conn, prefix: str) -> bool:
    return conn.execute("SELECT 1 FROM owners_loaded WHERE what LIKE ?", (prefix + "%",)).fetchone() is not None
