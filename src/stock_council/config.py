"""Paths and default settings. Secrets never live in this repo: they come from the environment."""
from __future__ import annotations

import os
from pathlib import Path

DATA_DIR = Path(os.environ.get("SC_DATA_DIR", Path.home() / ".local/share/stock-council"))
DB_PATH = DATA_DIR / "stock-council.db"

# Dad's Screener query, as written in his email (Nov 2026):
#   Market Capitalization > 500 AND Volume > 10000 AND Price to Earning < 40 AND
#   Return on Equity > 12 AND Debt to equity < 1 AND Volume 1week average > Volume 1year average * 6
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
