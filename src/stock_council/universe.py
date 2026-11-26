"""Fundamentals for every company worth screening, plus live price/volume metrics, for the Screener.

Universe = companies averaging at least ₹10 lakh traded a day over the last 20 sessions (~3,200).
Fundamentals come from Yahoo (same source and formulas as the fact sheets) and are refreshed in a
rolling way: each night the oldest ones are re-fetched, so none is more than a week old.
Price, volume and delivery metrics are computed from our own NSE/BSE files every time a screen runs.
"""
from __future__ import annotations

import logging
import threading
import time
from datetime import date

import os

import numpy as np
import pandas as pd

from . import fundamentals
from .config import IS_US, MARKET, PRIMARY_EXCH

CUR, UNIT = MARKET["cur"], MARKET["unit_label"]
from .store import cache_put, connect, ensure_schema

log = logging.getLogger(__name__)
MIN_TURNOVER = 1e6  # ₹10 lakh (India) or $1M (US) traded a day, on average

SCHEMA = """
CREATE TABLE IF NOT EXISTS universe (
    isin TEXT PRIMARY KEY, symbol TEXT, bse_code TEXT, yahoo TEXT, name TEXT, sector TEXT, industry TEXT,
    shares_cr REAL, ttm_profit REAL, ttm_sales REAL, equity REAL,
    roe REAL, roe_3y REAL, roce REAL, roa REAL, de REAL, sales_cagr_3y REAL, profit_cagr_3y REAL,
    cfo_conv REAL, fcf REAL, fcf_neg_years INTEGER, asset_turnover REAL,
    updated TEXT, error TEXT
);
"""
NEW_COLS = ["opm REAL", "npm REAL", "interest_cover REAL", "q_sales_yoy REAL", "q_profit_yoy REAL",
            "div_yield REAL", "ev_ebitda REAL", "lender INTEGER",
            "insiders REAL", "institutions REAL", "short_float REAL", "short_days REAL", "short_chg REAL"]
LENDER_WORDS = ("bank", "credit services", "mortgage", "insurance - ", "financial conglomerate")


def is_lender(sector, industry) -> bool:
    """Banks, NBFCs, home-finance and insurers: deposits/borrowings are their raw material, so
    debt/equity, cash cycle and cash-conversion rules don't apply to them."""
    ind = (industry or "").lower()
    return (sector or "") == "Financial Services" and any(w in ind for w in LENDER_WORDS)

# key -> (label, group, unit, help). The order here is the order in the Screener's picker.
METRICS = {
    "price": ("Price", "Price & volume", CUR, "Last close"),
    "change_1d": ("Change today", "Price & volume", "%", "Price change on the last day"),
    "ret_1w": ("1-week return", "Price & volume", "%", ""),
    "ret_1m": ("1-month return", "Price & volume", "%", ""),
    "ret_1y": ("1-year return", "Price & volume", "%", ""),
    "from_high": ("From 52-week high", "Price & volume", "%", "0 = at the high, -20 = 20% below"),
    "vs_200dma": ("Vs 200-day average", "Price & volume", "%", "Above the 200-day average is positive"),
    "spike_x": ("Volume spike", "Price & volume", "×", "1-week average volume ÷ 1-year average"),
    "volume": ("Volume today", "Price & volume", "shares", "NSE + BSE"),
    "turnover_cr": ("Traded today", "Price & volume", f"{CUR} {UNIT}", ""),
    "delivery": ("Delivery today", "Price & volume", "%", "Share of volume actually delivered"),
    "delivery_30d": ("Delivery, 30-day average", "Price & volume", "%", ""),
    "mcap": ("Market cap", "Size & value", f"{CUR} {UNIT}", ""),
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
    "q_sales_yoy": ("Quarter sales vs year ago", "Growth", "%", "Latest quarter vs same quarter last year"),
    "q_profit_yoy": ("Quarter profit vs year ago", "Growth", "%", "Latest quarter vs same quarter last year"),
    "opm": ("Operating margin", "Quality", "%", "EBITDA ÷ sales, last year"),
    "npm": ("Net margin", "Quality", "%", "Profit ÷ sales, last year"),
    "interest_cover": ("Interest cover", "Quality", "×", "Operating profit ÷ interest"),
    "div_yield": ("Dividend yield", "Size & value", "%", "Last 12 months"),
    "ev_ebitda": ("EV / EBITDA", "Size & value", "×", "Enterprise value ÷ EBITDA"),
    "cfo_conv": ("Profit turning into cash", "Cash", "%", "Operating cash ÷ (profit + depreciation)"),
    "promoter": ("Promoter holding", "Owners", "%", "Latest quarter"),
    "promoter_chg": ("Promoter change, 1 yr", "Owners", "pts", "Percentage points vs a year ago"),
    "pledge": ("Promoter shares pledged", "Owners", "%", "Share of promoter holding pledged"),
    "fii": ("Foreign funds (FII)", "Owners", "%", ""),
    "dii": ("Indian funds (DII)", "Owners", "%", ""),
    "funds": ("All funds (FII + DII)", "Owners", "%", ""),
    "funds_chg": ("Funds change, 1 yr", "Owners", "pts", "FII + DII vs a year ago"),
    "fcf": ("Free cash flow", "Cash", f"{CUR} {UNIT}", "Last year"),
    "fcf_neg_years": ("Years of negative free cash", "Cash", "of 3", ""),
}

