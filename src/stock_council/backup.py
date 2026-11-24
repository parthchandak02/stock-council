"""Nightly backup of what can't be re-downloaded: settings, runs and AI verdicts (the track record),
broker orders, saved-screen history. Market data (a year of NSE/BSE files, fundamentals, ownership,
announcements) is rebuilt by `stock-council update` / `universe` / `ownership` and is not backed up.
"""
from __future__ import annotations

import gzip
import os
import shutil
import sqlite3
from datetime import date
from pathlib import Path

from .config import DB_PATH, MARKET

PREFIX = "stock-council" if MARKET["code"] == "in" else f"stock-council-{MARKET['code']}"

KEEP = ["settings", "runs", "picks", "orders", "screen_hits"]
DEFAULT_DIR = "/srv/backups/stock-council"


def run(out_dir: str | None = None, keep_days: int = 30) -> dict:
    out = Path(out_dir or os.environ.get("SC_BACKUP_DIR", DEFAULT_DIR))
    out.mkdir(parents=True, exist_ok=True)
    tmp = out / f".{PREFIX}-{date.today():%Y-%m-%d}.sqlite"
    tmp.unlink(missing_ok=True)
    src = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True, timeout=60)
    src.execute("PRAGMA busy_timeout=60000")
    dst = sqlite3.connect(tmp)
    tables = {r[0] for r in src.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    rows = {}
    for t in KEEP:
        if t not in tables:
            continue
        ddl = src.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (t,)).fetchone()[0]
        dst.execute(ddl)
        cur = src.execute(f"SELECT * FROM {t}")
        cols = len(cur.description)
        data = cur.fetchall()
        dst.executemany(f"INSERT INTO {t} VALUES ({','.join('?' * cols)})", data)
        rows[t] = len(data)
    dst.commit()
    dst.execute("VACUUM")
    dst.close()
    src.close()
    final = out / f"{PREFIX}-{date.today():%Y-%m-%d}.sqlite.gz"
    with open(tmp, "rb") as f, gzip.open(final, "wb", compresslevel=9) as g:
        shutil.copyfileobj(f, g)
    tmp.unlink()
    os.chmod(final, 0o640)
    # keep the last `keep_days` files
    olds = sorted(out.glob(f"{PREFIX}-2*.sqlite.gz"))[:-keep_days]
    for o in olds:
        o.unlink()
    return {"file": str(final), "bytes": final.stat().st_size, "rows": rows, "removed": len(olds)}


def restore(gz_path: str) -> None:
    """Put the kept tables back into the live database (market data is re-downloaded separately)."""
    raw = Path(gz_path).with_suffix("")
    with gzip.open(gz_path, "rb") as g, open(raw, "wb") as f:
        shutil.copyfileobj(g, f)
    from .daily import db, orders_db
    from .settings import get_settings
    conn = db()
    orders_db()
    get_settings(conn)
    conn.execute(f"ATTACH DATABASE '{raw}' AS b")
    for t in KEEP:
        if conn.execute("SELECT 1 FROM b.sqlite_master WHERE name=?", (t,)).fetchone():
            conn.execute(f"INSERT OR REPLACE INTO main.{t} SELECT * FROM b.{t}")
    conn.commit()
    conn.execute("DETACH DATABASE b")
    raw.unlink()
