"""Fundamentals for every company worth screening, plus live price/volume metrics, for the Screener.

Universe = companies averaging at least ₹10 lakh traded a day over the last 20 sessions (~3,200).
Fundamentals come from Yahoo (same source and formulas as the fact sheets) and are refreshed in a
rolling way: each night the oldest ones are re-fetched, so none is more than a week old.
Price, volume and delivery metrics are computed from our own NSE/BSE files every time a screen runs.
"""
from __future__ import annotations

import concurrent.futures as cf
import logging
import threading
import time
from datetime import date

import numpy as np
import pandas as pd

from . import fundamentals
from .store import cache_put, connect, ensure_schema

log = logging.getLogger(__name__)
MIN_TURNOVER = 1e6  # ₹10 lakh a day

SCHEMA = """
CREATE TABLE IF NOT EXISTS universe (
    isin TEXT PRIMARY KEY, symbol TEXT, bse_code TEXT, yahoo TEXT, name TEXT, sector TEXT, industry TEXT,
    shares_cr REAL, ttm_profit REAL, ttm_sales REAL, equity REAL,
    roe REAL, roe_3y REAL, roce REAL, roa REAL, de REAL, sales_cagr_3y REAL, profit_cagr_3y REAL,
    cfo_conv REAL, fcf REAL, fcf_neg_years INTEGER, asset_turnover REAL,
    updated TEXT, error TEXT
);
"""

# key -> (label, group, unit, help). The order here is the order in the Screener's picker.
METRICS = {
    "price": ("Price", "Price & volume", "₹", "Last close"),
    "change_1d": ("Change today", "Price & volume", "%", "Price change on the last day"),
    "ret_1w": ("1-week return", "Price & volume", "%", ""),
    "ret_1m": ("1-month return", "Price & volume", "%", ""),
    "ret_1y": ("1-year return", "Price & volume", "%", ""),
    "from_high": ("From 52-week high", "Price & volume", "%", "0 = at the high, -20 = 20% below"),
    "vs_200dma": ("Vs 200-day average", "Price & volume", "%", "Above the 200-day average is positive"),
    "spike_x": ("Volume spike", "Price & volume", "×", "1-week average volume ÷ 1-year average"),
    "volume": ("Volume today", "Price & volume", "shares", "NSE + BSE"),
    "turnover_cr": ("Traded today", "Price & volume", "₹ Cr", ""),
    "delivery": ("Delivery today", "Price & volume", "%", "Share of volume actually delivered"),
    "delivery_30d": ("Delivery, 30-day average", "Price & volume", "%", ""),
    "mcap": ("Market cap", "Size & value", "₹ Cr", ""),
    "pe": ("P/E", "Size & value", "", "Market cap ÷ last 12 months' profit"),
    "pb": ("P/B", "Size & value", "", "Market cap ÷ book value"),
    "peg": ("PEG", "Size & value", "", "P/E ÷ 3-year profit growth"),
    "roe": ("ROE", "Quality", "%", "Last year, on average equity"),
    "roe_3y": ("ROE, 3-year average", "Quality", "%", ""),
    "roce": ("ROCE", "Quality", "%", ""),
    "roa": ("ROA", "Quality", "%", ""),
    "de": ("Debt / equity", "Quality", "", "Latest balance sheet"),
    "sales_cagr_3y": ("Sales growth, 3 yr", "Growth", "%", "Yearly average"),
    "profit_cagr_3y": ("Profit growth, 3 yr", "Growth", "%", "Yearly average"),
    "cfo_conv": ("Profit turning into cash", "Cash", "%", "Operating cash ÷ (profit + depreciation)"),
    "fcf": ("Free cash flow", "Cash", "₹ Cr", "Last year"),
    "fcf_neg_years": ("Years of negative free cash", "Cash", "of 3", ""),
}