if IS_US:   # US ownership: no promoter/pledge; insiders, institutions and short interest instead
    for k in ("promoter", "promoter_chg", "pledge", "fii", "dii", "funds", "funds_chg", "delivery", "delivery_30d"):  # no delivery data in the US
        METRICS.pop(k, None)
    METRICS.update({
        "insiders": ("Insider holding", "Owners", "%", "Officers, directors, 10% owners"),
        "institutions": ("Institutional holding", "Owners", "%", "Funds and other institutions"),
        "short_float": ("Short interest", "Owners", "% float", "Shares sold short, % of float"),
        "offex": ("Off-exchange today", "Price & volume", "%", "Share of volume traded off the exchanges (FINRA); US stand-in for delivery"),
        "offex_20d": ("Off-exchange, 20-day", "Price & volume", "%", "Off-exchange share over the last 20 days"),
        "offex_chg": ("Off-exchange vs usual", "Price & volume", "pts", "20-day off-exchange share minus its 1-year median; below 0 = more trading on the exchanges"),
        "short_days": ("Days to cover", "Owners", "days", "Short position ÷ average daily volume"),
        "short_chg": ("Short interest change, 1 mo", "Owners", "%", "This month's short position vs last month's"),
        "insiders_chg": ("Insider buying, 3 mo", "Owners", "pts", "Open-market buys minus sales by officers, directors and 10% owners (Form 4), last 3 months, % of shares"),
        "insiders_net": ("Insider net buying, 3 mo", "Owners", f"{CUR} M", "Value bought minus value sold on the open market (Form 4), last 3 months"),
        "institutions_chg": ("Funds change, last quarter", "Owners", "pts", "Change in funds' holdings over the latest reported quarter (13F; funds that filed both quarters), % of shares"),
    })

