"""Runs: the evening screen, a screen started from the site, and one-stock checks.

Every run records its progress as it goes (stage + funnel on the run, status + opinions on each
stock) so the site can show the whole process live. Stocks are analysed in parallel and the AIs
on the council answer at the same time. Every result is kept: the track record starts on day one.
"""
from __future__ import annotations

import concurrent.futures as cf
import json
import logging
import threading
from datetime import datetime, timezone

from . import council
from .config import IS_US
from .exchanges import update as _update_in
from .factsheet import build, render_markdown
from .nse_api import NSE
from .screen import ScreenResult, run_screen, trading_dates
from .settings import get_settings
from .store import connect, ensure_schema

log = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY, day TEXT NOT NULL, kind TEXT NOT NULL,      -- daily | manual | on_demand
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
# Columns added after the first release; ALTER TABLE is a no-op error when they exist.
MIGRATIONS = [
    "ALTER TABLE runs ADD COLUMN stage TEXT",      # download | scan | filter | council | done
    "ALTER TABLE runs ADD COLUMN funnel TEXT",     # {companies, spikes, checked, passed[], rejected[]}
    "ALTER TABLE picks ADD COLUMN status TEXT",    # queued | facts | news | council | done | error
    "ALTER TABLE picks ADD COLUMN seq INTEGER",
]
STAGES = ["download", "scan", "filter", "council", "done"]
PARALLEL_STOCKS = 4
_lock = threading.Lock()


def db():
    conn = connect()
    ensure_schema(conn, "daily", SCHEMA)
    if not getattr(db, "_migrated", False):
        for sql in MIGRATIONS:
            try:
                conn.execute(sql)
            except Exception:
                pass
        conn.commit()
        db._migrated = True
    return conn


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def ist_today() -> str:
    """Today in this market's own time zone (India or New York), as YYYY-MM-DD."""
    from .config import market_today
    return market_today().isoformat()


class NothingToDo(Exception):
    pass


def _start(conn, day: str, kind: str, st: dict, stage: str) -> int:
    cur = conn.execute(
        "INSERT INTO runs(day, kind, started, status, stage, filters, prompt, models) VALUES (?,?,?,?,?,?,?,?)",
        (day, kind, _now(), "running", stage, json.dumps(st["filters"]), st["prompt"], json.dumps(st["models"])))
    conn.commit()
    return cur.lastrowid


def _run_set(conn, run_id: int, **kw) -> None:
    cols = ", ".join(f"{k}=?" for k in kw)
    vals = [json.dumps(v, default=str) if isinstance(v, (dict, list)) else v for v in kw.values()]
    with _lock:
        conn.execute(f"UPDATE runs SET {cols} WHERE id=?", (*vals, run_id))
        conn.commit()


def _pick_set(conn, run_id: int, isin: str, **kw) -> None:
    cols = ", ".join(f"{k}=?" for k in kw)
    vals = [json.dumps(v, default=str) if isinstance(v, (dict, list)) else v for v in kw.values()]
    with _lock:
        conn.execute(f"UPDATE picks SET {cols} WHERE run_id=? AND isin=?", (*vals, run_id, isin))
        conn.commit()


def _funnel(res: ScreenResult) -> dict:
    slim = lambda r: {k: r.get(k) for k in ("isin", "nse_symbol", "bse_symbol", "name", "close", "change_pct",
                                             "spike_x", "delivery_pct", "market_cap_cr", "pe", "roe_pct",
                                             "debt_to_equity", "fails")}
    return {"companies": res.companies, "spikes": res.spikes,
            "checked": len(res.passed) + len(res.rejected),
            "passed": [slim(r) for r in res.passed], "rejected": [slim(r) for r in res.rejected]}


def analyse(run_id: int, isin: str, symbol: str, st: dict) -> float:
    """Fact sheet, shared news, then the whole council at once. Saves each step as it lands."""
    conn = db()
    try:
        _pick_set(conn, run_id, isin, status="facts")
        sheet = build(symbol, nse=NSE())
        md = render_markdown(sheet)
        _pick_set(conn, run_id, isin, status="news", name=sheet["name"], price=sheet["technicals"]["close"],
                  sheet=sheet, sheet_md=md)
        res = council.run(
            sheet, md, prompt=st["prompt"], members=st["models"],
            on_news=lambda n: _pick_set(conn, run_id, isin, status="council", news=n),
            on_opinion=lambda ops: _pick_set(conn, run_id, isin, opinions=ops, verdict=council.tally(
                [o for o in ops if not o.get("pending")])))
        _pick_set(conn, run_id, isin, status="done", opinions=res["opinions"], verdict=res["verdict"], error=None)
        return res["cost_usd"]
    except Exception as e:
        log.exception("%s failed", symbol)
        _pick_set(conn, run_id, isin, status="error", error=str(e)[:500])
        return 0.0


