"""The volume-shock screen, Screener-style, run on our own exchange data.

Order matters for speed: the volume test needs only exchange files, so it runs first across every
listed company; the fundamentals test (Yahoo) runs only on the few dozen volume spikes.
Volume is NSE + BSE combined, matching Screener.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

import pandas as pd

from . import fundamentals
from .config import DEFAULT_FILTERS, IS_US, PRIMARY_EXCH, WEEK_DAYS, YEAR_DAYS
from .store import cache_get, cache_put, connect

log = logging.getLogger(__name__)


@dataclass
class ScreenResult:
    date: str
    filters: dict
    spikes: int
    companies: int = 0
    passed: list[dict] = field(default_factory=list)
    rejected: list[dict] = field(default_factory=list)


def trading_dates(conn, n: int) -> list[str]:
    rows = conn.execute(
        "SELECT DISTINCT date FROM daily WHERE exch=? ORDER BY date DESC LIMIT ?", (PRIMARY_EXCH, n)
    ).fetchall()
    return [r["date"] for r in rows]


def volume_spikes(conn, filters: dict) -> tuple[str, pd.DataFrame]:
    dates = trading_dates(conn, YEAR_DAYS)
    if len(dates) < 60:
        raise RuntimeError(f"only {len(dates)} trading days stored; run `stock-council update` first")
    today, week = dates[0], set(dates[:WEEK_DAYS])
    df = pd.read_sql_query(
        "SELECT date, isin, SUM(volume) AS vol, SUM(turnover) AS t FROM daily WHERE date >= ? GROUP BY date, isin",
        conn, params=(dates[-1],),
    )
    from .actions import adjust
    df = adjust(df, conn)  # splits/bonuses: earlier volumes in today's share terms
    g = df.groupby("isin")
    stats = pd.DataFrame({
        "vol_1y_avg": g["vol"].mean(),
        "days": g["vol"].size(),
        "vol_1w_avg": df[df["date"].isin(week)].groupby("isin")["vol"].mean(),
        "vol_today": df[df["date"] == today].set_index("isin")["vol"],
        "turnover_today": df[df["date"] == today].groupby("isin")["t"].sum(),
    }).dropna(subset=["vol_today", "vol_1w_avg"])
    # India: "Volume > 10,000" is shares (Screener's rule). US: dollars traded today.
    traded = stats["turnover_today"].fillna(0) if IS_US else stats["vol_today"]
    stats["spike_x"] = stats["vol_1w_avg"] / stats["vol_1y_avg"]
    hits = stats[(traded > filters["min_volume"])
                 & (stats["vol_1w_avg"] > filters["volume_spike_multiple"] * stats["vol_1y_avg"])
                 & (stats["days"] >= 20)]
    return today, hits.sort_values("spike_x", ascending=False), len(stats)


def identity(conn, isin: str, day: str) -> dict:
    rows = conn.execute(
        "SELECT * FROM daily WHERE isin=? AND date=? ORDER BY exch DESC", (isin, day)
    ).fetchall()  # NSE first
    nse = next((r for r in rows if r["exch"] == PRIMARY_EXCH), None)
    bse = next((r for r in rows if r["exch"] == "BSE"), None)
    main = nse or bse
    vol = sum(r["volume"] or 0 for r in rows)
    deliv = [r["deliv_qty"] for r in rows if r["deliv_qty"] is not None]
    return {
        "isin": isin,
        "nse_symbol": nse["symbol"] if nse else None,
        "bse_code": bse["code"] if bse else None,
        "bse_symbol": bse["symbol"] if bse else None,
        "name": (bse["name"] if bse else None) or main["name"] or main["symbol"],
        "close": main["close"],
        "change_pct": round((main["close"] / main["prev_close"] - 1) * 100, 2) if main["prev_close"] else None,
        "volume": vol,
        "delivery_pct": round(sum(deliv) / vol * 100, 1) if deliv and vol else None,
        "yahoo_symbol": _yahoo(conn, nse, bse),
        "cik": main["code"] if IS_US else None,   # SEC company id (US rows keep it in `code`)
    }


def _yahoo(conn, nse, bse) -> str:
    if IS_US:
        row = conn.execute("SELECT yahoo FROM us_symbols WHERE ticker=?", (nse["symbol"],)).fetchone()
        return row["yahoo"] if row else nse["symbol"].replace(".", "-")
    return f"{nse['symbol']}.NS" if nse else f"{bse['symbol']}.BO"


def get_fundamentals(conn, ident: dict) -> dict:
    key = f"fund:{ident['yahoo_symbol']}"
    cached = cache_get(conn, key, max_age_days=6)
    if cached is None:
        cached = fundamentals.fetch(ident["yahoo_symbol"], with_sector=IS_US)
        cache_put(conn, key, cached)
    # re-price with today's exchange close so market cap and P/E are current
    f = dict(cached)
    f.update(_sector(conn, ident["isin"], f))
    if ident["close"] and f.get("shares_cr"):
        f["price"] = ident["close"]
        f["market_cap_cr"] = round(ident["close"] * f["shares_cr"], 1)
        if f.get("ttm_net_profit") and f["ttm_net_profit"] > 0:
            f["pe"] = round(f["market_cap_cr"] / f["ttm_net_profit"], 2)
        # P/B uses the latest equity, which we can recover from the cached P/B
        if cached.get("pb") and cached.get("market_cap_cr"):
            f["pb"] = round(cached["pb"] * f["market_cap_cr"] / cached["market_cap_cr"], 2)
    return f


def _sector(conn, isin: str, f: dict) -> dict:
    """Sector, industry and whether it is a lender, from the Screener's universe table."""
    from .universe import is_lender
    sector, industry = f.get("sector"), f.get("industry")
    try:
        row = conn.execute("SELECT sector, industry FROM universe WHERE isin=?", (isin,)).fetchone()
        if row and row["sector"]:
            sector, industry = row["sector"], row["industry"]
    except Exception:
        pass
    return {"sector": sector, "industry": industry, "lender": is_lender(sector, industry)}


