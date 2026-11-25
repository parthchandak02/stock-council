"""US market data, stored in the same `daily` table shape as India's so the screen is shared.

Universe: Nasdaq Trader's official symbol directory (every NYSE / Nasdaq / NYSE American listing),
filtered to common shares (no ETFs, test issues, warrants, units, rights, preferreds, notes).
SEC's ticker file adds each company's CIK (its EDGAR id) for filings.
Prices: Yahoo Finance daily bars in polite batches (split-adjusted price and volume; no sign-up).
In the `daily` table: exch='US', isin=ticker (our id), code=CIK, series=listing exchange.
"""
from __future__ import annotations

import io
import logging
import re
import time
from datetime import date, timedelta

import httpx
import pandas as pd

from .store import connect, ensure_schema

log = logging.getLogger(__name__)

SEC_UA = "Stock Council screen@parthchandak.info"   # SEC asks every client to identify itself
_sec_lock = __import__("threading").Lock()
_sec_last = [0.0]


def sec_wait() -> None:
    """One pace for every thread: SEC allows 10 requests a second; we keep to ~7."""
    with _sec_lock:
        gap = 0.14 - (time.monotonic() - _sec_last[0])
        if gap > 0:
            time.sleep(gap)
        _sec_last[0] = time.monotonic()
NASDAQ_LISTED = "https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt"
OTHER_LISTED = "https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt"
SEC_TICKERS = "https://www.sec.gov/files/company_tickers_exchange.json"
EXCH = {"N": "NYSE", "A": "NYSE American", "P": "NYSE Arca", "Z": "Cboe BZX", "V": "IEX"}
NOT_COMMON = re.compile(
    r"\b(warrants?|units?|rights?|preferred|preference|notes? due|debentures?|subordinated|"
    r"depositary shares? (each )?represent(ing)? a? ?(1/|fractional|\d+/)|%|trust preferred|when issued|"
    r"redeemable|capital securities|senior notes|baby bond)\b", re.I)

SCHEMA = """
CREATE TABLE IF NOT EXISTS us_symbols (
    ticker TEXT PRIMARY KEY, yahoo TEXT, name TEXT, exchange TEXT, cik TEXT, updated TEXT
);
"""


def db():
    conn = connect()
    ensure_schema(conn, "us_symbols", SCHEMA)
    return conn


def _get(url: str, sec: bool = False) -> str:
    headers = {"User-Agent": SEC_UA} if sec else {"User-Agent": "Mozilla/5.0 (stock-council)"}
    for attempt in range(3):
        try:
            r = httpx.get(url, headers=headers, timeout=60, follow_redirects=True)
            if r.status_code == 200:
                return r.text
        except httpx.HTTPError:
            pass
        time.sleep(3 * (attempt + 1))
    raise RuntimeError(f"could not download {url}")