PRESETS = [
    {"id": "volume-shock", "name": "Volume shock", "desc": "Big volume on solid businesses",
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

if IS_US:   # the same ideas sized for the US ($ millions), plus US-flavoured ones
    PRESETS = [
        {"id": "volume-shock", "name": "Volume shock", "desc": "Big volume on solid businesses",
         # India's six rules, sized for the US: weekly volume spikes are smaller in big US stocks, so 2.5x
         # finds about as many a day (9 on average over 20 days to 25 Nov 2026, never none) as 6x does in India.
         "rules": [["spike_x", ">", 2.5], ["turnover_cr", ">", 1], ["mcap", ">", 300], ["pe", "<", 40], ["roe", ">", 12], ["de", "<", 1]]},
        {"id": "quality", "name": "Quality at a fair price", "desc": "High returns, low debt, not expensive",
         "rules": [["roe", ">", 15], ["roce", ">", 15], ["de", "<", 0.5], ["pe", "<", 30], ["mcap", ">", 1000]]},
        {"id": "growers", "name": "Fast growers", "desc": "Sales and profit up 20%+ a year",
         "rules": [["sales_cagr_3y", ">", 20], ["profit_cagr_3y", ">", 20], ["roe", ">", 12], ["mcap", ">", 300]]},
        {"id": "breakout", "name": "Near the high, with volume", "desc": "Within 5% of 52-week high on 2× volume",
         "rules": [["from_high", ">", -5], ["spike_x", ">", 2], ["mcap", ">", 300]]},
        {"id": "cash", "name": "Cash machines", "desc": "Profit that turns into cash",
         "rules": [["cfo_conv", ">", 90], ["fcf_neg_years", "<", 1], ["de", "<", 0.5], ["roe", ">", 15]]},
        {"id": "squeeze", "name": "Short squeeze watch", "desc": "Heavily shorted, volume surging",
         "rules": [["short_float", ">", 15], ["spike_x", ">", 2], ["mcap", ">", 300]]},
    ]

_db_lock = threading.Lock()


def db():
    conn = connect()
    ensure_schema(conn, "universe", SCHEMA)
    if not getattr(db, "_migrated", False):
        for col in NEW_COLS:
            try:
                conn.execute(f"ALTER TABLE universe ADD COLUMN {col}")
            except Exception:
                pass
        conn.commit()
        db._migrated = True
    return conn


def candidates(conn) -> pd.DataFrame:
    if IS_US:
        return _candidates_us(conn)
    return _candidates_in(conn)


def _candidates_us(conn) -> pd.DataFrame:
    dates = [r[0] for r in conn.execute(
        "SELECT DISTINCT date FROM daily WHERE exch='US' ORDER BY date DESC LIMIT 20")]
    q = f"""SELECT d.isin, SUM(d.turnover)/{len(dates)}.0 AS avg_t, s.ticker AS symbol, s.cik AS bse_code,
              s.yahoo, s.name FROM daily d JOIN us_symbols s ON s.ticker = d.isin
            WHERE d.date IN ({",".join("?" * len(dates))}) GROUP BY d.isin HAVING avg_t >= ?"""
    return pd.read_sql_query(q, conn, params=(*dates, MIN_TURNOVER))


def _candidates_in(conn) -> pd.DataFrame:
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


COLS = ["isin", "symbol", "bse_code", "yahoo", "name", "sector", "industry", "shares_cr", "ttm_profit", "ttm_sales",
        "equity", "roe", "roe_3y", "roce", "roa", "de", "sales_cagr_3y", "profit_cagr_3y", "cfo_conv", "fcf",
        "fcf_neg_years", "asset_turnover", "updated", "error", "opm", "npm", "interest_cover", "q_sales_yoy",
        "q_profit_yoy", "div_yield", "ev_ebitda", "lender", "insiders", "institutions", "short_float", "short_days", "short_chg"]


def _row(c: pd.Series, f: dict | None, err: str | None) -> tuple:
    a0 = (f or {}).get("annual", [{}])[0] if (f or {}).get("annual") else {}
    neg = sum(1 for a in ((f or {}).get("annual") or [])[:3] if (a.get("free_cash_flow") or 0) < 0)
    eq = (f["market_cap_cr"] / f["pb"]) if f and f.get("pb") and f.get("market_cap_cr") else None
    g = (f or {}).get
    return (c["isin"], c["symbol"], c["bse_code"], c["yahoo"], c["name"], g("sector"), g("industry"),
            g("shares_cr"), g("ttm_net_profit"), g("ttm_sales"), eq,
            g("roe_pct"), g("roe_3y_avg_pct"), g("roce_pct"), g("roa_pct"), g("debt_to_equity"),
            g("sales_cagr_3y_pct"), g("profit_cagr_3y_pct"), a0.get("cfo_to_profit_plus_dep_pct"),
            a0.get("free_cash_flow"), neg if f else None, g("asset_turnover"), date.today().isoformat(), err,
            g("opm_pct"), g("npm_pct"), g("interest_cover"), g("q_sales_yoy_pct"), g("q_profit_yoy_pct"),
            g("div_yield_pct"), g("ev_ebitda"), int(is_lender(g("sector"), g("industry"))) if f else None,
            g("insiders_pct"), g("institutions_pct"), g("short_pct_float"), g("short_ratio_days"), g("short_chg_1m_pct"))


def refresh(limit: int | None = None, max_age_days: int = 7, force: list[str] | None = None,
            pause: float = 0.6) -> dict:
    """Fetch fundamentals for companies that are missing, failed (retried after a day) or older than
    max_age_days; `force` = ISINs to refresh now (e.g. results just out).

    One at a time with a short pause: Yahoo answers a burst with empty data, which must not be
    mistaken for "this company has no financials". Several empties in a row -> long pause."""
    conn = db()
    cands = candidates(conn)
    have = {r["isin"]: (r["updated"], r["error"]) for r in conn.execute("SELECT isin, updated, error FROM universe")}
    today = date.today()

    def due(i):
        if max_age_days <= 0 or (force and i in force):   # 0 = re-fetch everything, failures included
            return True
        if i not in have:
            return True
        upd, err = have[i]
        age = (today - date.fromisoformat(upd)).days
        return age >= (1 if err else max_age_days)

    todo = cands[[due(i) for i in cands["isin"]]]
    todo = todo.assign(_age=[(have.get(i) or ("0000-00-00",))[0] for i in todo["isin"]]).sort_values("_age")
    if limit:
        todo = todo.head(limit)
    stats = {"universe": len(cands), "todo": len(todo), "ok": 0, "failed": 0}
    empties = 0
    for i, (_, c) in enumerate(todo.iterrows()):
        f, err = None, None
        for attempt in range(3):
            try:
                f = fundamentals.fetch(c["yahoo"], with_sector=True)
                empties = 0
                if IS_US:   # official yearly numbers from 10-K filings replace Yahoo's where the SEC has them
                    row = conn.execute("SELECT cik FROM us_symbols WHERE ticker=?", (c["isin"],)).fetchone()
                    if row and row["cik"]:
                        from .sec_financials import upgrade
                        f = upgrade(f, row["cik"])
                break
            except LookupError as e:
                err = str(e)[:200]
                empties += 1
                if empties >= 5:           # looks like throttling, not missing data
                    log.warning("universe: %d empty replies in a row, pausing", empties)
                    time.sleep(120)
                    empties = 0
                    continue
                break
            except Exception as e:
                err = str(e)[:200]
                time.sleep(10 * (attempt + 1))
        with _db_lock:
            conn.execute(f"INSERT OR REPLACE INTO universe ({','.join(COLS)}) VALUES ({','.join('?' * len(COLS))})",
                         _row(c, f, None if f else err))
            if f:
                cache_put(conn, f"fund:{c['yahoo']}", f)
            conn.commit()
            if f and IS_US:
                snapshot_owners(conn, c["isin"])
        stats["ok" if f else "failed"] += 1
        if i and i % 100 == 0:
            log.info("universe: %d/%d (%d failed)", i, len(todo), stats["failed"])
        time.sleep(pause)
    return stats


_pm_cache: dict = {}


_pm_lock = __import__("threading").Lock()


def price_metrics(conn) -> tuple[str, pd.DataFrame]:
    """A year of prices boiled down to one row per company. Takes ~20 s, so it is built once per trading day
    and kept three ways: in memory, on disk (survives restarts; shared with the evening job, which builds it
    right after the day's prices land), and behind a lock (visitors wait for the one build in progress
    instead of each starting their own)."""
    from .config import DATA_DIR, MARKET
    day = conn.execute("SELECT max(date) FROM daily").fetchone()[0]
    rows = conn.execute("SELECT count(*) FROM daily WHERE date=?", (day,)).fetchone()[0]
    latest = f"{day}-{rows}-v3"  # prices still arriving for the day -> a new key, never a frozen half-day
    if IS_US:                    # FINRA's file lands after our prices: its arrival is a new key too
        from .offex import latest_day
        latest += "-" + (latest_day(conn) or "none")
    if _pm_cache.get("day") == latest:
        return _pm_cache["val"]
    with _pm_lock:
        if _pm_cache.get("day") == latest:
            return _pm_cache["val"]
        f = DATA_DIR / f"price-metrics-{MARKET['code']}-{latest}.pkl"
        val = None
        if f.exists():
            try:
                val = pd.read_pickle(f)
            except Exception:
                val = None
        if val is None:
            val = _price_metrics(conn)
            try:
                tmp = f.with_suffix(f".{os.getpid()}.tmp")   # two processes never share a temp file
                pd.to_pickle(val, tmp)
                tmp.replace(f)                       # atomic: readers never see half a file
                for old in DATA_DIR.glob(f"price-metrics-{MARKET['code']}-*.pkl"):
                    if old != f:
                        old.unlink(missing_ok=True)
            except Exception as e:
                log.warning("price metrics not saved to disk: %s", e)
        _pm_cache.update(day=latest, val=val)
        return val


def _offex(conn, vol: pd.DataFrame, since: str) -> pd.DataFrame:
    """US stand-in for delivery: off-exchange share of volume (FINRA), its 20-day level, and that level
    against the stock's own last year (percentage points). Latest FINRA day may be a day behind prices."""
    try:
        o = pd.read_sql_query("SELECT date, symbol isin, off_vol FROM offex WHERE date >= ?", conn, params=(since,))
    except Exception:
        return pd.DataFrame()
    if o.empty:
        return pd.DataFrame()
    off = o.pivot_table(index="date", columns="isin", values="off_vol", aggfunc="sum").sort_index()
    v = vol.reindex(index=off.index, columns=off.columns)
    pct = (off / v * 100).where((v > 0) & (off <= v * 1.05)).clip(upper=100)   # bad days (volume revised) drop out
    roll = (off.rolling(20, min_periods=10).sum() / v.where(off.notna()).rolling(20, min_periods=10).sum() * 100).clip(upper=100)
    return pd.DataFrame({"offex": pct.iloc[-1], "offex_20d": roll.iloc[-1],
                         "offex_chg": roll.iloc[-1] - roll.median()})


def _price_metrics(conn) -> tuple[str, pd.DataFrame]:
    dates = [r[0] for r in conn.execute(
        "SELECT DISTINCT date FROM daily WHERE exch=? ORDER BY date DESC LIMIT 252", (PRIMARY_EXCH,))]
    df = pd.read_sql_query(
        "SELECT date, isin, SUM(volume) vol, SUM(turnover) t, SUM(deliv_qty) dq, "
        f"COALESCE(MAX(CASE WHEN exch='{PRIMARY_EXCH}' THEN close END), MAX(close)) close, "
        f"COALESCE(MAX(CASE WHEN exch='{PRIMARY_EXCH}' THEN prev_close END), MAX(prev_close)) prev "
        f"FROM daily WHERE date >= ? GROUP BY date, isin", conn, params=(dates[-1],))
    for c in ("vol", "t", "dq", "close", "prev"):   # floats: 0/0 must give NaN, not raise (US has no delivery data)
        df[c] = pd.to_numeric(df[c], errors="coerce").astype(float)
    from .actions import adjust
    df = adjust(df, conn, price_cols=("close", "prev"), volume_cols=("vol", "dq"))
    today = dates[0]
    close = df.pivot_table(index="date", columns="isin", values="close", aggfunc="last").sort_index()
    vol = df.pivot_table(index="date", columns="isin", values="vol", aggfunc="sum").sort_index()
    dq = df.pivot_table(index="date", columns="isin", values="dq", aggfunc=lambda x: x.sum(min_count=1)).sort_index()
    g = df[df["date"] == today].groupby("isin")
    last = pd.DataFrame({"close": g["close"].last(), "prev": g["prev"].last(), "vol": g["vol"].sum(),
                         "t": g["t"].sum(), "dq": g["dq"].sum(min_count=1)})   # no delivery data -> NaN, not 0%

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
        "volume": last["vol"], "turnover_cr": last["t"] / MARKET["unit"],
        "delivery": last["dq"] / last["vol"] * 100,
        "delivery_30d": (dq.tail(30).sum() / vol.tail(30).where(dq.tail(30).notna()).sum()) * 100,
    })
    if IS_US:
        out = out.join(_offex(conn, vol, dates[-1]), how="left")
        for k in ("offex", "offex_20d", "offex_chg"):   # no FINRA data yet: blank columns, never missing ones
            if k not in out.columns:
                out[k] = float("nan")
    # 3-month trend for list sparklines: ~63 closes, thinned to 32 points
    tail = close.tail(63)
    out["spark"] = pd.Series({k: [round(float(x), 4) for x in tail[k].dropna().values[::2]] for k in tail.columns})
    out = out[out.index.isin(last.index)]
    return today, out


