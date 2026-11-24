"""Official NSE filings: which companies just published results, and every company announcement.

Results: the newest-first list of results filings tells us whose numbers changed, so their
fundamentals are refreshed the same evening instead of waiting for the weekly cycle.
Announcements (orders, acquisitions, board meetings, credit ratings, ...): one call per day for
all companies; each fact sheet shows, and the AIs read, the last 30 days for that company.
"""
from __future__ import annotations

import logging
import time
from datetime import date, datetime, timedelta

from .nse_api import NSE
from .store import connect, ensure_schema

log = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS announcements (
    symbol TEXT NOT NULL, at TEXT NOT NULL, subject TEXT, text TEXT, url TEXT,
    PRIMARY KEY (symbol, at, subject)
);
CREATE INDEX IF NOT EXISTS ann_sym ON announcements(symbol, at);
"""


def db():
    conn = connect()
    ensure_schema(conn, "announcements", SCHEMA)
    return conn


def recent_results(days: int = 3, nse: NSE | None = None, max_pages: int = 40) -> set[str]:
    """NSE symbols that filed results in the last `days` days."""
    nse = nse or NSE()
    cutoff = datetime.now() - timedelta(days=days)
    out: set[str] = set()
    for page in range(1, max_pages + 1):
        d = nse._get_json("integrated-filing-results?index=equities&period_ended=all&"
                          f"type=Integrated%20Filing-%20Financials&page={page}&size=50")
        rows = d.get("data") or []
        if not rows:
            break
        oldest = None
        for r in rows:
            try:
                at = datetime.strptime(r.get("broadcast_Date") or "", "%d-%b-%Y %H:%M:%S")
            except ValueError:
                continue
            oldest = at
            if at >= cutoff and r.get("symbol"):
                out.add(r["symbol"])
        if oldest and oldest < cutoff:
            break
        time.sleep(1.2)
    return out


def refresh_results(days: int = 3) -> dict:
    """Re-fetch fundamentals for companies whose results just came out."""
    from .universe import db as udb, refresh
    syms = recent_results(days)
    conn = udb()
    isins = [r[0] for r in conn.execute(
        f"SELECT isin FROM universe WHERE symbol IN ({','.join('?' * len(syms))})", tuple(syms))] if syms else []
    stats = refresh(force=isins, limit=len(isins) or None, max_age_days=10_000) if isins else {"todo": 0}
    return {"filed": len(syms), "in_universe": len(isins), **stats}


def update_announcements(days: int = 2, nse: NSE | None = None) -> int:
    nse = nse or NSE()
    to, frm = date.today(), date.today() - timedelta(days=days)
    rows = nse._get_json(f"corporate-announcements?index=equities&from_date={frm:%d-%m-%Y}&to_date={to:%d-%m-%Y}")
    conn = db()
    n = 0
    for r in rows or []:
        try:
            at = datetime.strptime(r.get("an_dt") or "", "%d-%b-%Y %H:%M:%S").isoformat(timespec="minutes")
        except ValueError:
            continue
        conn.execute("INSERT OR IGNORE INTO announcements VALUES (?,?,?,?,?)",
                     (r.get("symbol"), at, r.get("desc"), (r.get("attchmntText") or "")[:600], r.get("attchmntFile")))
        n += 1
    conn.commit()
    return n


SKIP = ("trading window", "loss of share certificate", "duplicate share", "copy of newspaper", "investor presentation - ")


def for_symbol(conn, symbol: str, days: int = 30, limit: int = 12) -> list[dict]:
    ensure_schema(conn, "announcements", SCHEMA)
    since = (date.today() - timedelta(days=days)).isoformat()
    rows = conn.execute("SELECT at, subject, text, url FROM announcements WHERE symbol=? AND at>=? ORDER BY at DESC",
                        (symbol, since)).fetchall()
    out = [dict(r) for r in rows if not any(s in f"{r['subject']} {r['text']}".lower() for s in SKIP)]
    return out[:limit]