def _council_stage(conn, run_id: int, rows: list[dict], st: dict) -> float:
    with _lock:
        for i, r in enumerate(rows):
            conn.execute(
                "INSERT OR REPLACE INTO picks(run_id, isin, symbol, name, price, status, seq) VALUES (?,?,?,?,?,?,?)",
                (run_id, r["isin"], r["symbol"], r.get("name"), r.get("close"), "queued", i))
        conn.commit()
    with cf.ThreadPoolExecutor(max_workers=PARALLEL_STOCKS) as ex:
        costs = list(ex.map(lambda r: analyse(run_id, r["isin"], r["symbol"], st), rows))
    return round(sum(costs), 4)


def run_screen_job(kind: str = "daily", max_stocks: int = 20, require_today: bool = False,
                   run_id: int | None = None) -> int:
    """The whole screen: download → volume scan → fundamentals → council.

    kind 'daily' (timer) skips if today's files are not out or today already ran; 'manual' (the
    site's button) always runs. Pass run_id to continue a run row created by the site."""
    conn = db()
    st = get_settings(conn)
    if run_id is None:
        run_id = _start(conn, ist_today(), kind, st, "download")
    try:
        if IS_US:
            from .us_data import update as _update_us
            _update_us(history_days=10)
        else:
            _update_in(history_days=10)
        latest = trading_dates(conn, 1)[0]
        if require_today and latest != ist_today():
            raise NothingToDo(f"no exchange data for {ist_today()} yet (latest {latest})")
        if not IS_US:   # NSE splits, announcements, results-day refresh (US: per stock, in its fact sheet)
            from .upkeep import before_screen
            before_screen()
        if kind == "daily" and conn.execute(
                "SELECT 1 FROM runs WHERE kind='daily' AND day=? AND status='done' AND id!=?", (latest, run_id)).fetchone():
            raise NothingToDo(f"daily run for {latest} already done")
        _run_set(conn, run_id, day=latest, stage="scan")

        def progress(res: ScreenResult):
            _run_set(conn, run_id, stage="filter", spikes=res.spikes, funnel=_funnel(res))

        res = run_screen(st["filters"], on_progress=progress)
        _run_set(conn, run_id, stage="council", spikes=res.spikes, funnel=_funnel(res))
        rows = [{**r, "symbol": r["nse_symbol"] or r["bse_code"]} for r in res.passed[:max_stocks]]
        cost = _council_stage(conn, run_id, rows, st)
        _run_set(conn, run_id, status="done", stage="done", finished=_now(), cost_usd=cost)
    except NothingToDo:
        with _lock:
            conn.execute("DELETE FROM runs WHERE id=?", (run_id,))
            conn.commit()
        raise
    except Exception as e:
        _run_set(conn, run_id, status="failed", finished=_now(), error=str(e)[:500])
        raise
    return run_id


def run_daily(max_stocks: int = 20, require_today: bool = False) -> int:
    return run_screen_job("daily", max_stocks, require_today)


def start_run(kind: str, symbol: str | None = None) -> int:
    """Create the run row now (so the site can show it at once); the caller runs it in a thread."""
    conn = db()
    st = get_settings(conn)
    day = trading_dates(conn, 1)[0] if kind in ("on_demand", "custom") else ist_today()
    return _start(conn, day, kind, st, "council" if kind in ("on_demand", "custom") else "download")


def run_one(symbol: str, run_id: int | None = None) -> int:
    conn = db()
    st = get_settings(conn)
    if run_id is None:
        run_id = _start(conn, trading_dates(conn, 1)[0], "on_demand", st, "council")
    symbol = symbol.upper()
    try:
        from .factsheet import _isin_for
        isin = _isin_for(conn, symbol)
        cost = _council_stage(conn, run_id, [{"isin": isin, "symbol": symbol, "name": symbol}], st)
        failed = conn.execute("SELECT error FROM picks WHERE run_id=? AND status='error'", (run_id,)).fetchone()
        if failed:
            raise RuntimeError(failed["error"])
        _run_set(conn, run_id, status="done", stage="done", finished=_now(), cost_usd=cost)
    except Exception as e:
        _run_set(conn, run_id, status="failed", stage="done", finished=_now(), error=str(e)[:500])
        raise
    return run_id


