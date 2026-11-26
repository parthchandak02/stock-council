"""One checked fact sheet per stock: the shared facts every AI on the council receives.

Sources: NSE/BSE daily files (price, volume, delivery), NSE disclosures (shareholding, pledge),
Yahoo Finance (financial statements). Numbers here are computed, never guessed by an AI.
"""
from __future__ import annotations

import pandas as pd

from .config import IS_US, MARKET, PRIMARY_EXCH

C, U = MARKET["cur"], MARKET["unit_label"]   # ₹ / Cr (India) or $ / M (US)
from .nse_api import NSE
from .screen import get_fundamentals, identity, trading_dates
from .store import cache_get, cache_put, connect


def _isin_for(conn, symbol: str) -> str:
    row = conn.execute(
        f"SELECT isin FROM daily WHERE (symbol=? AND exch='{PRIMARY_EXCH}') OR code=? OR (symbol=? AND exch='BSE') "
        "ORDER BY date DESC LIMIT 1", (symbol, symbol, symbol)).fetchone()
    if not row:
        raise LookupError(f"{symbol} not found in {MARKET['exchanges']} data")
    return row["isin"]


def technicals(conn, isin: str) -> dict:
    from .actions import aliases
    ids = aliases(conn, isin)  # one history across an ISIN change (splits)
    df = pd.read_sql_query(
        "SELECT date, SUM(volume) vol, SUM(deliv_qty) deliv, "
        f"MAX(CASE WHEN exch='{PRIMARY_EXCH}' THEN close END) nse_close, MAX(CASE WHEN exch='BSE' THEN close END) bse_close "
        f"FROM daily WHERE isin IN ({','.join('?' * len(ids))}) GROUP BY date ORDER BY date", conn, params=ids)
    from .actions import adjust
    for c in ("vol", "deliv", "nse_close", "bse_close"):   # an exchange with no rows reads as None objects
        df[c] = pd.to_numeric(df[c], errors="coerce").astype(float)
    df["isin"] = isin
    df = adjust(df, conn, price_cols=("nse_close", "bse_close"), volume_cols=("vol", "deliv"))
    df["close"] = df["nse_close"].fillna(df["bse_close"])
    c, v = df["close"], df["vol"]
    last = c.iloc[-1]
    delta = c.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / 14, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / 14, adjust=False).mean()
    rsi = 100 - 100 / (1 + gain / loss)
    # On-balance volume over ~3 months: rising OBV = accumulation, falling = distribution
    obv = (v * delta.apply(lambda x: 1 if x > 0 else (-1 if x < 0 else 0))).cumsum()
    obv_3m = obv.iloc[-1] - obv.iloc[-63] if len(obv) > 63 else None
    # Consolidation before the spike: price range over the 60 sessions before the last week
    base = c.iloc[-65:-5] if len(c) > 65 else c.iloc[:-5]
    base_range = (base.max() / base.min() - 1) * 100 if len(base) > 10 else None
    deliv_pct = (df["deliv"] / df["vol"] * 100).where(df["deliv"].notna())
    return {
        "close": round(last, 2),
        "high_52w": round(c.max(), 2), "low_52w": round(c.min(), 2),
        "pct_from_52w_high": round((last / c.max() - 1) * 100, 1),
        "return_1y_pct": round((last / c.iloc[0] - 1) * 100, 1),
        "return_1m_pct": round((last / c.iloc[-22] - 1) * 100, 1) if len(c) > 22 else None,
        "sma_50": round(c.tail(50).mean(), 2), "sma_200": round(c.tail(200).mean(), 2),
        "rsi_14": round(rsi.iloc[-1], 1),
        "volume_today": int(v.iloc[-1]), "volume_1w_avg": int(v.tail(5).mean()),
        "volume_1y_avg": int(v.mean()),
        "delivery_pct_today": round(deliv_pct.iloc[-1], 1) if pd.notna(deliv_pct.iloc[-1]) else None,
        "delivery_pct_30d_avg": round(deliv_pct.tail(30).mean(), 1) if deliv_pct.notna().any() else None,
        "obv_change_3m_vs_avg_daily_volume": round(obv_3m / v.mean(), 1) if obv_3m is not None else None,
        "range_60d_before_spike_pct": round(base_range, 1) if base_range is not None else None,
        "last_10_sessions": [
            {"date": d, "close": round(cl, 2), "volume": int(vo),
             "delivery_pct": None if pd.isna(dp) else round(dp, 1)}
            for d, cl, vo, dp in zip(df["date"].tail(10), c.tail(10), v.tail(10), deliv_pct.tail(10))
        ],
    }


