"""US: off-exchange share of volume, from FINRA's free daily short-sale volume files.

The US publishes nothing like India's delivery figures (settlement is netted and private). The closest public
signal is WHERE trading happened: FINRA's consolidated file (CNMS) gives each stock's regular-hours volume that
was traded off the exchanges (dark pools and wholesalers that fill retail orders). Divided by the stock's
total volume that is its off-exchange share. A stock's usual level mostly reflects how retail-heavy it is, so
the useful signal is the change against its own last year: a falling share means more of the trading moved
onto the exchanges. It is a stand-in, never called "delivery".

File: https://cdn.finra.org/equity/regsho/daily/CNMSshvolYYYYMMDD.txt (Date|Symbol|ShortVolume|
ShortExemptVolume|TotalVolume|Market), posted by 6 pm New York time on the trade date, history from Aug 2018.
"""
from __future__ import annotations

import logging
import time

import httpx

from .store import ensure_schema

log = logging.getLogger(__name__)

URL = "https://cdn.finra.org/equity/regsho/daily/CNMSshvol{ymd}.txt"
SCHEMA = """CREATE TABLE IF NOT EXISTS offex (
    date TEXT NOT NULL, symbol TEXT NOT NULL, off_vol REAL, short_vol REAL,
    PRIMARY KEY (date, symbol))"""


def fetch_day(conn, day: str, symbols: set[str]) -> int | None:
    """Load one day's file for our symbols. None when FINRA has no file (yet) for that day."""
    try:
        r = httpx.get(URL.format(ymd=day.replace("-", "")), timeout=60,
                      headers={"User-Agent": "Mozilla/5.0 (stock-council)"}, follow_redirects=True)
    except httpx.HTTPError as e:
        log.warning("FINRA %s: %s", day, e)
        return None
    if r.status_code != 200 or not r.text.startswith("Date|"):
        return None
    rows = []
    for line in r.text.splitlines()[1:]:
        p = line.split("|")
        if len(p) < 5 or p[1] not in symbols:
            continue
        try:
            rows.append((day, p[1], float(p[4]), float(p[2])))
        except ValueError:
            continue
    conn.executemany("INSERT OR REPLACE INTO offex VALUES (?,?,?,?)", rows)
    conn.commit()
    return len(rows)


def sync(conn, days: int = 260) -> dict:
    """Fill in every trading day of the last year we don't have yet, and re-read the last two days
    (FINRA sometimes posts a corrected file the next day)."""
    ensure_schema(conn, "offex", SCHEMA)
    dates = [r[0] for r in conn.execute("SELECT DISTINCT date FROM daily ORDER BY date DESC LIMIT ?", (days,))]
    have = {r[0] for r in conn.execute("SELECT DISTINCT date FROM offex")}
    symbols = {r[0] for r in conn.execute("SELECT DISTINCT symbol FROM daily WHERE date=?", (dates[0],))} if dates else set()
    todo = [d for i, d in enumerate(dates) if d not in have or i < 2]
    loaded, missing = 0, 0
    for d in todo:
        n = fetch_day(conn, d, symbols)
        if n is None:
            missing += 1
        else:
            loaded += 1
        time.sleep(0.2)
    return {"days": len(dates), "loaded": loaded, "not_posted": missing}


def latest_day(conn) -> str | None:
    try:
        return conn.execute("SELECT max(date) FROM offex").fetchone()[0]
    except Exception:
        return None
