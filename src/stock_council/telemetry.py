"""Cost and token log for every AI call (OpenRouter returns the exact cost of each call in `usage.cost`).

Always recorded (it is tiny); whether the site *shows* costs is a setting (Settings → Costs).
"""
from __future__ import annotations

import time

from .store import connect, ensure_schema

SCHEMA = """
CREATE TABLE IF NOT EXISTS ai_calls (
    id INTEGER PRIMARY KEY, at TEXT NOT NULL, model TEXT NOT NULL, purpose TEXT,
    run_id INTEGER, isin TEXT, symbol TEXT,
    prompt_tokens INTEGER, completion_tokens INTEGER, reasoning_tokens INTEGER,
    cost REAL, seconds REAL, ok INTEGER, error TEXT
);
CREATE INDEX IF NOT EXISTS ai_calls_at ON ai_calls(at);
CREATE INDEX IF NOT EXISTS ai_calls_run ON ai_calls(run_id, isin);
"""


def db():
    conn = connect()
    ensure_schema(conn, "ai_calls", SCHEMA)
    return conn


def log_call(model: str, purpose: str, ctx: dict | None, usage: dict | None, seconds: float, error: str | None = None):
    u = usage or {}
    details = u.get("completion_tokens_details") or {}
    ctx = ctx or {}
    try:
        conn = db()
        conn.execute(
            "INSERT INTO ai_calls (at, model, purpose, run_id, isin, symbol, prompt_tokens, completion_tokens, "
            "reasoning_tokens, cost, seconds, ok, error) VALUES (datetime('now'),?,?,?,?,?,?,?,?,?,?,?,?)",
            (model, purpose, ctx.get("run_id"), ctx.get("isin"), ctx.get("symbol"), u.get("prompt_tokens"),
             u.get("completion_tokens"), details.get("reasoning_tokens"), u.get("cost"), round(seconds, 2),
             0 if error else 1, (error or "")[:300] or None))
        conn.commit()
    except Exception:
        pass   # telemetry must never break a run


def calls_for(run_id: int, isin: str | None = None) -> list[dict]:
    q = "SELECT * FROM ai_calls WHERE run_id=?" + (" AND isin=?" if isin else "") + " ORDER BY id"
    return [dict(r) for r in db().execute(q, (run_id, isin) if isin else (run_id,))]


def summary(days: int = 30) -> dict:
    conn = db()
    since = f"-{days} days"
    by_model = [dict(r) for r in conn.execute(
        "SELECT model, count(*) calls, sum(ok=0) failed, sum(prompt_tokens) tin, sum(completion_tokens) tout, "
        "sum(reasoning_tokens) treason, round(sum(cost), 4) cost, round(avg(cost), 5) avg_cost, round(avg(seconds), 1) avg_s "
        "FROM ai_calls WHERE at >= datetime('now', ?) GROUP BY model ORDER BY cost DESC", (since,))]
    total = sum(r["cost"] or 0 for r in by_model)
    one = lambda q, *a: conn.execute(q, a).fetchone()[0] or 0
    month = one("SELECT sum(cost) FROM ai_calls WHERE at >= strftime('%Y-%m-01','now')")
    today = one("SELECT sum(cost) FROM ai_calls WHERE at >= date('now')")
    stocks = one("SELECT count(DISTINCT run_id || isin) FROM ai_calls WHERE isin IS NOT NULL AND at >= datetime('now', ?)", since)
    days_rows = [dict(r) for r in conn.execute(
        "SELECT date(at) day, round(sum(cost), 4) cost, count(*) calls FROM ai_calls "
        "WHERE at >= datetime('now', ?) GROUP BY day ORDER BY day", (since,))]
    return {"by_model": by_model, "total": round(total, 4), "month": round(month, 4), "today": round(today, 4),
            "stocks": stocks, "per_stock": round(total / stocks, 4) if stocks else None, "days": days_rows}


_key_cache: dict = {}


def openrouter_key() -> dict | None:
    """Spend and cap of the OpenRouter key itself (GET /api/v1/key), cached for 10 minutes."""
    if _key_cache.get("at", 0) > time.time() - 600:
        return _key_cache.get("data")
    try:
        import httpx
        from .council import _key
        r = httpx.get("https://openrouter.ai/api/v1/key", headers={"Authorization": f"Bearer {_key()}"}, timeout=15)
        d = r.json().get("data") if r.status_code == 200 else None
    except Exception:
        d = None
    _key_cache.update(at=time.time(), data=d)
    return d


def backfill_from_picks() -> int:
    """One-off: costs of runs made before this log existed, from each stock's saved opinions."""
    import json
    conn = db()
    if conn.execute("SELECT 1 FROM ai_calls LIMIT 1").fetchone():
        return 0
    n = 0
    try:
        rows = conn.execute("SELECT p.run_id, p.isin, p.symbol, p.opinions, p.news, r.started FROM picks p "
                            "JOIN runs r ON r.id=p.run_id WHERE p.opinions IS NOT NULL").fetchall()
    except Exception:
        return 0
    for r in rows:
        at = (r["started"] or "")[:19].replace("T", " ")
        news = json.loads(r["news"] or "{}")
        items = [("deepseek/deepseek-v4.1-flash", "news", news.get("cost"), None)] if news.get("cost") is not None else []
        items += [(o.get("model"), o.get("name"), o.get("cost"), o.get("seconds")) for o in json.loads(r["opinions"])
                  if not o.get("pending")]
        for model, purpose, cost, secs in items:
            conn.execute("INSERT INTO ai_calls (at, model, purpose, run_id, isin, symbol, cost, seconds, ok) "
                         "VALUES (?,?,?,?,?,?,?,?,?)", (at, model, purpose, r["run_id"], r["isin"], r["symbol"], cost, secs,
                                                     1 if cost is not None else 0))
            n += 1
    conn.commit()
    return n
