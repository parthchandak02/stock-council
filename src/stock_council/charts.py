"""Data for the interactive charts: daily OHLC + volume (+ delivery in India), moving averages,
volume-spike days, the market index for comparison, and longer history from Yahoo.

Up to a year comes from our own exchange files (split-adjusted, NSE+BSE volume combined, delivery %).
3Y / 5Y / Max come from Yahoo (already split-adjusted), cached for the day.
"""
from __future__ import annotations

import math

import pandas as pd

from .config import IS_US, MARKET, PRIMARY_EXCH as PX
from .store import cache_get, cache_put

INDEX = ("^GSPC", "S&P 500") if IS_US else ("^NSEI", "Nifty 50")
LONG = {"3y": "3y", "5y": "5y", "max": "max"}


def _r(x, n=2):
    return None if x is None or (isinstance(x, float) and math.isnan(x)) else round(float(x), n)


def _pack(df: pd.DataFrame, call_day: str | None = None) -> dict:
    """bars + moving averages + spike days, from a frame with date, open, high, low, close, vol [, dq]."""
    df = df.dropna(subset=["close"]).reset_index(drop=True)
    if df.empty:
        return {"bars": []}
    for c in ("open", "high", "low"):          # some rows only have a close
        df[c] = df[c].fillna(df["close"])
    ma50, ma200 = df["close"].rolling(50).mean(), df["close"].rolling(200).mean()
    vavg = df["vol"].rolling(50, min_periods=10).mean().shift(1)
    bars = []
    for i, r in df.iterrows():
        b = {"t": r["date"], "o": _r(r["open"]), "h": _r(r["high"]), "l": _r(r["low"]), "c": _r(r["close"]),
             "v": int(r["vol"]) if r["vol"] == r["vol"] else 0}
        if "dq" in df and r.get("dq") == r.get("dq") and r.get("dq") and r["vol"]:
            b["d"] = round(r["dq"] / r["vol"] * 100, 1)        # delivery %
        if vavg[i] == vavg[i] and vavg[i]:
            b["x"] = round(r["vol"] / vavg[i], 1)              # volume vs its 50-day average
        bars.append(b)
    line = lambda s: [{"t": d, "v": _r(v)} for d, v in zip(df["date"], s) if v == v]
    return {"bars": bars, "ma50": line(ma50), "ma200": line(ma200),
            "spikes": [b["t"] for b in bars if b.get("x", 0) >= 4], "call": call_day}


def ours(conn, isin: str, call_day: str | None = None) -> dict:
    """Up to a year from our own files: split-adjusted, volume from every exchange, delivery %."""
    from .actions import adjust, aliases
    ids = aliases(conn, isin)
    q = ",".join("?" * len(ids))
    df = pd.read_sql_query(
        f"SELECT date, '{isin}' isin, "
        f"COALESCE(MAX(CASE WHEN exch='{PX}' THEN open END), MAX(open)) open, "
        f"COALESCE(MAX(CASE WHEN exch='{PX}' THEN high END), MAX(high)) high, "
        f"COALESCE(MAX(CASE WHEN exch='{PX}' THEN low END), MAX(low)) low, "
        f"COALESCE(MAX(CASE WHEN exch='{PX}' THEN close END), MAX(close)) close, "
        f"SUM(volume) vol, SUM(deliv_qty) dq FROM daily WHERE isin IN ({q}) GROUP BY date ORDER BY date",
        conn, params=ids)
    for c in ("open", "high", "low", "close", "vol", "dq"):
        df[c] = pd.to_numeric(df[c], errors="coerce").astype(float)
    if len(df) >= 2:
        df = adjust(df, conn, price_cols=("open", "high", "low", "close"), volume_cols=("vol", "dq"))
    out = _pack(df, call_day)
    out["source"] = f"{MARKET['exchanges']} closing prices, split-adjusted"
    return out


def yahoo(conn, symbol: str, span: str) -> dict:
    """3Y / 5Y / Max from Yahoo (daily up to 5 years, weekly beyond), cached for the day."""
    key = f"chart:{symbol}:{span}"
    hit = cache_get(conn, key, max_age_days=1)
    if hit is not None:
        return hit
    import yfinance as yf
    interval = "1wk" if span == "max" else "1d"
    try:
        h = yf.Ticker(symbol).history(period=LONG[span], interval=interval, auto_adjust=False)
    except Exception:
        h = pd.DataFrame()
    if h is None or h.empty:
        return {"bars": [], "source": "Yahoo Finance (no data)"}
    df = pd.DataFrame({"date": [d.date().isoformat() for d in h.index], "open": h["Open"].values, "high": h["High"].values,
                       "low": h["Low"].values, "close": h["Close"].values, "vol": h["Volume"].values.astype(float)})
    out = _pack(df)
    out["source"] = "Yahoo Finance" + (", weekly" if interval == "1wk" else "")
    cache_put(conn, key, out)
    return out


def index(conn, span: str = "1y") -> dict:
    """The market index closes for the comparison line."""
    period = LONG.get(span, "1y")
    key = f"chart:index:{INDEX[0]}:{period}"
    hit = cache_get(conn, key, max_age_days=1)
    if hit is not None:
        return hit
    import yfinance as yf
    try:
        h = yf.Ticker(INDEX[0]).history(period=period, interval="1wk" if span == "max" else "1d")
        pts = [{"t": d.date().isoformat(), "v": _r(v)} for d, v in zip(h.index, h["Close"]) if v == v]
    except Exception:
        pts = []
    out = {"name": INDEX[1], "points": pts}
    if pts:
        cache_put(conn, key, out)
    return out
