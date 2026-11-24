"""Official yearly financials for US companies, straight from their 10-K filings (SEC XBRL "companyfacts").

Free, no key, ~10 years deep. Used for the US fact sheet instead of Yahoo's 4 years; Yahoo still gives the
price, quarters and short interest. Values are $ millions, newest year first, in the same shape as
fundamentals.annual so every page and the council read them unchanged.
"""
from __future__ import annotations

import time
from datetime import date

import httpx

from .config import MARKET
from .store import cache_get, cache_put, connect
from .us_data import SEC_UA

UNIT = MARKET["unit"]
TAGS = {   # first tag a company reports wins
    "sales": ["RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues", "SalesRevenueNet",
              "RevenueFromContractWithCustomerIncludingAssessedTax", "RevenuesNetOfInterestExpense"],
    "net_profit": ["NetIncomeLoss", "ProfitLoss", "NetIncomeLossAvailableToCommonStockholdersBasic"],
    "depreciation": ["DepreciationDepletionAndAmortization", "DepreciationAndAmortization", "DepreciationAmortizationAndAccretionNet", "Depreciation"],
    "cfo": ["NetCashProvidedByUsedInOperatingActivities", "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations"],
    "capex": ["PaymentsToAcquirePropertyPlantAndEquipment", "PaymentsToAcquireProductiveAssets"],
    "cogs": ["CostOfRevenue", "CostOfGoodsAndServicesSold", "CostOfGoodsSold"],
    "ebit": ["OperatingIncomeLoss"],
    "equity": ["StockholdersEquity", "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"],
    "assets": ["Assets"],
    "inventory": ["InventoryNet"],
    "receivables": ["AccountsReceivableNetCurrent", "ReceivablesNetCurrent"],
    "payables": ["AccountsPayableCurrent", "AccountsPayableAndAccruedLiabilitiesCurrent"],
    "cur_assets": ["AssetsCurrent"],
    "cur_liab": ["LiabilitiesCurrent"],
    "lt_debt": ["LongTermDebt", "LongTermDebtNoncurrent"],
    "lt_debt_current": ["LongTermDebtCurrent", "DebtCurrent"],
    "short_debt": ["ShortTermBorrowings", "CommercialPaper"],
}
FLOWS = {"sales", "net_profit", "depreciation", "cfo", "capex", "cogs", "ebit"}   # over a year, not at a date


def _series(facts: dict, tags: list[str], flow: bool) -> dict[str, float]:
    """{fiscal-year end date: value} from annual 10-K figures, latest filing winning."""
    for t in tags:
        units = (facts.get(t) or {}).get("units") or {}
        rows = units.get("USD") or []
        out: dict[str, tuple[str, float]] = {}
        for x in rows:
            if x.get("form") not in ("10-K", "10-K/A") or x.get("fp") != "FY":
                continue
            if flow:
                if not x.get("start"):
                    continue
                days = (date.fromisoformat(x["end"]) - date.fromisoformat(x["start"])).days
                if not 350 <= days <= 380:      # a full year, not a quarter shown in the 10-K
                    continue
            prev = out.get(x["end"])
            if prev is None or x.get("filed", "") >= prev[0]:
                out[x["end"]] = (x.get("filed", ""), float(x["val"]))
        if out:
            return {k: v for k, (_, v) in out.items()}
    return {}


def _get(cik: str) -> dict | None:
    for attempt in range(3):
        try:
            r = httpx.get(f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json",
                          headers={"User-Agent": SEC_UA}, timeout=60)
            if r.status_code == 200:
                return r.json()
            if r.status_code == 404:
                return None
        except httpx.HTTPError:
            pass
        time.sleep(2 * (attempt + 1))
    return None


