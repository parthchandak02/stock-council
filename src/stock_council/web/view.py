"""Turns a fact sheet into the few numbers worth a glance, each marked good / watch / bad."""
from __future__ import annotations

from ..council import VOTE_GROUP

FILTER_LABELS = {
    "volume_spike_multiple": ("Volume spike", "1-week average volume above {v:g}× the 1-year average"),
    "min_volume": ("Traded today", "more than {v:,.0f} shares traded today"),
    "min_market_cap_cr": ("Size", "market cap above ₹{v:,.0f} Cr"),
    "max_pe": ("Price", "P/E below {v:g}"),
    "min_roe_pct": ("Returns", "ROE above {v:g}%"),
    "max_debt_to_equity": ("Debt", "debt / equity below {v:g}"),
}

SHORT = {"Gemini": "Ge", "Claude": "Cl", "ChatGPT": "GPT", "DeepSeek": "DS", "Llama": "Ll", "Qwen": "Qw", "Grok": "Gk"}


def short_name(name: str) -> str:
    return SHORT.get(name, (name or "?")[:2])


def _tone(value, good, bad, higher_is_better=True):
    if value is None:
        return "na"
    if higher_is_better:
        return "good" if value >= good else ("bad" if value < bad else "warn")
    return "good" if value <= good else ("bad" if value > bad else "warn")


def _pct(x, d=1):
    return "-" if x is None else f"{x:.{d}f}%"


def _x(x, d=2):
    return "-" if x is None else f"{x:.{d}f}"


def key_numbers(sheet: dict) -> list[dict]:
    """The nine checks in plain words, grouped like Dad's prompt."""
    f = sheet.get("fundamentals") or {}
    t = sheet.get("technicals") or {}
    annual = f.get("annual") or [{}]
    a0 = annual[0] if annual else {}
    sh = sheet.get("shareholding") or []
    pl = sheet.get("pledge") or {}
    out = []

    lender = f.get("lender")
    NA = "Lender: deposits and loans are its raw material"
    conv = a0.get("cfo_to_profit_plus_dep_pct")
    if lender:
        out.append({"group": "Cash", "label": "Profit turning into cash", "value": "Not used", "tone": "na", "hint": NA})
    else:
        out.append({"group": "Cash", "label": "Profit turning into cash", "value": _pct(conv, 0),
                    "tone": _tone(conv, 80, 50), "hint": f"Operating cash ÷ (profit + depreciation) · FY{(a0.get('fy_end') or '')[2:4]}"})
    fcf = a0.get("free_cash_flow")
    neg_years = sum(1 for a in annual[:3] if (a.get("free_cash_flow") or 0) < 0)
    out.append({"group": "Cash", "label": "Free cash flow", "value": "-" if fcf is None else f"₹{fcf:,.0f} Cr",
                "tone": "na" if fcf is None else ("good" if neg_years == 0 else "bad" if neg_years >= 2 else "warn"),
                "hint": f"Negative {neg_years} of last 3 years" if neg_years else "Positive all of last 3 years"})

    de = f.get("debt_to_equity")
    old_de = annual[2].get("debt_to_equity") if len(annual) > 2 else None
    trend = "" if de is None or old_de is None else (" · falling" if de < old_de - 0.05 else " · rising" if de > old_de + 0.05 else " · steady")
    if lender:
        out.append({"group": "Debt", "label": "Debt / equity", "value": "Not used", "tone": "na", "hint": NA})
    else:
        out.append({"group": "Debt", "label": "Debt / equity", "value": _x(de) + trend, "tone": _tone(de, 0.5, 1.0, False),
                    "hint": f"vs {_x(old_de)} two years earlier"})
    wc = a0.get("cash_conversion_cycle_days")
    if not lender:
        out.append({"group": "Debt", "label": "Cash cycle", "value": "-" if wc is None else f"{wc:.0f} days",
                    "tone": _tone(wc, 60, 120, False), "hint": "Days cash is tied up in stock and dues"})

    pledge = pl.get("pledged_pct_of_promoter_shares")
    out.append({"group": "Owners", "label": "Promoter shares pledged", "value": _pct(pledge) if pl and pledge else "None",
                "tone": "good" if not pl or not pledge else _tone(pledge, 0, 10, False),
                "hint": f"= {_pct(pl.get('pledged_pct_of_total_shares'))} of all shares" if pl and pledge else "No pledge disclosed"})
    if len(sh) >= 2:
        new, old = sh[0], sh[-1]
        dp = (new.get("promoter") or 0) - (old.get("promoter") or 0)
        inst_new = (new.get("fii") or 0) + (new.get("dii") or 0)
        inst_old = (old.get("fii") or 0) + (old.get("dii") or 0)
        di = inst_new - inst_old
        out.append({"group": "Owners", "label": "Promoter stake", "value": f"{new.get('promoter', 0):.1f}% ({dp:+.1f})",
                    "tone": "good" if dp >= 0 else ("warn" if dp > -1 else "bad"), "hint": f"Change since {old.get('quarter', '')[:6]}"})
        out.append({"group": "Owners", "label": "Funds (FII + DII)", "value": f"{inst_new:.1f}% ({di:+.1f})",
                    "tone": "good" if di > 0.5 else ("bad" if di < -1 else "warn"), "hint": f"Change since {old.get('quarter', '')[:6]}"})

    dl, dla = t.get("delivery_pct_today"), t.get("delivery_pct_30d_avg")
    out.append({"group": "Trading", "label": "Delivery today", "value": _pct(dl, 0) + ("" if dla is None else f" vs {dla:.0f}%"),
                "tone": "na" if dl is None or not dla else ("good" if dl >= dla * 0.9 else "bad" if dl < dla * 0.6 else "warn"),
                "hint": "vs 30-day average · low = day trading"})
    rsi = t.get("rsi_14")
    out.append({"group": "Trading", "label": "RSI (14 days)", "value": _x(rsi, 0),
                "tone": "na" if rsi is None else ("warn" if rsi > 70 or rsi < 30 else "good"),
                "hint": "Over 70 overbought · under 30 oversold"})
    obv = t.get("obv_change_3m_vs_avg_daily_volume")
    out.append({"group": "Trading", "label": "3-month money flow", "value": "-" if obv is None else ("Accumulation" if obv > 0 else "Distribution"),
                "tone": "na" if obv is None else ("good" if obv > 0 else "bad"), "hint": "On-balance volume trend"})

    g = f.get("sales_cagr_3y_pct")
    out.append({"group": "Growth", "label": "Sales growth (3 yr)", "value": _pct(g), "tone": _tone(g, 15, 8), "hint": "Yearly average"})
    pe, roe, roce = f.get("pe"), f.get("roe_pct"), f.get("roce_pct")
    out.append({"group": "Value", "label": "P/E", "value": _x(pe, 1), "tone": _tone(pe, 25, 40, False), "hint": f"P/B {_x(f.get('pb'))} · PEG {_x(f.get('peg'))}"})
    out.append({"group": "Value", "label": "ROE", "value": _pct(roe), "tone": _tone(roe, 15, 12), "hint": f"3-yr avg {_pct(f.get('roe_3y_avg_pct'))}"})
    out.append({"group": "Value", "label": "ROCE", "value": _pct(roce), "tone": _tone(roce, 15, 10), "hint": f"ROA {_pct(f.get('roa_pct'))}"})
    return out