PRESETS = [
    {"id": "volume-shock", "name": "Volume shock", "desc": "Dad's screen",
     "rules": [["spike_x", ">", 6], ["volume", ">", 10000], ["mcap", ">", 500], ["pe", "<", 40], ["roe", ">", 12], ["de", "<", 1]]},
    {"id": "quality", "name": "Quality at a fair price", "desc": "High returns, low debt, not expensive",
     "rules": [["roe", ">", 15], ["roce", ">", 15], ["de", "<", 0.5], ["pe", "<", 30], ["mcap", ">", 500]]},
    {"id": "growers", "name": "Fast growers", "desc": "Sales and profit up 20%+ a year",
     "rules": [["sales_cagr_3y", ">", 20], ["profit_cagr_3y", ">", 20], ["roe", ">", 12], ["mcap", ">", 500]]},
    {"id": "breakout", "name": "Near the high, with volume", "desc": "Within 5% of 52-week high on 2× volume",
     "rules": [["from_high", ">", -5], ["spike_x", ">", 2], ["delivery", ">", 40], ["mcap", ">", 500]]},
    {"id": "cash", "name": "Cash machines", "desc": "Profit that turns into cash",
     "rules": [["cfo_conv", ">", 90], ["fcf_neg_years", "<", 1], ["de", "<", 0.5], ["roe", ">", 15]]},
]

_db_lock = threading.Lock()


def db():
    conn = connect()
    ensure_schema(conn, "universe", SCHEMA)
    return conn


def candidates(conn) -> pd.DataFrame:
    dates = [r[0] for r in conn.execute(
        "SELECT DISTINCT date FROM daily WHERE exch='NSE' ORDER BY date DESC LIMIT 20")]
    q = f"""SELECT isin, SUM(turnover)/{len(dates)}.0 AS avg_t,
              MAX(CASE WHEN exch='NSE' THEN symbol END) AS nse, MAX(CASE WHEN exch='BSE' THEN code END) AS bse_code,
              MAX(CASE WHEN exch='BSE' THEN symbol END) AS bse, MAX(CASE WHEN exch='BSE' THEN name END) AS bse_name,
              MAX(CASE WHEN exch='NSE' THEN name END) AS nse_name
            FROM daily WHERE date IN ({",".join("?" * len(dates))}) GROUP BY isin HAVING avg_t >= ?"""
    df = pd.read_sql_query(q, conn, params=(*dates, MIN_TURNOVER))
    df["yahoo"] = np.where(df["nse"].notna(), df["nse"] + ".NS", df["bse"] + ".BO")
    df["symbol"] = df["nse"].fillna(df["bse"])
    df["name"] = df["bse_name"].fillna(df["nse_name"])
    return df


def _row(c: pd.Series, f: dict | None, err: str | None) -> tuple:
    a0 = (f or {}).get("annual", [{}])[0] if (f or {}).get("annual") else {}
    neg = sum(1 for a in ((f or {}).get("annual") or [])[:3] if (a.get("free_cash_flow") or 0) < 0)
    eq = (f["market_cap_cr"] / f["pb"]) if f and f.get("pb") and f.get("market_cap_cr") else None
    g = (f or {}).get
    return (c["isin"], c["symbol"], c["bse_code"], c["yahoo"], c["name"], g("sector"), g("industry"),
            g("shares_cr"), g("ttm_net_profit"), g("ttm_sales"), eq,
            g("roe_pct"), g("roe_3y_avg_pct"), g("roce_pct"), g("roa_pct"), g("debt_to_equity"),
            g("sales_cagr_3y_pct"), g("profit_cagr_3y_pct"), a0.get("cfo_to_profit_plus_dep_pct"),
            a0.get("free_cash_flow"), neg if f else None, g("asset_turnover"), date.today().isoformat(), err)


def refresh(limit: int | None = None, max_age_days: int = 7, workers: int = 3) -> dict:
    """Fetch fundamentals for universe companies that are missing or older than max_age_days."""
    conn = db()
    cands = candidates(conn)
    have = {r["isin"]: r["updated"] for r in conn.execute("SELECT isin, updated FROM universe")}
    today = date.today()
    todo = cands[[(i not in have) or (today - date.fromisoformat(have[i])).days >= max_age_days for i in cands["isin"]]]
    # missing first, then oldest
    todo = todo.assign(_age=[have.get(i, "0000-00-00") for i in todo["isin"]]).sort_values("_age")
    if limit:
        todo = todo.head(limit)
    stats = {"universe": len(cands), "todo": len(todo), "ok": 0, "failed": 0}

    def one(c):
        for attempt in range(3):
            try:
                f = fundamentals.fetch(c["yahoo"], with_sector=True)
                return c, f, None
            except LookupError as e:
                return c, None, str(e)[:200]
            except Exception as e:  # rate limit / network: back off
                time.sleep(5 * (attempt + 1))
                last = str(e)[:200]
        return c, None, last

    with cf.ThreadPoolExecutor(max_workers=workers) as ex:
        for i, (c, f, err) in enumerate(ex.map(one, [r for _, r in todo.iterrows()])):
            with _db_lock:
                conn.execute(f"INSERT OR REPLACE INTO universe VALUES ({','.join('?' * 24)})", _row(c, f, err))
                if f:
                    cache_put(conn, f"fund:{c['yahoo']}", f)
                conn.commit()
            stats["ok" if f else "failed"] += 1
            if i and i % 100 == 0:
                log.info("universe: %d/%d", i, len(todo))
    return stats