def _offex_facts(conn, symbol: str) -> dict:
    """US: off-exchange share of volume from FINRA (stand-in for India's delivery %, not the same thing:
    it says where trades happened, not whether shares were kept)."""
    try:
        rows = conn.execute(
            "SELECT o.date, o.off_vol, d.volume FROM offex o JOIN daily d ON d.date=o.date AND d.symbol=o.symbol "
            "WHERE o.symbol=? ORDER BY o.date DESC LIMIT 260", (symbol,)).fetchall()
    except Exception:
        return {}
    pts = [(r["date"], r["off_vol"], r["volume"]) for r in rows if r["volume"] and r["off_vol"] is not None and r["off_vol"] <= r["volume"] * 1.05]
    if len(pts) < 10:
        return {}
    pct = lambda xs: round(100 * sum(o for _, o, _ in xs) / sum(v for _, _, v in xs), 1)
    roll = sorted(pct(pts[i:i + 20]) for i in range(0, max(1, len(pts) - 19)))
    return {"off_exchange_pct_day": pct(pts[:1]), "off_exchange_day": pts[0][0],
            "off_exchange_pct_20d": pct(pts[:20]), "off_exchange_pct_20d_1y_median": roll[len(roll) // 2],
            "off_exchange_note": "FINRA off-exchange share of volume (dark pools and wholesalers); "
                                 "a fall against its own median means more trading moved onto the exchanges"}


def build(symbol: str, nse: NSE | None = None) -> dict:
    conn = connect()
    isin = _isin_for(conn, symbol)
    day = trading_dates(conn, 1)[0]
    ident = identity(conn, isin, day)
    tech = technicals(conn, isin)
    sheet = {"as_of": day, **ident, "technicals": tech,
             "spike_x": round(tech["volume_1w_avg"] / max(tech["volume_1y_avg"], 1), 1)}
    sheet["fundamentals"] = get_fundamentals(conn, ident)
    if IS_US:
        from .sec_financials import upgrade
        from .us_extras import extras
        sheet["fundamentals"] = upgrade(sheet["fundamentals"], ident.get("cik"))   # official 10-K years
        sheet.update(extras(conn, ident, sheet["fundamentals"]))
        tech.update(_offex_facts(conn, isin))
    elif ident["nse_symbol"]:
        key = f"nse:{ident['nse_symbol']}"
        disc = cache_get(conn, key, max_age_days=2)
        if disc is None:
            nse = nse or NSE()
            disc = {"shareholding": nse.shareholding(ident["nse_symbol"]),
                    "pledge": nse.pledge(ident["nse_symbol"])}
            cache_put(conn, key, disc)
        sheet.update(disc)
        full = next((q.get("company") for q in disc.get("shareholding") or [] if q.get("company")), None)
        if full:
            sheet["name"] = full  # exchange files cut names short; the filing has the full name
        try:
            from .filings import for_symbol
            sheet["announcements"] = for_symbol(conn, ident["nse_symbol"])
        except Exception:
            sheet["announcements"] = []
    return sheet


def _fmt(x, suffix=""):
    return "n/a" if x is None else f"{x:,}{suffix}" if isinstance(x, int) else f"{x}{suffix}"


def _in_owners(s: dict) -> list[str]:
    L = ["", "## 3. Shareholding & governance (% of total shares, NSE filings)"]
    if s.get("shareholding"):
        L += ["| Quarter | Promoter | FII | DII | of which MFs | Public (non-institutional) | Small retail (<₹2L) |",
              "|---|---|---|---|---|---|---|"]
        for q in s["shareholding"]:
            L.append(f"| {q['quarter']} | {_fmt(q.get('promoter'))} | {_fmt(q.get('fii'))} | {_fmt(q.get('dii'))} | "
                     f"{_fmt(q.get('mutual_funds'))} | {_fmt(q.get('public_non_institutions'))} | {_fmt(q.get('retail_small'))} |")
    else:
        L.append("Shareholding not available (BSE-only company).")
    p = s.get("pledge")
    L.append(f"Promoter pledge: {_fmt(p['pledged_pct_of_promoter_shares'], '%')} of promoter shares = "
             f"{_fmt(p['pledged_pct_of_total_shares'], '%')} of total shares (as of {p['as_of']})"
             if p else "Promoter pledge: none disclosed on NSE.")
    return L


def _us_owners(s: dict) -> list[str]:
    o = s.get("owners_us") or {}
    ins = s.get("insiders") or {}
    L = ["", "## 3. Ownership & insiders (US; there is no promoter pledge here)",
         f"- Insiders own {_fmt(o.get('insiders_pct'), '%')}; institutions own {_fmt(o.get('institutions_pct'), '%')}",
         f"- Short interest {_fmt(o.get('short_pct_float'), '%')} of float, {_fmt(o.get('short_ratio_days'))} days to cover"]
    if ins:
        L.append(f"- Insider open-market trades, last {ins['days']} days (SEC Form 4): buys ${ins['buys_usd']:,} by "
                 f"{ins['buyers']}, sells ${ins['sells_usd']:,} by {ins['sellers']} → net ${ins['net_usd']:,}")
        if ins.get("trades"):
            L += ["", "| Date | Insider | Role | Type | Shares | Price |", "|---|---|---|---|---|---|"]
            L += [f"| {x['date']} | {x['who']} | {x['role']} | {x['type']} | {x['shares']:,} | ${x['price']} |"
                  for x in ins["trades"]]
    return L


def render_markdown(s: dict) -> str:
    f, t = s["fundamentals"], s["technicals"]
    L = [f"# {s['name']} ({s['nse_symbol'] or s['bse_symbol']}) — fact sheet as of {s['as_of']}",
         f"Price {C}{t['close']} ({_fmt(s['change_pct'], '%')} today) · Market cap {C}{_fmt(f.get('market_cap_cr'))} {U}",
         "", f"## 1. Cash-flow quality ({C} {U}, financial years{', from ' + f['annual_source'] if f.get('annual_source') else ''})",
         "| FY end | Sales | Net profit | Depreciation | Operating CF | CFO/(NP+Dep) | Capex | Free CF |",
         "|---|---|---|---|---|---|---|---|"]
    for a in f["annual"][:4]:
        L.append(f"| {a['fy_end']} | {_fmt(a['sales'])} | {_fmt(a['net_profit'])} | {_fmt(a['depreciation'])} | "
                 f"{_fmt(a['operating_cash_flow'])} | {_fmt(a['cfo_to_profit_plus_dep_pct'], '%')} | "
                 f"{_fmt(a['capex'])} | {_fmt(a['free_cash_flow'])} |")
    L += ["", "## 2. Working capital & solvency",
          "| FY end | Inventory days | Receivable days | Payable days | Cash conversion cycle | Working-capital days | Debt/Equity |",
          "|---|---|---|---|---|---|---|"]
    for a in f["annual"][:4]:
        L.append(f"| {a['fy_end']} | {_fmt(a['inventory_days'])} | {_fmt(a['receivable_days'])} | "
                 f"{_fmt(a['payable_days'])} | {_fmt(a.get('cash_conversion_cycle_days'))} | "
                 f"{_fmt(a['working_capital_days'])} | {_fmt(a['debt_to_equity'])} |")
    L.append(f"Latest debt/equity: {_fmt(f.get('debt_to_equity'))} (balance sheet {f.get('debt_to_equity_as_of')})")
    if IS_US:
        L += _us_owners(s)
    else:
        L += _in_owners(s)
    L += ["", "## 4. Technicals & volume" + ("" if IS_US else " (NSE+BSE combined)"),
          f"- 52-week range {C}{t['low_52w']}–{C}{t['high_52w']} ({_fmt(t['pct_from_52w_high'], '%')} from high); "
          f"1-month return {_fmt(t['return_1m_pct'], '%')}, 1-year {_fmt(t['return_1y_pct'], '%')}",
          f"- 50-day avg {C}{t['sma_50']}, 200-day avg {C}{t['sma_200']}, RSI(14) {t['rsi_14']}",
          f"- Volume today {_fmt(t['volume_today'])}; 1-week avg {_fmt(t['volume_1w_avg'])}; "
          f"1-year avg {_fmt(t['volume_1y_avg'])} → {s.get('spike_x') or round(t['volume_1w_avg'] / max(t['volume_1y_avg'], 1), 1)}× spike"]
    if not IS_US:   # the US has no delivery data
        L.append(f"- Delivery today {_fmt(t['delivery_pct_today'], '%')} vs 30-day avg {_fmt(t['delivery_pct_30d_avg'], '%')} "
                 "(low delivery on a spike = intraday speculation; high = genuine buying)")
    L += [f"- On-balance volume change over 3 months: {_fmt(t['obv_change_3m_vs_avg_daily_volume'])} days of average volume "
          "(positive = accumulation, negative = distribution)",
          f"- Price range in the 60 sessions before the last week: {_fmt(t['range_60d_before_spike_pct'], '%')} "
          "(narrow range = consolidation)",
          "", "| Date | Close | Volume | Delivery % |", "|---|---|---|---|"]
    for d in t["last_10_sessions"]:
        L.append(f"| {d['date']} | {d['close']} | {d['volume']:,} | {_fmt(d['delivery_pct'])} |")
    L += ["", "## 5. Growth",
          f"- 3-year sales CAGR {_fmt(f.get('sales_cagr_3y_pct'), '%')}, 3-year profit CAGR {_fmt(f.get('profit_cagr_3y_pct'), '%')}",
          "| Quarter end | Sales | Net profit |", "|---|---|---|"]
    for q in f.get("quarters", []):
        L.append(f"| {q['quarter_end']} | {_fmt(q['sales'])} | {_fmt(q['net_profit'])} |")
    L += ["", "## 6. Valuation & efficiency",
          f"- TTM P/E {_fmt(f.get('pe'))}, P/B {_fmt(f.get('pb'))}, PEG {_fmt(f.get('peg'))} (P/E ÷ 3-yr profit CAGR)",
          f"- ROE {_fmt(f.get('roe_pct'), '%')} (last FY; 3-yr avg {_fmt(f.get('roe_3y_avg_pct'), '%')}), "
          f"ROCE {_fmt(f.get('roce_pct'), '%')}, ROA {_fmt(f.get('roa_pct'), '%')}, asset turnover {_fmt(f.get('asset_turnover'))}×",
          f"- TTM sales {C}{_fmt(f.get('ttm_sales'))} {U}, TTM net profit {C}{_fmt(f.get('ttm_net_profit'))} {U}"]
    if s.get("announcements"):
        L += ["", "## 7. Official filings: SEC 8-K (last 30 days)" if IS_US else "## 7. Official announcements to NSE (last 30 days)"]
        L += [f"- {a['at'][:10]} {a['subject']}: {a['text'][:300]}" for a in s["announcements"]]
    return "\n".join(_spaced(L))


def _spaced(lines: list[str]) -> list[str]:
    """Markdown tables need a blank line before and after them, or they print as raw text."""
    out: list[str] = []
    for line in lines:
        is_row = line.startswith("|")
        if out and out[-1].strip() and is_row != out[-1].startswith("|"):
            out.append("")
        out.append(line)
    return out