def table(conn) -> tuple[str, pd.DataFrame]:
    """Every universe company with fundamentals and today's price metrics, as one frame."""
    db()   # make sure the table (and newer columns) exist, e.g. on a brand-new market database
    today, pm = price_metrics(conn)
    u = pd.read_sql_query("SELECT * FROM universe WHERE error IS NULL", conn).set_index("isin")
    df = u.join(pm, how="inner")
    if IS_US:
        return today, _owner_changes(conn, _valuation(df))
    try:
        from .ownership import db as _odb
        _odb()
        o = pd.read_sql_query("SELECT isin, promoter, promoter_1y, pledge, fii, dii, fii_1y, dii_1y FROM ownership", conn).set_index("isin")
        df = df.join(o, how="left")
        df["promoter_chg"] = df["promoter"] - df["promoter_1y"]
        df["funds"] = df["fii"] + df["dii"]
        df["funds_chg"] = df["funds"] - (df["fii_1y"] + df["dii_1y"])
    except Exception as e:
        log.warning("ownership not joined: %s", e)
    return today, _valuation(df)


OWNER_HIST = """CREATE TABLE IF NOT EXISTS owner_hist (
    isin TEXT NOT NULL, day TEXT NOT NULL, insiders REAL, institutions REAL, short_float REAL,
    PRIMARY KEY (isin, day))"""


