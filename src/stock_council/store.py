"""SQLite storage: daily exchange rows, download log, and cached per-stock data."""
from __future__ import annotations

import json
import sqlite3
from datetime import date

from .config import DATA_DIR, DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS daily (
    date TEXT NOT NULL, exch TEXT NOT NULL, isin TEXT NOT NULL,
    symbol TEXT, code TEXT, series TEXT, name TEXT,
    open REAL, high REAL, low REAL, close REAL, prev_close REAL,
    volume INTEGER, turnover REAL, deliv_qty INTEGER,
    PRIMARY KEY (date, exch, isin)
);
CREATE INDEX IF NOT EXISTS daily_isin ON daily(isin, date);
CREATE INDEX IF NOT EXISTS daily_code ON daily(date, exch, code);
CREATE INDEX IF NOT EXISTS daily_sym ON daily(date, exch, symbol, series);
CREATE TABLE IF NOT EXISTS fetched (
    date TEXT NOT NULL, kind TEXT NOT NULL, status TEXT NOT NULL,
    PRIMARY KEY (date, kind)
);
CREATE TABLE IF NOT EXISTS cache (
    key TEXT PRIMARY KEY, day TEXT NOT NULL, value TEXT NOT NULL
);
"""


def connect() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SCHEMA)
    return conn


def cache_get(conn: sqlite3.Connection, key: str, max_age_days: int = 0):
    row = conn.execute("SELECT day, value FROM cache WHERE key=?", (key,)).fetchone()
    if not row:
        return None
    if (date.today() - date.fromisoformat(row["day"])).days > max_age_days:
        return None
    return json.loads(row["value"])


def cache_put(conn: sqlite3.Connection, key: str, value) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO cache(key, day, value) VALUES (?,?,?)",
        (key, date.today().isoformat(), json.dumps(value, default=str)),
    )
    conn.commit()
