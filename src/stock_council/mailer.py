"""Daily email through Cloudflare Email Service (REST API).

Sending to *verified destination addresses* is free on every Cloudflare plan, and we only ever
email the family, so this costs nothing. Configuration comes from the environment, never the repo:
  SC_EMAIL_FROM        sender on a Cloudflare Email Routing domain
  SC_EMAIL_TO          comma-separated recipients (each verified in Email Routing)
  SC_EMAIL_CC          optional, comma-separated
  SC_SITE_URL          link to the site
  CLOUDFLARE_ACCOUNT_ID
  SC_CF_EMAIL_TOKEN_FILE  file holding an API token with Email Sending: Edit
"""
from __future__ import annotations

import html
import json
import os
from pathlib import Path

import httpx

CALL_COLOUR = {"BUY": "#1a7f37", "SELL": "#c0392b", "HOLD": "#8a6d00", "NO CONSENSUS": "#555"}


def _token() -> str:
    path = Path(os.path.expanduser(os.environ.get("SC_CF_EMAIL_TOKEN_FILE", "~/.config/stock-council/cf-email.token")))
    return path.read_text().strip()


def _list(name: str) -> list[str]:
    return [x.strip() for x in os.environ.get(name, "").split(",") if x.strip()]


def build_email(run: dict, picks: list[dict], screens: list[dict] | None = None) -> tuple[str, str, str]:
    site = os.environ.get("SC_SITE_URL", "")
    buys = sum(1 for p in picks if (p.get("verdict") or {}).get("call") == "BUY")
    subject = f"Stock council {run['day']}: {len(picks)} stocks, {buys} BUY"
    rows_html, rows_txt = [], []
    for p in picks:
        v = p.get("verdict") or {}
        call = v.get("call", "ERROR" if p.get("error") else "-")
        label = f"{call}" + (f" ({v['flavour']})" if call == "BUY" and v.get("flavour") == "Stepwise Buy" else "")
        agree = f"{v.get('agree', 0)}/{v.get('voters', 0)}"
        sheet = p.get("sheet") or {}
        spike = sheet.get("spike_x") or (sheet.get("technicals") or {}).get("volume_1w_avg")
        reason = _one_line(p)
        link = f"{site}/stock/{run['id']}/{p['isin']}" if site else ""
        rows_html.append(
            f"<tr><td style='padding:6px 8px'><a href='{html.escape(link)}' style='color:#111;font-weight:600'>{html.escape(p['name'] or p['symbol'])}</a>"
            f"<br><span style='color:#777;font-size:12px'>{html.escape(p['symbol'] or '')} · ₹{p.get('price') or '-'}"
            f" · vol {sheet.get('spike_x', '-')}×</span></td>"
            f"<td style='padding:6px 8px;color:{CALL_COLOUR.get(call, '#555')};font-weight:700'>{html.escape(label)}<br>"
            f"<span style='color:#777;font-weight:400;font-size:12px'>{agree} agree · avg {v.get('avg_score', '-')}/10</span></td>"
            f"<td style='padding:6px 8px;font-size:13px;color:#333'>{html.escape(reason)}</td></tr>")
        rows_txt.append(f"- {p['name']} ({p['symbol']}) ₹{p.get('price')}: {label}, {agree} agree, avg {v.get('avg_score')}/10. {reason} {link}")
    body_html = (
        "<div style='font-family:system-ui,Segoe UI,Arial,sans-serif;max-width:720px'>"
        f"<p style='margin:0 0 12px'>{len(picks)} stocks passed today's volume-shock screen "
        f"({run.get('spikes', '?')} volume spikes checked).</p>"
        "<table style='border-collapse:collapse;width:100%' border='0'>"
        "<tr style='text-align:left;color:#777;font-size:12px'><th style='padding:6px 8px'>Stock</th>"
        "<th style='padding:6px 8px'>Council</th><th style='padding:6px 8px'>Why</th></tr>"
        + "".join(rows_html) + "</table>"
        + ("<p style='margin:18px 0 4px;font-weight:600'>Your saved screens</p><ul style='margin:0;padding-left:18px'>"
           + "".join(f"<li><a href='{html.escape(site)}/screener?s={s['id']}'>{html.escape(s['name'])}</a>: "
                     f"{s['count']} match{'es' if s['count'] != 1 else ''}"
                     + (f", <b>{s['new']} new</b>" if s['new'] else "") + "</li>" for s in screens) + "</ul>"
           if screens else "")
        + (f"<p style='margin:16px 0 0'><a href='{html.escape(site)}'>Open the full analysis</a></p>" if site else "")
        + "<p style='color:#999;font-size:11px;margin-top:16px'>Facts from NSE/BSE filings; opinions from 7 AI models. "
        "Not investment advice.</p></div>")
    body_txt = "\n".join([f"{len(picks)} stocks passed today's screen.", "", *rows_txt, "",
                          *[f"Saved screen {s['name']}: {s['count']} matches, {s['new']} new" for s in screens or []], "", site])
    return subject, body_html, body_txt