def snapshot_owners(conn, isin: str | None = None) -> int:
    """US: keep a dated copy of each company's ownership so changes over time can be screened
    (Yahoo only gives today's numbers). One row per company per day."""
    ensure_schema(conn, "owner_hist", OWNER_HIST)
    q = ("INSERT OR REPLACE INTO owner_hist SELECT isin, updated, insiders, institutions, short_float FROM universe "
         "WHERE error IS NULL AND (insiders IS NOT NULL OR institutions IS NOT NULL)" + (" AND isin=?" if isin else ""))
    n = conn.execute(q, (isin,) if isin else ()).rowcount
    conn.commit()
    return n


def _owner_changes(conn, df: pd.DataFrame) -> pd.DataFrame:
    """US ownership changes from SEC filings: insider net buying over 3 months (Form 4) and the change in
    funds' holdings over the latest reported quarter (13F). See us_owners.py."""
    try:
        from .us_owners import changes
        return df.join(changes(conn)[["insiders_chg", "insiders_net", "institutions_chg"]], how="left")
    except Exception as e:
        log.warning("ownership changes not joined: %s", e)
        for k in ("insiders_chg", "insiders_net", "institutions_chg"):
            df[k] = float("nan")
        return df


def _valuation(df: pd.DataFrame) -> pd.DataFrame:
    """Market cap and price ratios from today's price (both markets)."""
    df["mcap"] = df["price"] * df["shares_cr"]
    df["pe"] = np.where(df["ttm_profit"] > 0, df["mcap"] / df["ttm_profit"], np.nan)
    df["pb"] = np.where(df["equity"] > 0, df["mcap"] / df["equity"], np.nan)
    df["peg"] = np.where((df["profit_cagr_3y"] > 0) & df["pe"].notna(), df["pe"] / df["profit_cagr_3y"], np.nan)
    return df


