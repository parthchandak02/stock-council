"""Company financials from Yahoo Finance, turned into Screener-style ratios.

Checked against Screener for Hi-Tech Pipes (Nov 2026): ROE 5.88%, P/E 21.1, D/E 0.25 all match.
Definitions (Screener's, as far as they are public):
  ROE   = last FY net profit / average of opening and closing equity
  ROCE  = last FY EBIT / average capital employed (equity + debt)
  P/E   = market cap / trailing-12-month net profit (last 4 quarters)
  D/E   = total debt / equity from the latest balance sheet (half-yearly if available)
All money values are in ₹ crore.
"""
from __future__ import annotations

import math

import pandas as pd
import yfinance as yf

CR = 1e7


def _row(df: pd.DataFrame, *names: str) -> list[float | None]:
    for n in names:
        if n in df.index:
            return [None if pd.isna(v) else float(v) / CR for v in df.loc[n].tolist()]
    return [None] * len(df.columns)


def _avg(a, b):
    if a is None:
        return None
    return a if b is None else (a + b) / 2


def _div(a, b, scale=1.0):
    if a is None or b in (None, 0):
        return None
    return round(a / b * scale, 2)


def _cagr(new, old, years):
    if not new or not old or new <= 0 or old <= 0:
        return None
    return round(((new / old) ** (1 / years) - 1) * 100, 1)