def annual(cik: str | None, years: int = 6) -> list[dict]:
    """Newest first. Cached for a week (10-Ks arrive once a year)."""
    if not cik:
        return []
    conn = connect()
    key = f"secfin:{cik}"
    hit = cache_get(conn, key, max_age_days=7)
    if hit is not None:
        return hit
    d = _get(cik)
    facts = ((d or {}).get("facts") or {}).get("us-gaap") or {}
    s = {k: _series(facts, tags, k in FLOWS) for k, tags in TAGS.items()}
    ends = sorted(s["sales"] or s["net_profit"], reverse=True)[:years]
    m = lambda k, e: None if s[k].get(e) is None else s[k][e] / UNIT
    out = []
    for e in ends:
        sales, pat, dep, cfo = m("sales", e), m("net_profit", e), m("depreciation", e), m("cfo", e)
        capex = -m("capex", e) if m("capex", e) is not None else None
        cost = m("cogs", e) or sales
        debt = None
        if any(m(k, e) is not None for k in ("lt_debt", "lt_debt_current", "short_debt")):
            debt = sum(m(k, e) or 0 for k in ("lt_debt", "lt_debt_current", "short_debt"))
        base = (pat or 0) + (dep or 0) if pat is not None else None
        div = lambda a, b, k=1.0: None if a is None or not b else round(a / b * k, 2)
        row = {"fy_end": e, "sales": _r(sales), "net_profit": _r(pat), "depreciation": _r(dep),
               "operating_cash_flow": _r(cfo), "capex": _r(capex),
               "free_cash_flow": _r(cfo + capex) if cfo is not None and capex is not None else None,
               "cfo_to_profit_plus_dep_pct": div(cfo, base, 100) if base and base > 0 else None,   # meaningless on a loss
               "debt_to_equity": div(debt, m("equity", e)),
               "inventory_days": div(m("inventory", e), cost, 365),
               "receivable_days": div(m("receivables", e), sales, 365),
               "payable_days": div(m("payables", e), cost, 365),
               "working_capital_days": div((m("cur_assets", e) or 0) - (m("cur_liab", e) or 0), sales, 365)
               if m("cur_assets", e) is not None and m("cur_liab", e) is not None else None,
               "_equity": m("equity", e), "_debt": debt, "_ebit": m("ebit", e), "_assets": m("assets", e)}
        if None not in (row["inventory_days"], row["receivable_days"], row["payable_days"]):
            row["cash_conversion_cycle_days"] = round(row["inventory_days"] + row["receivable_days"] - row["payable_days"], 1)
        out.append(row)
    cache_put(conn, key, out)
    return out


def upgrade(f: dict, cik: str | None) -> dict:
    """Swap Yahoo's yearly numbers for the SEC's where the SEC has at least 3 years."""
    try:
        a = annual(cik)
    except Exception:
        return f
    if len(a) < 3 or a[0].get("sales") is None:
        return f
    avg = lambda x, y: x if y is None else (x + y) / 2 if x is not None else None
    roe = lambda i: None if i + 1 >= len(a) or a[i]["net_profit"] is None or not avg(a[i]["_equity"], a[i + 1]["_equity"]) \
        else round(a[i]["net_profit"] / avg(a[i]["_equity"], a[i + 1]["_equity"]) * 100, 2)
    cagr = lambda k: None if len(a) < 4 or not a[0][k] or not a[3][k] or a[0][k] <= 0 or a[3][k] <= 0 \
        else round(((a[0][k] / a[3][k]) ** (1 / 3) - 1) * 100, 1)
    roes = [x for x in (roe(0), roe(1), roe(2)) if x is not None]
    cap = lambda i: (a[i]["_equity"] or 0) + (a[i]["_debt"] or 0)
    roce = None
    if a[0]["_ebit"] is not None and len(a) > 1 and (cap(0) + cap(1)):
        roce = round(a[0]["_ebit"] / ((cap(0) + cap(1)) / 2) * 100, 2)
    out = dict(f)
    yahoo = {x.get("fy_end", "")[:7]: x for x in f.get("annual") or []}   # same year-end month
    rows = []
    for x in a:
        row = {k: v for k, v in x.items() if not k.startswith("_")}
        y = yahoo.get(row["fy_end"][:7]) or {}
        for k, v in y.items():                 # a gap in the SEC data: keep Yahoo's figure
            if row.get(k) is None and v is not None and k != "cfo_to_profit_plus_dep_pct":
                row[k] = v
        if y.get("debt_to_equity") is not None:  # debt tags vary by company in XBRL; Yahoo's "Total Debt" is consistent
            row["debt_to_equity"] = y["debt_to_equity"]
        rows.append(row)
    out["annual"] = rows
    out["annual_source"] = "SEC 10-K filings"
    out["roe_pct"] = roe(0) if roe(0) is not None else f.get("roe_pct")
    out["roe_3y_avg_pct"] = round(sum(roes) / len(roes), 2) if roes else f.get("roe_3y_avg_pct")
    out["roce_pct"] = roce if roce is not None else f.get("roce_pct")
    out["sales_cagr_3y_pct"] = cagr("sales") if cagr("sales") is not None else f.get("sales_cagr_3y_pct")
    out["profit_cagr_3y_pct"] = cagr("net_profit") if cagr("net_profit") is not None else f.get("profit_cagr_3y_pct")
    if out.get("pe") and out.get("profit_cagr_3y_pct") and out["profit_cagr_3y_pct"] > 0:
        out["peg"] = round(out["pe"] / out["profit_cagr_3y_pct"], 2)
    return out


def _r(x, n=1):
    return None if x is None else round(x, n)
