"""Settings Dad can edit on the site: screen filters, the prompt, and which models sit on the council."""
from __future__ import annotations

import json

from .config import DEFAULT_FILTERS
from .council import COUNCIL, DEFAULT_PROMPT

SCHEMA = "CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL, updated TEXT)"


def get_settings(conn) -> dict:
    conn.execute(SCHEMA)
    rows = {r["key"]: json.loads(r["value"]) for r in conn.execute("SELECT key, value FROM settings")}
    return {
        "filters": {**DEFAULT_FILTERS, **rows.get("filters", {})},
        "prompt": rows.get("prompt") or DEFAULT_PROMPT,
        "models": rows.get("models") or COUNCIL,
    }


def save_setting(conn, key: str, value) -> None:
    conn.execute(SCHEMA)
    conn.execute("INSERT OR REPLACE INTO settings VALUES (?,?,datetime('now'))", (key, json.dumps(value)))
    conn.commit()