def fetch(yahoo_symbol: str, price: float | None = None) -> dict:
    t = yf.Ticker(yahoo_symbol)
    inc, bs, cf = t.income_stmt, t.balance_sheet, t.cashflow
    qi, qb = t.quarterly_income_stmt, t.quarterly_balance_sheet
    if inc is None or inc.empty or bs is None or bs.empty:
        raise LookupError(f"no financials on Yahoo for {yahoo_symbol}")

    # drop fully empty year columns (Yahoo often returns a blank oldest year)
    inc = inc.loc[:, inc.notna().any()]
    bs = bs.loc[:, bs.notna().any()]
    cf = cf.loc[:, cf.notna().any()] if cf is not None else pd.DataFrame()
    years = [c.date().isoformat() for c in inc.columns]

    rev = _row(inc, "Total Revenue", "Operating Revenue")
    pat = _row(inc, "Net Income Common Stockholders", "Net Income")
    dep = _row(inc, "Reconciled Depreciation", "Depreciation And Amortization In Income Statement")
    ebit = _row(inc, "EBIT", "Operating Income")
    cogs = _row(inc, "Cost Of Revenue")
    eq = _row(bs, "Stockholders Equity", "Common Stock Equity")
    debt = _row(bs, "Total Debt")
    assets = _row(bs, "Total Assets")
    inv = _row(bs, "Inventory")
    recv = _row(bs, "Accounts Receivable", "Receivables")
    pay = _row(bs, "Accounts Payable", "Payables")
    ca = _row(bs, "Current Assets")
    cl = _row(bs, "Current Liabilities")
    cfo = _row(cf, "Operating Cash Flow") if not cf.empty else []
    capex = _row(cf, "Capital Expenditure") if not cf.empty else []

    def at(lst, i):
        return lst[i] if i < len(lst) else None

    annual = []
    for i, y in enumerate(years):
        r, p, d = at(rev, i), at(pat, i), at(dep, i)
        c, cx = at(cfo, i), at(capex, i)
        base = (p or 0) + (d or 0) if p is not None else None
        cost = at(cogs, i) or r
        annual.append({
            "fy_end": y, "sales": _r(r), "net_profit": _r(p), "depreciation": _r(d),
            "operating_cash_flow": _r(c), "capex": _r(cx),
            "free_cash_flow": _r(c + cx) if c is not None and cx is not None else None,
            "cfo_to_profit_plus_dep_pct": _div(c, base, 100),
            "debt_to_equity": _div(at(debt, i), at(eq, i)),
            "inventory_days": _div(at(inv, i), cost, 365),
            "receivable_days": _div(at(recv, i), r, 365),
            "payable_days": _div(at(pay, i), cost, 365),
            "working_capital_days": _div((at(ca, i) or 0) - (at(cl, i) or 0), r, 365)
            if at(ca, i) is not None and at(cl, i) is not None else None,
        })
        a = annual[-1]
        if None not in (a["inventory_days"], a["receivable_days"], a["payable_days"]):
            a["cash_conversion_cycle_days"] = round(
                a["inventory_days"] + a["receivable_days"] - a["payable_days"], 1)

    # trailing twelve months profit and latest balance sheet
    ttm_pat = ttm_sales = None
    if qi is not None and not qi.empty:
        qp = _row(qi, "Net Income Common Stockholders", "Net Income")[:4]
        qs = _row(qi, "Total Revenue", "Operating Revenue")[:4]
        if len(qp) == 4 and None not in qp:
            ttm_pat = sum(qp)
        if len(qs) == 4 and None not in qs:
            ttm_sales = sum(qs)
    ttm_pat = ttm_pat if ttm_pat is not None else at(pat, 0)
    latest_eq, latest_debt, bs_date = at(eq, 0), at(debt, 0), years[0]
    if qb is not None and not qb.empty:
        qeq, qdebt = _row(qb, "Stockholders Equity", "Common Stock Equity"), _row(qb, "Total Debt")
        if qeq and qeq[0] is not None:
            latest_eq, latest_debt, bs_date = qeq[0], qdebt[0], qb.columns[0].date().isoformat()

    shares = None
    try:
        shares = t.fast_info.get("shares")
    except Exception:
        pass
    if price is None:
        try:
            price = float(t.fast_info.get("lastPrice"))
        except Exception:
            price = None
    mcap = price * shares / CR if price and shares else None

    quarters = []
    if qi is not None and not qi.empty:
        qs_all = _row(qi, "Total Revenue", "Operating Revenue")
        qp_all = _row(qi, "Net Income Common Stockholders", "Net Income")
        for i, c in enumerate(qi.columns[:5]):
            quarters.append({"quarter_end": c.date().isoformat(), "sales": _r(at(qs_all, i)),
                             "net_profit": _r(at(qp_all, i))})

    pe = _div(mcap, ttm_pat) if ttm_pat and ttm_pat > 0 else None
    profit_cagr_3y = _cagr(at(pat, 0), at(pat, 3), 3)
    return {
        "yahoo_symbol": yahoo_symbol,
        "price": _r(price), "shares_cr": _r(shares / CR) if shares else None,
        "market_cap_cr": _r(mcap),
        "ttm_net_profit": _r(ttm_pat), "ttm_sales": _r(ttm_sales),
        "pe": pe,
        "pb": _div(mcap, latest_eq),
        "roe_pct": _div(at(pat, 0), _avg(at(eq, 0), at(eq, 1)), 100),
        "roe_3y_avg_pct": _mean([_div(at(pat, i), _avg(at(eq, i), at(eq, i + 1)), 100) for i in range(3)]),
        "roce_pct": _div(at(ebit, 0), _avg(_sum(at(eq, 0), at(debt, 0)), _sum(at(eq, 1), at(debt, 1))), 100),
        "roa_pct": _div(at(pat, 0), _avg(at(assets, 0), at(assets, 1)), 100),
        "asset_turnover": _div(at(rev, 0), _avg(at(assets, 0), at(assets, 1))),
        "debt_to_equity": _div(latest_debt, latest_eq),
        "debt_to_equity_as_of": bs_date,
        "sales_cagr_3y_pct": _cagr(at(rev, 0), at(rev, 3), 3),
        "profit_cagr_3y_pct": profit_cagr_3y,
        "peg": round(pe / profit_cagr_3y, 2) if pe and profit_cagr_3y and profit_cagr_3y > 0 else None,
        "annual": annual,
        "quarters": quarters,
    }


def _r(x, n=1):
    return None if x is None or (isinstance(x, float) and math.isnan(x)) else round(x, n)


def _sum(a, b):
    return None if a is None else a + (b or 0)


def _mean(xs):
    xs = [x for x in xs if x is not None]
    return round(sum(xs) / len(xs), 2) if xs else None
