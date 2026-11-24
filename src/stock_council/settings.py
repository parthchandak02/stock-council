"""Settings Dad can edit on the site: screen filters, the prompt, and which models sit on the council."""
from __future__ import annotations

import json

from .config import DEFAULT_FILTERS
from .council import ADDED_V2, COUNCIL, DEFAULT_PROMPT
from .store import ensure_schema

from .config import IS_US, MARKET

DEFAULT_SCHEDULE = {"enabled": True, "time": MARKET["default_time"], "days": [0, 1, 2, 3, 4], "max_stocks": 20}  # Mon=0, market time
DEFAULT_EMAIL = {"enabled": False, "to": [], "cc": []}
DEFAULT_WHATSAPP = {"enabled": not IS_US}   # India: Dad's group. US: site only (for now)
DEFAULT_TELEMETRY = {"show_costs": True}   # show what each AI call cost, on every page

SCHEMA = "CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL, updated TEXT)"


def _council(rows: dict) -> tuple[list[dict], list[dict], str]:
    """(every member incl. switched-off ones, the members who vote, default thinking level)."""
    thinking = rows.get("thinking") or "medium"
    saved = rows.get("models") or [dict(m) for m in COUNCIL]
    if rows.get("council_version", 1) < 2:      # saved before Mistral and GLM joined: add them once
        have = {m["model"] for m in saved}
        saved = saved + [dict(m) for m in ADDED_V2 if m["model"] not in have]
    full = [{"name": m["name"], "model": m["model"], "effort": m.get("effort") or "default",
             "enabled": m.get("enabled", True)} for m in saved]
    voting = [{"name": m["name"], "model": m["model"], "effort": thinking if m["effort"] == "default" else m["effort"]}
              for m in full if m["enabled"]]
    return full, voting or [dict(m) for m in COUNCIL], thinking


def get_settings(conn) -> dict:
    ensure_schema(conn, "settings", SCHEMA)
    rows = {r["key"]: json.loads(r["value"]) for r in conn.execute("SELECT key, value FROM settings")}
    council_all, voting, thinking = _council(rows)
    return {
        "filters": {**DEFAULT_FILTERS, **rows.get("filters", {})},
        "prompt": rows.get("prompt") or DEFAULT_PROMPT,
        "models": voting,                 # the members who vote, each with its thinking level
        "council_all": council_all,       # for Settings: every member, including ones switched off
        "thinking": thinking,
        "screens": rows.get("screens") or [],
        # Broker orders by email (values live in the database, not in this public repo)
        "broker_to": rows.get("broker_to") or "",
        "accounts": rows.get("accounts") or [],
        "gmail_account": rows.get("gmail_account") or "",
        # Evening run and email (editable in Settings; the server checks every 15 minutes)
        "schedule": {**DEFAULT_SCHEDULE, **(rows.get("schedule") or {})},
        "email": {**DEFAULT_EMAIL, **(rows.get("email") or {})},
        "whatsapp": {**DEFAULT_WHATSAPP, **(rows.get("whatsapp") or {})},
        "telemetry": {**DEFAULT_TELEMETRY, **(rows.get("telemetry") or {})},
        "screener_state": rows.get("screener_state") or {},   # last screen used + each screen's edited rules
    }


def save_setting(conn, key: str, value) -> None:
    ensure_schema(conn, "settings", SCHEMA)
    conn.execute("INSERT OR REPLACE INTO settings VALUES (?,?,datetime('now'))", (key, json.dumps(value)))
    conn.commit()