def check(f: dict, filters: dict) -> list[str]:
    """Return the reasons a stock fails; empty list = passes."""
    fails = []
    mc, pe, roe, de = f.get("market_cap_cr"), f.get("pe"), f.get("roe_pct"), f.get("debt_to_equity")
    if mc is None or mc <= filters["min_market_cap_cr"]:
        fails.append(f"market cap {mc} ≤ {filters['min_market_cap_cr']} Cr")
    if pe is None or pe >= filters["max_pe"]:
        fails.append(f"P/E {pe} ≥ {filters['max_pe']}" if pe is not None else "P/E n/a (loss)")
    if roe is None or roe <= filters["min_roe_pct"]:
        fails.append(f"ROE {roe}% ≤ {filters['min_roe_pct']}%")
    if not f.get("lender") and (de is None or de >= filters["max_debt_to_equity"]):  # not for banks/NBFCs
        fails.append(f"D/E {de} ≥ {filters['max_debt_to_equity']}")
    return fails


def run_screen(filters: dict | None = None, on_progress=None) -> ScreenResult:
    """on_progress(result) is called after the volume scan and after every fundamentals check."""
    filters = {**DEFAULT_FILTERS, **(filters or {})}
    conn = connect()
    day, hits, companies = volume_spikes(conn, filters)
    res = ScreenResult(date=day, filters=filters, spikes=len(hits), companies=companies)
    if on_progress:
        on_progress(res)
    for isin, h in hits.iterrows():
        ident = identity(conn, isin, day)
        ident.update(vol_1w_avg=int(h["vol_1w_avg"]), vol_1y_avg=int(h["vol_1y_avg"]),
                     spike_x=round(float(h["spike_x"]), 1))
        try:
            f = get_fundamentals(conn, ident)
        except Exception as e:
            log.warning("%s: no fundamentals (%s)", ident["yahoo_symbol"], e)
            res.rejected.append({**ident, "fails": ["no financials published"]})
            if on_progress:
                on_progress(res)
            continue
        row = {**ident, **{k: f.get(k) for k in (
            "market_cap_cr", "pe", "pb", "roe_pct", "roce_pct", "debt_to_equity", "sales_cagr_3y_pct")}}
        fails = check(f, filters)
        (res.rejected if fails else res.passed).append({**row, "fails": fails})
        if on_progress:
            on_progress(res)
    res.passed.sort(key=lambda r: -r["spike_x"])
    return res


def render(res: ScreenResult) -> str:
    out = [f"Screen for {res.date}: {res.spikes} volume spikes, {len(res.passed)} pass all filters\n"]
    hdr = f"{'Stock':28} {'Price':>8} {'Chg%':>6} {'Spike':>6} {'Deliv%':>6} {'MCap Cr':>9} {'P/E':>6} {'ROE%':>6} {'D/E':>5}"
    out.append(hdr)
    for r in res.passed:
        out.append(f"{(r['nse_symbol'] or r['bse_symbol'] or '')[:12] + ' ' + (r['name'] or '')[:15]:28} "
                   f"{r['close'] or 0:8.1f} {r['change_pct'] or 0:6.1f} {r['spike_x']:5.1f}x "
                   f"{r['delivery_pct'] or 0:6.1f} {r['market_cap_cr'] or 0:9.0f} {r['pe'] or 0:6.1f} "
                   f"{r['roe_pct'] or 0:6.1f} {r['debt_to_equity'] or 0:5.2f}")
    out.append(f"\nRejected by fundamentals ({len(res.rejected)}):")
    for r in res.rejected[:40]:
        out.append(f"  {(r['nse_symbol'] or r['bse_symbol'] or '')[:14]:14} {r['spike_x']:5.1f}x  " + "; ".join(r["fails"]))
    return "\n".join(out)