def run_rules(rules: list, conn=None, limit: int = 200) -> dict:
    conn = conn or db()
    today, df = table(conn)
    total = len(df)
    mask = pd.Series(True, index=df.index)
    skipped = []
    for key, op, val in rules:
        if key not in df.columns:
            skipped.append(METRICS.get(key, (key,))[0])   # its data didn't load: say so, never filter silently
            continue
        col = df[key]
        if isinstance(val, str) and val.startswith("@") and val[1:] in df.columns:
            other = df[val[1:]]           # compare two measures, e.g. ROE above ROCE
            cond = ((col > other) if op == ">" else (col < other)) & other.notna()
        else:
            cond = (col > val) if op == ">" else (col < val)
        cond &= col.notna()
        if key == "de" and "lender" in df.columns:
            cond |= df["lender"].fillna(0).astype(bool)   # debt/equity doesn't apply to lenders
        mask &= cond
    hits = df[mask].copy()
    sort_key = next((k for k, _, _ in rules if k == "spike_x"), "mcap")
    if sort_key in hits.columns:
        hits = hits.sort_values(sort_key, ascending=False)
    hits = hits.head(limit)
    newest = conn.execute("SELECT max(updated), min(updated), count(*) FROM universe").fetchone()
    return {"day": today, "universe": total, "hits": hits.reset_index().to_dict("records"), "skipped": skipped,
            "fund_updated": newest[0], "fund_oldest": newest[1], "fund_count": newest[2]}


