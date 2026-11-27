"""Evening WhatsApp message through Hermes.

The app writes the text to a local socket; a small Hermes-side script sends it to one fixed group
("Invest · Stock & ETF Screen"). The app cannot choose the recipient.
"""
from __future__ import annotations

import os
import socket
from datetime import date

from .config import MARKET

SOCKET = os.environ.get("SC_WHATSAPP_SOCKET", "/run/stock-council-hermes.sock")
EMOJI = {"BUY": "🟢", "SELL": "🔴", "HOLD": "🟡", "NO CONSENSUS": "⚪"}
WORD = {"BUY": "BUY", "SELL": "SELL", "HOLD": "HOLD", "NO CONSENSUS": "SPLIT"}


def build_message(run: dict, picks: list[dict], site: str = "") -> str:
    from .web.view import nice_name
    day = date.fromisoformat(run["day"]).strftime("%a %-d %b")
    fn = run.get("funnel") or {}
    lines = [f"*Sabha* · {day}",
             (f"{len(picks)} stock{'s' if len(picks) != 1 else ''} from \"{fn['screen']}\" ({fn.get('matches', '?')} of {fn.get('companies') or 0:,} companies match)" if fn.get('screen') else f"{len(picks)} stock{'s' if len(picks) != 1 else ''} passed ({run.get('spikes') or fn.get('spikes') or '?'} volume spikes)"), ""]
    for p in picks:
        v = p.get("verdict") or {}
        call = v.get("call") or ("ERROR" if p.get("error") else "-")
        name = nice_name(p.get("name") or p.get("symbol"))[:28]
        price = f"{MARKET['cur']}{p['price']:,.0f}" if p.get("price") else ""
        if p.get("error") or not v:
            lines.append(f"⚠️ {name} {price} · analysis failed")
        else:
            lines.append(f"{EMOJI.get(call, '⚪')} {name} {price} · {WORD.get(call, call)} {v.get('agree', 0)}/{v.get('voters', 0)} · {v.get('avg_score', '-')}")
    if not picks:
        lines.append("No stock passed every filter today.")
    if site:
        lines += ["", f"{site}/run/{run['id']}"]
    return "\n".join(lines)


def send(text: str, timeout: float = 45) -> None:
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
        s.settimeout(timeout)
        s.connect(SOCKET)
        s.sendall(text.encode("utf-8")[:3500])
        s.shutdown(socket.SHUT_WR)
        reply = s.recv(200).decode("utf-8", "replace").strip()
    if reply != "ok":
        raise RuntimeError(f"WhatsApp send failed: {reply or 'no reply'}")