def price_metrics(conn) -> tuple[str, pd.DataFrame]:
    dates = [r[0] for r in conn.execute("SELECT DISTINCT date FROM daily WHERE exch='NSE' ORDER BY date DESC LIMIT 252")]
    df = pd.read_sql_query(
        "SELECT date, isin, SUM(volume) vol, SUM(turnover) t, SUM(deliv_qty) dq, "
        "COALESCE(MAX(CASE WHEN exch='NSE' THEN close END), MAX(close)) close, "
        "COALESCE(MAX(CASE WHEN exch='NSE' THEN prev_close END), MAX(prev_close)) prev "
        f"FROM daily WHERE date >= ? GROUP BY date, isin", conn, params=(dates[-1],))
    today = dates[0]
    close = df.pivot(index="date", columns="isin", values="close").sort_index()
    vol = df.pivot(index="date", columns="isin", values="vol").sort_index()
    dq = df.pivot(index="date", columns="isin", values="dq").sort_index()
    last = df[df["date"] == today].set_index("isin")

    def ret(n):
        base = close.iloc[-n - 1] if len(close) > n else close.iloc[0]
        return (close.iloc[-1] / base - 1) * 100

    out = pd.DataFrame({
        "price": close.iloc[-1],
        "change_1d": (last["close"] / last["prev"] - 1) * 100,
        "ret_1w": ret(5), "ret_1m": ret(21), "ret_1y": ret(len(close) - 1),
        "from_high": (close.iloc[-1] / close.max() - 1) * 100,
        "vs_200dma": (close.iloc[-1] / close.tail(200).mean() - 1) * 100,
        "spike_x": vol.tail(5).mean() / vol.mean(),
        "volume": last["vol"], "turnover_cr": last["t"] / 1e7,
        "delivery": last["dq"] / last["vol"] * 100,
        "delivery_30d": (dq.tail(30).sum() / vol.tail(30).where(dq.tail(30).notna()).sum()) * 100,
    })
    out = out[out.index.isin(last.index)]
    return today, out


def table(conn) -> tuple[str, pd.DataFrame]:
    """Every universe company with fundamentals and today's price metrics, as one frame."""
    today, pm = price_metrics(conn)
    u = pd.read_sql_query("SELECT * FROM universe WHERE error IS NULL", conn).set_index("isin")
    df = u.join(pm, how="inner")
    df["mcap"] = df["price"] * df["shares_cr"]
    df["pe"] = np.where(df["ttm_profit"] > 0, df["mcap"] / df["ttm_profit"], np.nan)
    df["pb"] = np.where(df["equity"] > 0, df["mcap"] / df["equity"], np.nan)
    df["peg"] = np.where((df["profit_cagr_3y"] > 0) & df["pe"].notna(), df["pe"] / df["profit_cagr_3y"], np.nan)
    return today, df


def run_rules(rules: list, conn=None, limit: int = 200) -> dict:
    conn = conn or db()
    today, df = table(conn)
    total = len(df)
    mask = pd.Series(True, index=df.index)
    for key, op, val in rules:
        if key not in df.columns:
            continue
        col = df[key]
        mask &= (col > val) if op == ">" else (col < val)
        mask &= col.notna()
    hits = df[mask].copy()
    sort_key = next((k for k, _, _ in rules if k == "spike_x"), "mcap")
    hits = hits.sort_values(sort_key, ascending=False).head(limit)
    newest = conn.execute("SELECT max(updated), min(updated), count(*) FROM universe").fetchone()
    return {"day": today, "universe": total, "hits": hits.reset_index().to_dict("records"),
            "fund_updated": newest[0], "fund_oldest": newest[1], "fund_count": newest[2]}