def refresh_symbols() -> int:
    """Today's list of listed common stocks, with SEC CIKs."""
    rows = []
    nl = pd.read_csv(io.StringIO(_get(NASDAQ_LISTED)), sep="|", dtype=str)
    nl = nl[~nl["Symbol"].str.startswith("File Creation", na=False)]
    # columns: Symbol|Security Name|Market Category|Test Issue|Financial Status|Round Lot Size|ETF|NextShares
    nl = nl[(nl["ETF"] == "N") & (nl["Test Issue"] == "N")]
    for sym, name in zip(nl["Symbol"], nl["Security Name"]):
        rows.append((sym, name, "Nasdaq"))
    ol = pd.read_csv(io.StringIO(_get(OTHER_LISTED)), sep="|", dtype=str)
    ol = ol[~ol["ACT Symbol"].str.startswith("File Creation", na=False)]
    # columns: ACT Symbol|Security Name|Exchange|CQS Symbol|ETF|Round Lot Size|Test Issue|NASDAQ Symbol
    ol = ol[(ol["ETF"] == "N") & (ol["Test Issue"] == "N") & ol["Exchange"].isin(["N", "A"])]
    for sym, name, ex in zip(ol["ACT Symbol"], ol["Security Name"], ol["Exchange"]):
        rows.append((sym, name, EXCH.get(ex, ex)))
    rows = [(s, n, e) for s, n, e in rows if isinstance(s, str) and s and not NOT_COMMON.search(n or "")
            and not re.search(r"[\$\^]", s)]
    import json
    sec = json.loads(_get(SEC_TICKERS, sec=True))
    f = sec["fields"]
    cik = {d[f.index("ticker")].upper(): str(d[f.index("cik")]).zfill(10) for d in sec["data"]}
    conn = db()
    today = date.today().isoformat()
    conn.execute("DELETE FROM us_symbols")
    for s, n, e in rows:
        yahoo = s.replace(".", "-").replace("/", "-")
        clean = re.sub(r"\s*[-,]?\s*(New )?(Class [A-Z] )?(Common Stock|Ordinary Shares|Common Shares|Capital Stock|"
                       r"American Depositary Shares?|Shares of Beneficial Interest|Class [A-Z])\b.*$", "", n or "", flags=re.I)
        clean = re.sub(r"\s*\(The\)$", "", clean).strip()
        conn.execute("INSERT OR REPLACE INTO us_symbols VALUES (?,?,?,?,?,?)",
                     (s, yahoo, clean or n, e, cik.get(s.upper()) or cik.get(s.replace(".", "-").upper()), today))
    conn.commit()
    return len(rows)