def _one_line(p: dict) -> str:
    if p.get("error"):
        return "Analysis failed: " + p["error"][:120]
    ops = [o for o in (p.get("opinions") or []) if not o.get("error")]
    call = (p.get("verdict") or {}).get("lean")
    from .council import VOTE_GROUP
    same = [o for o in ops if VOTE_GROUP.get(str(o.get("verdict", "")).lower()) == call] or ops
    risk = next((o.get("key_risk") for o in same if o.get("key_risk")), "")
    return risk[:160]


def recipients() -> tuple[list[str], list[str]]:
    """To and CC from Settings; the .env values are only a fallback."""
    try:
        from .daily import db
        from .settings import get_settings
        em = get_settings(db())["email"]
        if em.get("to"):
            return em["to"], em.get("cc") or []
    except Exception:
        pass
    return _list("SC_EMAIL_TO"), _list("SC_EMAIL_CC")


def _cf(method: str, path: str, **kw) -> dict:
    account = os.environ["CLOUDFLARE_ACCOUNT_ID"]
    r = httpx.request(method, f"https://api.cloudflare.com/client/v4/accounts/{account}/{path}",
                      headers={"Authorization": f"Bearer {_token()}"}, timeout=30, **kw)
    return r.json()


_addr_cache: dict = {}


def address_status(fresh: bool = False) -> dict[str, bool]:
    """Cloudflare only delivers to addresses that clicked its verify link: email -> verified?
    Cached 10 minutes so opening Settings doesn't wait on Cloudflare."""
    import time
    if not fresh and _addr_cache.get("at", 0) > time.time() - 600:
        return _addr_cache["data"]
    d = _cf("GET", "email/routing/addresses")
    out = {x["email"].lower(): bool(x.get("verified")) for x in d.get("result") or []}
    _addr_cache.update(at=time.time(), data=out)
    return out


def ensure_addresses(emails: list[str]) -> list[str]:
    """Register new recipients with Cloudflare, which emails each one a verify link. Returns the new ones."""
    known = address_status(fresh=True)
    added = []
    for e in emails:
        if e.lower() not in known:
            if _cf("POST", "email/routing/addresses", json={"email": e}).get("success"):
                added.append(e)
    return added


def send(subject: str, body_html: str, body_txt: str, to: list[str] | None = None, cc: list[str] | None = None) -> dict:
    account = os.environ["CLOUDFLARE_ACCOUNT_ID"]
    if to is None:
        to, cc = recipients()
    payload = {"from": os.environ["SC_EMAIL_FROM"], "to": to, "subject": subject,
               "html": body_html, "text": body_txt}
    if cc:
        payload["cc"] = cc
    r = httpx.post(f"https://api.cloudflare.com/client/v4/accounts/{account}/email/sending/send",
                   headers={"Authorization": f"Bearer {_token()}"}, json=payload, timeout=60)
    data = r.json() if r.headers.get("content-type", "").startswith("application/json") else {"raw": r.text}
    if r.status_code >= 300 or data.get("success") is False:
        raise RuntimeError(f"email send failed: HTTP {r.status_code} {json.dumps(data)[:400]}")
    return data
