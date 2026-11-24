"""Stock splits and bonus issues, so history is comparable across them (as Screener does).

A 1-for-5 split makes the price fall to a fifth and the share count (and so the volume) rise five
times overnight. Unadjusted, that looks like an 80% crash on a 5× volume shock. We read every split
and bonus from NSE's corporate-actions list (one call for a whole date range) and, whenever history
is used, divide earlier prices and multiply earlier volumes by the factor.
"""
from __future__ import annotations

import logging
import re
import time
from datetime import date, datetime, timedelta

import pandas as pd

from .store import connect, ensure_schema

log = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS corp_actions (
    isin TEXT NOT NULL, symbol TEXT, ex_date TEXT NOT NULL, kind TEXT NOT NULL, factor REAL NOT NULL,
    subject TEXT, PRIMARY KEY (isin, ex_date, kind)
);
"""

SPLIT = re.compile(r"from\s+(?:rs|re)\.?\s*([\d.]+).*?to\s+(?:rs|re)\.?\s*([\d.]+)", re.I)
BONUS = re.compile(r"bonus\s+(\d+)\s*:\s*(\d+)", re.I)


def factor_of(subject: str) -> tuple[str, float] | None:
    """'Face Value Split ... From Rs 10/- To Rs 2/-' -> ('split', 5.0); 'Bonus 1:1' -> ('bonus', 2.0)."""
    s = subject or ""
    if "split" in s.lower() or "sub-division" in s.lower():
        m = SPLIT.search(s)
        if m and float(m.group(2)) > 0:
            return "split", float(m.group(1)) / float(m.group(2))
    m = BONUS.search(s)
    if m and float(m.group(2)) > 0:
        a, b = float(m.group(1)), float(m.group(2))
        return "bonus", (a + b) / b
    return None


def fetch(from_day: date, to_day: date) -> int:
    """Store NSE splits and bonuses with ex-dates in the range. Returns how many were stored."""
    from .nse_api import NSE
    nse = NSE()
    rows = nse._get_json("corporates-corporateActions?index=equities&from_date="
                         f"{from_day:%d-%m-%Y}&to_date={to_day:%d-%m-%Y}")
    conn = connect()
    ensure_schema(conn, "corp_actions", SCHEMA)
    n = 0
    for r in rows:
        fk = factor_of(r.get("subject", ""))
        if not fk or not r.get("isin") or not r.get("exDate"):
            continue
        ex = datetime.strptime(r["exDate"], "%d-%b-%Y").date().isoformat()
        conn.execute("INSERT OR REPLACE INTO corp_actions VALUES (?,?,?,?,?,?)",
                     (r["isin"], r.get("symbol"), ex, fk[0], fk[1], r["subject"]))
        n += 1
    conn.commit()
    return n


def update(days_back: int = 400) -> int:
    """Evening upkeep: the last year (first run) and the coming month (ex-dates are announced early)."""
    today = date.today()
    return fetch(today - timedelta(days=days_back), today + timedelta(days=31))


_alias_cache: dict = {}


def canonical_map(conn) -> dict[str, str]:
    """Old ISIN -> current ISIN. A split often comes with a new ISIN; the NSE symbol (or BSE code)
    stays the same, so we join the two histories through it."""
    latest = conn.execute("SELECT max(date) FROM daily").fetchone()[0]
    if _alias_cache.get("day") == latest:
        return _alias_cache["map"]
    out: dict[str, str] = {}
    for key, exch in (("symbol", "NSE"), ("code", "BSE")):
        rows = conn.execute(
            f"SELECT {key} k, isin, max(date) last FROM daily WHERE exch=? AND {key} IN "
            f"(SELECT {key} FROM daily WHERE exch=? GROUP BY {key} HAVING count(DISTINCT isin) > 1) "
            f"GROUP BY {key}, isin ORDER BY {key}, last", (exch, exch)).fetchall()
        by: dict[str, list] = {}
        for k, isin, last in rows:
            by.setdefault(k, []).append(isin)
        for isins in by.values():
            for old in isins[:-1]:
                out.setdefault(old, isins[-1])
    _alias_cache.update(day=latest, map=out)
    return out


def aliases(conn, isin: str) -> list[str]:
    """Every ISIN this company has traded under (current one included)."""
    m = canonical_map(conn)
    cur = m.get(isin, isin)
    return [cur] + [o for o, n in m.items() if n == cur]


def load(conn) -> pd.DataFrame:
    ensure_schema(conn, "corp_actions", SCHEMA)
    return pd.read_sql_query("SELECT isin, ex_date, factor FROM corp_actions", conn)


def adjust(df: pd.DataFrame, conn, price_cols=("close", "prev", "open", "high", "low"),
           volume_cols=("vol", "volume", "dq", "deliv", "deliv_qty")) -> pd.DataFrame:
    """Adjust a long frame (columns date, isin, ...) in place-safe copy: rows before an ex-date get
    prices ÷ factor and volumes × factor. Rows on/after the ex-date are already in today's terms."""
    if df.empty:
        return df
    import numpy as np
    cmap = canonical_map(conn)
    df = df.copy()
    if cmap:
        s = df["isin"]
        df["isin"] = s.map(cmap).fillna(s)          # map+fillna: fast even on millions of rows
    acts = load(conn)
    if acts.empty:
        return df
    acts = acts.assign(isin=acts["isin"].map(cmap).fillna(acts["isin"]) if cmap else acts["isin"])
    present = set(acts["isin"]) & set(df["isin"].unique())
    if not present:
        return df
    mult = np.ones(len(df))
    idx = df.groupby("isin").indices
    dates = df["date"].astype(str).to_numpy()
    for isin, ex, factor in acts[acts["isin"].isin(present)][["isin", "ex_date", "factor"]].itertuples(index=False):
        rows = idx.get(isin)
        if rows is not None:
            mult[rows[dates[rows] < ex]] *= factor
    for c in price_cols:
        if c in df.columns:
            df[c] = df[c].to_numpy() / mult
    for c in volume_cols:
        if c in df.columns:
            df[c] = df[c].to_numpy() * mult
    return df
    cmap = canonical_map(conn)
    df = df.copy()
    if cmap:
        df["isin"] = df["isin"].replace(cmap)
    acts = load(conn)
    if acts.empty:
        return df
    acts = acts.assign(isin=acts["isin"].replace(cmap))
    acts = acts[acts["isin"].isin(set(df["isin"]))]
    if acts.empty:
        return df
    mult = pd.Series(1.0, index=df.index)
    idx = df.groupby("isin").indices
    dates = df["date"].astype(str).to_numpy()
    for a in acts.itertuples():
        rows = idx.get(a.isin)
        if rows is None:
            continue
        before = rows[dates[rows] < a.ex_date]
        mult.iloc[before] *= a.factor
    for c in price_cols:
        if c in df.columns:
            df[c] = df[c] / mult
    for c in volume_cols:
        if c in df.columns:
            df[c] = df[c] * mult
    return df