def _store(conn, data: pd.DataFrame, meta: dict[str, tuple], overlap: bool = False, resync: set | None = None) -> int:
    """data: yfinance multi-ticker frame (columns level 0 = ticker).
    overlap=True (evening top-up): the first day is already stored and only used as a check. Yahoo's prices
    are split-adjusted, so if that day's close no longer matches what we stored, the company split: its
    ticker goes into `resync` for a full re-download instead of mixing old and new price scales."""
    from .config import market_now
    now = market_now()
    # today's bar only once the close is in (4 pm ET + a margin for the final prints)
    cutoff = now.date() + timedelta(days=1) if (now.hour, now.minute) >= (16, 30) else now.date()
    n = 0
    tickers = data.columns.get_level_values(0).unique() if isinstance(data.columns, pd.MultiIndex) else []
    for yt in tickers:
        df = data[yt].dropna(subset=["Close"])
        if df.empty:
            continue
        sym, name, ex, cik = meta[yt]
        prev = df["Close"].shift(1)
        if overlap and len(df) > 1:
            d0 = df.index[0].date().isoformat()
            old = conn.execute("SELECT close FROM daily WHERE date=? AND exch='US' AND isin=?", (d0, sym)).fetchone()
            new = _f(df["Close"].iloc[0])
            if old and old[0] and new and abs(new / old[0] - 1) > 0.02:
                if resync is not None:
                    resync.add(yt)
                continue
            df, prev = df.iloc[1:], prev.iloc[1:]     # the check day: keep the stored row (and its prev close)
        df = df[[d.date() < cutoff for d in df.index]]   # never store an unfinished trading day
        prev = prev.loc[df.index]
        rows = [(d.date().isoformat(), "US", sym, sym, cik, ex, name,
                 _f(o), _f(h), _f(l), _f(c), _f(p), int(v or 0), (_f(c) or 0) * int(v or 0))
                for d, o, h, l, c, p, v in zip(df.index, df["Open"], df["High"], df["Low"], df["Close"], prev,
                                               df["Volume"].fillna(0))]
        if not rows:
            continue
        conn.executemany(
            "INSERT OR REPLACE INTO daily (date, exch, isin, symbol, code, series, name, open, high, low, close, "
            "prev_close, volume, turnover) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
        n += len(rows)
    conn.commit()
    return n


def _f(x):
    try:
        x = float(x)
        return None if x != x else round(x, 4)
    except (TypeError, ValueError):
        return None


def fetch_prices(period: str = "10d", chunk: int = 80, pause: float = 2.5, tickers: list[str] | None = None) -> dict:
    """Daily bars for every listed common stock. period='1y' for the first backfill."""
    import yfinance as yf
    conn = db()
    rows = conn.execute("SELECT ticker, yahoo, name, exchange, cik FROM us_symbols").fetchall()
    if not rows:
        refresh_symbols()
        rows = conn.execute("SELECT ticker, yahoo, name, exchange, cik FROM us_symbols").fetchall()
    meta = {r["yahoo"]: (r["ticker"], r["name"], r["exchange"], r["cik"]) for r in rows
            if tickers is None or r["ticker"] in tickers}
    ys = list(meta)
    stored, empty_chunks = 0, 0
    resync: set = set()
    for i in range(0, len(ys), chunk):
        part = ys[i:i + chunk]
        for attempt in range(3):
            try:
                data = yf.download(part, period=period, interval="1d", auto_adjust=False, group_by="ticker",
                                   threads=False, progress=False)
                break
            except Exception as e:
                log.warning("prices chunk %d failed (%s), retrying", i, e)
                time.sleep(20 * (attempt + 1))
        else:
            continue
        got = _store(conn, data, meta, overlap=period != "1y", resync=resync) if not data.empty else 0
        stored += got
        empty_chunks = empty_chunks + 1 if got == 0 else 0
        if empty_chunks >= 3:          # Yahoo throttling: back off hard
            log.warning("prices: 3 empty chunks in a row, pausing 3 minutes")
            time.sleep(180)
            empty_chunks = 0
        if i and i % (chunk * 10) == 0:
            log.info("prices: %d/%d tickers", i, len(ys))
        time.sleep(pause)
    if resync:   # splits: a fresh year of split-adjusted bars for just these tickers
        log.info("prices: %d tickers changed scale (split), re-downloading a year", len(resync))
        part = sorted(resync)
        for i in range(0, len(part), chunk):
            try:
                data = yf.download(part[i:i + chunk], period="1y", interval="1d", auto_adjust=False, group_by="ticker",
                                   threads=False, progress=False)
                if not data.empty:
                    syms = [meta[y][0] for y in part[i:i + chunk]]
                    conn.executemany("DELETE FROM daily WHERE exch='US' AND isin=?", [(x,) for x in syms])
                    stored += _store(conn, data, meta)
            except Exception as e:
                log.warning("split re-download failed: %s", e)
            time.sleep(pause)
    return {"tickers": len(ys), "rows": stored, "resynced": len(resync)}


def update(history_days: int = 10, **_) -> dict:
    """Evening refresh. First run (empty table) backfills a year."""
    conn = db()
    have = conn.execute("SELECT count(DISTINCT date) FROM daily WHERE exch='US'").fetchone()[0]
    if have >= 200 and not _traded_today():
        return {"skipped": "no US trading bar for today yet (holiday or before the close)"}
    if date.today().weekday() == 0 or not conn.execute("SELECT 1 FROM us_symbols LIMIT 1").fetchone():
        refresh_symbols()   # new listings / delistings, weekly
    # one extra day: the first day of the window is only a check against what is stored (see _store)
    return fetch_prices(period="1y" if have < 200 else f"{max(history_days, 5) + 1}d")


def _traded_today() -> bool:
    """Cheap check before downloading 5,800 tickers: do three big names have today's closing bar?"""
    from .config import market_now
    import yfinance as yf
    now = market_now()
    if (now.hour, now.minute) < (16, 30):
        return False
    try:
        d = yf.download(["SPY", "AAPL", "MSFT"], period="5d", interval="1d", progress=False, threads=False)
        return not d.empty and max(i.date() for i in d.index) >= now.date()
    except Exception:
        return True    # can't tell: do the normal download