def why(p: dict) -> dict:
    """The majority camp's main risk and what would change their mind."""
    v = p.get("verdict") or {}
    ops = [o for o in (p.get("opinions") or []) if not o.get("error") and not o.get("pending")]
    camp = [o for o in ops if VOTE_GROUP.get(str(o.get("verdict", "")).lower()) == v.get("lean")] or ops
    pick = lambda k: next((o.get(k) for o in camp if o.get(k)), None)
    return {"risk": pick("key_risk"), "trigger": pick("trigger_to_change_view"), "entry": pick("entry_zone")}


def short_fail(s: str) -> str:
    """'ROE 7.06% ≤ 12.0%' -> 'ROE 7.1%' and friends, for the rejected list."""
    import re
    if s.startswith("market cap"):
        m = re.search(r"market cap ([\d.]+|None)", s)
        return "Small" + (f" (₹{float(m.group(1)):,.0f} Cr)" if m and m.group(1) != "None" else "")
    if "loss" in s:
        return "Loss-making"
    if s.startswith("P/E"):
        return "P/E " + s.split()[1].split(".")[0]
    if s.startswith("ROE"):
        return "ROE " + f"{float(s.split()[1].rstrip('%')):.1f}%"
    if s.startswith("D/E"):
        v = s.split()[1]
        return "Debt " + ("?" if v == "None" else f"{float(v):.1f}×")
    if "financials" in s:
        return "No financials"
    return s[:30]


SMALL_WORDS = {"AND": "and", "OF": "of", "THE": "the", "&": "&"}
DROP = {"LIMITED", "LTD", "LTD.", "LIMIT", "LIMI", "LIM"}


def nice_name(name: str | None) -> str:
    """'APL APOLLO TUBES LTD.' -> 'APL Apollo Tubes'; leaves mixed-case names alone."""
    if not name:
        return ""
    words = name.split()
    while len(words) > 1 and words[-1].upper().rstrip(".") in {d.rstrip(".") for d in DROP}:
        words.pop()
    if name != name.upper():
        return " ".join(words)
    out = []
    for i, w in enumerate(words):
        if w in SMALL_WORDS and i:
            out.append(SMALL_WORDS[w])
        elif len(w) <= 3 and w.isalpha():
            out.append(w)  # acronyms like APL, HMA, NDR
        else:
            out.append("-".join(p.capitalize() for p in w.split("-")))
    return " ".join(out)