def retry_failed(run_id: int) -> int:
    """Re-run the stocks of a run that failed (network, lock, model outage)."""
    conn = db()
    st = get_settings(conn)
    rows = [dict(r) for r in conn.execute(
        "SELECT isin, symbol FROM picks WHERE run_id=? AND (error IS NOT NULL OR status='error')", (run_id,))]
    if rows:
        cost = _council_stage(conn, run_id, rows, st)
        _run_set(conn, run_id, cost_usd=(conn.execute("SELECT cost_usd FROM runs WHERE id=?", (run_id,)).fetchone()[0] or 0) + cost)
    return len(rows)


def run_custom(run_id: int, items: list[dict]) -> int:
    """The council on stocks picked by hand (e.g. from the Screener)."""
    conn = db()
    st = get_settings(conn)
    try:
        cost = _council_stage(conn, run_id, items, st)
        _run_set(conn, run_id, status="done", stage="done", finished=_now(), cost_usd=cost)
    except Exception as e:
        _run_set(conn, run_id, status="failed", stage="done", finished=_now(), error=str(e)[:500])
        raise
    return run_id


SCREEN_HITS = """
CREATE TABLE IF NOT EXISTS screen_hits (
    screen_id TEXT NOT NULL, day TEXT NOT NULL, isin TEXT NOT NULL, symbol TEXT, name TEXT,
    PRIMARY KEY (screen_id, day, isin)
);
"""


def run_saved_screens() -> dict:
    """Evening: run every saved screen marked 'daily' and keep its matches (no AI, no cost)."""
    from .universe import run_rules
    conn = db()
    ensure_schema(conn, "screen_hits", SCREEN_HITS)
    screens = [s for s in (get_settings(conn).get("screens") or []) if s.get("daily")]
    out = {}
    for s in screens:
        res = run_rules(s["rules"], conn)
        with _lock:
            conn.executemany("INSERT OR REPLACE INTO screen_hits VALUES (?,?,?,?,?)",
                             [(s["id"], res["day"], h["isin"], h.get("symbol"), h.get("name")) for h in res["hits"]])
            conn.commit()
        out[s["name"]] = len(res["hits"])
    return out


def saved_screen_history(conn, screen_id: str) -> dict:
    ensure_schema(conn, "screen_hits", SCREEN_HITS)
    days = [r[0] for r in conn.execute(
        "SELECT DISTINCT day FROM screen_hits WHERE screen_id=? ORDER BY day DESC LIMIT 2", (screen_id,))]
    if not days:
        return {}
    now = {r[0] for r in conn.execute("SELECT isin FROM screen_hits WHERE screen_id=? AND day=?", (screen_id, days[0]))}
    before = {r[0] for r in conn.execute("SELECT isin FROM screen_hits WHERE screen_id=? AND day=?", (screen_id, days[1]))} if len(days) > 1 else set()
    return {"day": days[0], "count": len(now), "new": sorted(now - before) if before else []}


ORDERS = """
CREATE TABLE IF NOT EXISTS orders (
    id INTEGER PRIMARY KEY, created TEXT NOT NULL, run_id INTEGER, isin TEXT, symbol TEXT, name TEXT,
    side TEXT NOT NULL, account TEXT, lines TEXT NOT NULL, subject TEXT
);
"""


def orders_db():
    conn = db()
    ensure_schema(conn, "orders", ORDERS)
    return conn


def log_order(run_id, isin, symbol, name, side, account, lines, subject) -> int:
    """Remember an order Dad prepared (we can't know whether he pressed Send in Gmail)."""
    conn = orders_db()
    with _lock:
        cur = conn.execute("INSERT INTO orders(created, run_id, isin, symbol, name, side, account, lines, subject) "
                           "VALUES (?,?,?,?,?,?,?,?,?)",
                           (_now(), run_id, isin, symbol, name, side, account, json.dumps(lines), subject))
        conn.commit()
    return cur.lastrowid
