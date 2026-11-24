"""The evening job and on-demand runs. Every result is saved (the track record starts on day one)."""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from . import council
from .exchanges import update
from .factsheet import build, render_markdown
from .nse_api import NSE
from .screen import run_screen
from .settings import get_settings
from .store import connect, ensure_schema

log = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY, day TEXT NOT NULL, kind TEXT NOT NULL,      -- kind: daily | on_demand
    started TEXT NOT NULL, finished TEXT, status TEXT NOT NULL,         -- running | done | failed
    filters TEXT, prompt TEXT, models TEXT, spikes INTEGER, cost_usd REAL, error TEXT,
    emailed TEXT
);
CREATE TABLE IF NOT EXISTS picks (
    run_id INTEGER NOT NULL, isin TEXT NOT NULL, symbol TEXT, name TEXT, price REAL,
    sheet TEXT, sheet_md TEXT, news TEXT, opinions TEXT, verdict TEXT, error TEXT,
    PRIMARY KEY (run_id, isin)
);
"""


def db():
    conn = connect()
    ensure_schema(conn, "daily", SCHEMA)
    return conn


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _start(conn, day: str, kind: str, st: dict) -> int:
    cur = conn.execute(
        "INSERT INTO runs(day, kind, started, status, filters, prompt, models) VALUES (?,?,?,?,?,?,?)",
        (day, kind, _now(), "running", json.dumps(st["filters"]), st["prompt"], json.dumps(st["models"])))
    conn.commit()
    return cur.lastrowid


def analyse(conn, run_id: int, symbol: str, st: dict, nse: NSE) -> dict:
    sheet = build(symbol, nse=nse)
    md = render_markdown(sheet)
    res = council.run(sheet, md, prompt=st["prompt"], members=st["models"])
    conn.execute(
        "INSERT OR REPLACE INTO picks VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (run_id, sheet["isin"], sheet.get("nse_symbol") or sheet.get("bse_symbol"), sheet["name"],
         sheet["technicals"]["close"], json.dumps(sheet, default=str), md, json.dumps(res["news"]),
         json.dumps(res["opinions"]), json.dumps(res["verdict"]), None))
    conn.commit()
    return res


class NothingToDo(Exception):
    pass


def ist_today() -> str:
    from .exchanges import ist_today as d
    return d().isoformat()


def run_daily(max_stocks: int = 20, require_today: bool = False) -> int:
    """The evening job. With require_today it is safe to schedule twice an evening: it does nothing
    if today's exchange files are not out yet (or it is a holiday), or if today already ran."""
    conn = db()
    st = get_settings(conn)
    update(history_days=10)
    from .screen import trading_dates
    latest = trading_dates(conn, 1)[0]
    if require_today and latest != ist_today():
        raise NothingToDo(f"no exchange data for {ist_today()} yet (latest {latest})")
    if conn.execute("SELECT 1 FROM runs WHERE kind='daily' AND day=? AND status='done'", (latest,)).fetchone():
        raise NothingToDo(f"daily run for {latest} already done")
    screen = run_screen(st["filters"])
    run_id = _start(conn, screen.date, "daily", st)
    conn.execute("UPDATE runs SET spikes=? WHERE id=?", (screen.spikes, run_id))
    nse, cost = NSE(), 0.0
    try:
        for row in screen.passed[:max_stocks]:
            sym = row["nse_symbol"] or row["bse_code"]
            try:
                cost += analyse(conn, run_id, sym, st, nse)["cost_usd"]
            except Exception as e:
                log.exception("%s failed", sym)
                conn.execute("INSERT OR REPLACE INTO picks(run_id, isin, symbol, name, price, error) VALUES (?,?,?,?,?,?)",
                             (run_id, row["isin"], sym, row["name"], row["close"], str(e)[:500]))
                conn.commit()
        conn.execute("UPDATE runs SET status='done', finished=?, cost_usd=? WHERE id=?", (_now(), round(cost, 4), run_id))
    except Exception as e:
        conn.execute("UPDATE runs SET status='failed', finished=?, error=? WHERE id=?", (_now(), str(e)[:500], run_id))
        raise
    finally:
        conn.commit()
    return run_id


def run_one(symbol: str) -> int:
    conn = db()
    st = get_settings(conn)
    from .screen import trading_dates
    run_id = _start(conn, trading_dates(conn, 1)[0], "on_demand", st)
    try:
        res = analyse(conn, run_id, symbol.upper(), st, NSE())
        conn.execute("UPDATE runs SET status='done', finished=?, cost_usd=? WHERE id=?", (_now(), res["cost_usd"], run_id))
    except Exception as e:
        conn.execute("UPDATE runs SET status='failed', finished=?, error=? WHERE id=?", (_now(), str(e)[:500], run_id))
        raise
    finally:
        conn.commit()
    return run_id


def retry_failed(run_id: int) -> int:
    """Re-run the council for stocks of a run that failed (network, lock, model outage)."""
    conn = db()
    st = get_settings(conn)
    nse, n = NSE(), 0
    for r in conn.execute("SELECT symbol FROM picks WHERE run_id=? AND error IS NOT NULL", (run_id,)).fetchall():
        try:
            res = analyse(conn, run_id, r["symbol"], st, nse)
            conn.execute("UPDATE runs SET cost_usd=coalesce(cost_usd,0)+? WHERE id=?", (res["cost_usd"], run_id))
            conn.commit()
            n += 1
        except Exception:
            log.exception("%s failed again", r["symbol"])
    return n
