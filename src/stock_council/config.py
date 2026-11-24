"""Paths and default settings. Secrets never live in this repo: they come from the environment."""
from __future__ import annotations

import os
from pathlib import Path

# One process serves one market. India (Dad's) is the default; the US copy runs with SC_MARKET=us
# and is served under /us on the same site. Each market has its own database.
MARKETS = {
    "in": {"code": "in", "name": "India", "flag": "🇮🇳", "cur": "₹", "unit": 1e7, "unit_label": "Cr",
           "tz": "Asia/Kolkata", "db": "stock-council.db", "base": "", "exchanges": "NSE & BSE",
           "id_label": "NSE", "close": "3:30 pm", "default_time": "20:15"},
    "us": {"code": "us", "name": "US", "flag": "🇺🇸", "cur": "$", "unit": 1e6, "unit_label": "M",
           "tz": "America/New_York", "db": "us.db", "base": "/us", "exchanges": "NYSE & Nasdaq",
           "id_label": "Ticker", "close": "4:00 pm", "default_time": "17:30"},
}
MARKET = MARKETS[os.environ.get("SC_MARKET", "in")]
IS_US = MARKET["code"] == "us"
PRIMARY_EXCH = "US" if IS_US else "NSE"   # the exchange whose calendar and prices lead

DATA_DIR = Path(os.environ.get("SC_DATA_DIR", Path.home() / ".local/share/stock-council"))
DB_PATH = DATA_DIR / MARKET["db"]

# Dad's Screener query, as written in his email (Nov 2026):
#   Market Capitalization > 500 AND Volume > 10000 AND Price to Earning < 40 AND
#   Return on Equity > 12 AND Debt to equity < 1 AND Volume 1week average > Volume 1year average * 6
DEFAULT_FILTERS_US = {   # Dad's rules, sized for the US ($ millions; "min_volume" is $ traded today)
    "min_market_cap_cr": 300.0,
    "min_volume": 1_000_000,
    "max_pe": 40.0,
    "min_roe_pct": 12.0,
    "max_debt_to_equity": 1.0,
    "volume_spike_multiple": 6.0,
}

DEFAULT_FILTERS = {
    "min_market_cap_cr": 500.0,
    "min_volume": 10_000,
    "max_pe": 40.0,
    "min_roe_pct": 12.0,
    "max_debt_to_equity": 1.0,
    "volume_spike_multiple": 6.0,
}

# Screener-style windows (inferred: 5 trading days = 1 week, ~250 = 1 year).
WEEK_DAYS = 5
YEAR_DAYS = 250

if IS_US:
    DEFAULT_FILTERS = DEFAULT_FILTERS_US


def market_today():
    """Today's date where this market trades (the server runs on US Pacific time)."""
    from datetime import datetime
    from zoneinfo import ZoneInfo
    return datetime.now(ZoneInfo(MARKET["tz"])).date()


def market_now():
    from datetime import datetime
    from zoneinfo import ZoneInfo
    return datetime.now(ZoneInfo(MARKET["tz"]))