# The Screener lists only these two by default; the others stay usable by link.
SHOWN_PRESETS = ["volume-shock", "quality"]


def rule_label(key: str, op: str, val) -> str:
    """'ROE above 12%' style text for a rule."""
    name, _, unit, _ = METRICS.get(key, (key, "", "", ""))
    if isinstance(val, str) and val.startswith("@"):
        return f"{name} {'above' if op == '>' else 'below'} {METRICS.get(val[1:], (val[1:],))[0]}"
    v = f"{val:,.0f}" if abs(val) >= 1000 else f"{val:g}"
    pre, u = "", ""
    if unit in ("%", "×"):
        u = unit
    elif unit.startswith(MARKET["cur"]):
        pre, u = MARKET["cur"], " " + unit[len(MARKET["cur"]):].strip() if unit[len(MARKET["cur"]):].strip() else ""
    elif unit:
        u = " " + unit
    return f"{name.split(' (')[0]} {'above' if op == '>' else 'below'} {pre}{v}{u}"


def funnel(rules: list, conn=None) -> dict:
    """Run a screen and count how many companies are left after each rule, in order (the evening run's
    'why these' panel). Same engine and same result as the Screener."""
    conn = conn or db()
    res = run_rules(rules, conn, limit=500)
    today, df = table(conn)
    left = pd.Series(True, index=df.index)
    steps = []
    for key, op, val in rules:
        if key not in df.columns:
            continue
        col = df[key]
        if isinstance(val, str) and val.startswith("@"):
            if val[1:] not in df.columns:
                continue
            other = df[val[1:]]
            cond = ((col > other) if op == ">" else (col < other)) & other.notna()
        else:
            cond = (col > val) if op == ">" else (col < val)
        cond &= col.notna()
        if key == "de" and "lender" in df.columns:
            cond |= df["lender"].fillna(0).astype(bool)
        left &= cond
        steps.append([rule_label(key, op, val), int(left.sum())])
    res["steps"] = steps
    return res
