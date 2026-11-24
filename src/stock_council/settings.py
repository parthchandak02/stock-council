"""Settings Dad can edit on the site: screen filters, the prompt, and which models sit on the council."""
from __future__ import annotations

import json

from .config import DEFAULT_FILTERS
from .council import COUNCIL, DEFAULT_PROMPT
from .store import ensure_schema

from .config import IS_US, MARKET

DEFAULT_SCHEDULE = {"enabled": True, "time": MARKET["default_time"], "days": [0, 1, 2, 3, 4], "max_stocks": 20}  # Mon=0, market time
DEFAULT_EMAIL = {"enabled": False, "to": [], "cc": []}
DEFAULT_WHATSAPP = {"enabled": not IS_US}   # India: Dad's group. US: site only (for now)
DEFAULT_TELEMETRY = {"show_costs": True}   # show what each AI call cost, on every page

SCHEMA = "CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL, updated TEXT)"


def get_settings(conn) -> dict:
    ensure_schema(conn, "settings", SCHEMA)
    rows = {r["key"]: json.loads(r["value"]) for r in conn.execute("SELECT key, value FROM settings")}
    return {
        "filters": {**DEFAULT_FILTERS, **rows.get("filters", {})},
        "prompt": rows.get("prompt") or DEFAULT_PROMPT,
        "models": rows.get("models") or COUNCIL,
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
