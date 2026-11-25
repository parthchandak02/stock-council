"""screen.parthchandak.info: today's list, the live process, one page per stock, how it works.

The password page in front of it is the shared site-gate service. The tunnel sends only the icon
and manifest paths straight here, so phones and browsers can show the app icon before sign-in.
"""
from __future__ import annotations

import json
import os
import threading
from datetime import date
from pathlib import Path

import markdown
from fastapi import FastAPI, Form, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from ..config import DEFAULT_FILTERS, IS_US, MARKET, PRIMARY_EXCH as PX
from ..council import COUNCIL, DEFAULT_PROMPT, VOTE_GROUP
import uuid

from ..daily import db, log_order, orders_db, run_custom, run_one, run_screen_job, saved_screen_history, start_run
from ..universe import METRICS, PRESETS, run_rules
from ..settings import get_settings, save_setting
from .view import FILTER_LABELS, key_numbers, nice_name, short_fail, short_name, why

HERE = Path(__file__).parent
STATIC = HERE / "static"
app = FastAPI(title="Stock Council", docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=STATIC), name="static")

from starlette.middleware.gzip import GZipMiddleware
app.add_middleware(GZipMiddleware, minimum_size=800)     # pages are mostly repeated markup: ~5x smaller


@app.middleware("http")
async def _cache_static(request: Request, call_next):
    """CSS/JS/icons are fingerprinted with ?v=<build>, so browsers may keep them until the next deploy."""
    resp = await call_next(request)
    if request.url.path.startswith("/static/"):
        resp.headers["cache-control"] = ("public, max-age=31536000, immutable" if request.url.query
                                         else "public, max-age=3600")
    return resp
tpl = Jinja2Templates(directory=HERE / "templates")
tpl.env.filters["md"] = lambda s: markdown.markdown(s or "", extensions=["tables"])
# A value that isn't there at all (a failed stock has no fact sheet) counts as "none" in every template check,
# so `x is not none` guards it the same way as an empty value.
from jinja2 import Undefined as _Undefined
tpl.env.tests["none"] = lambda x: x is None or isinstance(x, _Undefined)
_C = MARKET["cur"]
tpl.env.filters["inr"] = lambda x: "-" if x is None else f"{_C}{x:,.2f}" if x < 1000 else f"{_C}{x:,.0f}"
tpl.env.filters["nice"] = nice_name
tpl.env.filters["fail"] = short_fail
tpl.env.filters["num"] = lambda x: "-" if x is None else f"{x:,}"
tpl.env.filters["day"] = lambda s: date.fromisoformat(s[:10]).strftime("%a %-d %b") if s else ""
B = MARKET["base"]


def _build_id() -> str:
    """Changes with every deploy (git pull + restart): the commit, read straight from .git (the service user
    may not be allowed to run git on this checkout); else the newest template/static file."""
    try:
        git = next(p / ".git" for p in HERE.parents if (p / ".git").is_dir())
        head = (git / "HEAD").read_text().strip()
        if head.startswith("ref: "):
            ref = head[5:]
            f = git / ref
            if f.exists():
                return f.read_text().strip()[:10]
            for line in (git / "packed-refs").read_text().splitlines():
                if line.endswith(" " + ref):
                    return line.split()[0][:10]
        return head[:10]
    except Exception:
        files = list((HERE / "templates").iterdir()) + list(STATIC.iterdir())
        return str(int(max(f.stat().st_mtime for f in files)))


BUILD = _build_id()


def show_costs() -> bool:
    try:
        return bool(get_settings(db())["telemetry"].get("show_costs"))
    except Exception:
        return False


def usd(x) -> str:
    if x is None:
        return "–"
    return f"${x:.2f}" if x >= 0.995 else f"${x:.3f}" if x >= 0.0095 else f"${x:.4f}"


def funnel_steps(run: dict) -> list[tuple[str, int]]:
    """How many volume spikes are left after each rule, in order (a stock drops out at its first failed rule)."""
    fn, f = run.get("funnel") or {}, run.get("filters") or {}
    C, U = MARKET["cur"], MARKET["unit_label"]
    rules = [("market cap", f"Market cap over {C}{f.get('min_market_cap_cr', 0):,.0f} {U}"),
             ("P/E", f"P/E under {f.get('max_pe', 0):g}"), ("ROE", f"ROE over {f.get('min_roe_pct', 0):g}%"),
             ("D/E", f"Debt / equity under {f.get('max_debt_to_equity', 0):g}")]
    first = [0] * len(rules)
    for r in fn.get("rejected") or []:
        fails = r.get("fails") or []
        i = next((i for i, (k, _) in enumerate(rules) for x in fails
                  if x.startswith(k) or (k == "P/E" and "loss" in x)), 0)
        first[i] += 1
    left = fn.get("spikes") or 0
    out = [(f"Volume over {f.get('volume_spike_multiple', 6):g}× usual", left)]
    for (k, label), n in zip(rules, first):
        left -= n
        out.append((label, max(left, 0)))
    if fn.get("passed") is not None and out:
        out[-1] = (out[-1][0], len(fn["passed"]))
    return out


def rules_qs(rules: list) -> str:
    from urllib.parse import urlencode
    return urlencode([(k, x) for m, o, v in rules for k, x in (("m", m), ("o", o), ("v", v))])


def market_href(request, code: str) -> str:
    """The same page in the other market (Screener -> Screener), or its Today page when there is no match
    (a stock or run belongs to one market only)."""
    section = request.url.path.strip("/").split("/")[0] if request else ""
    section = section if section in ("screener", "how", "history", "track", "settings") else ""
    return ("/us/" if code == "us" else "/") + section


tpl.env.globals.update(market_href=market_href)
tpl.env.globals.update(B=B, M=MARKET, IS_US=IS_US, BUILD=BUILD, show_costs=show_costs, funnel_steps=funnel_steps,
                       rules_qs=rules_qs)
tpl.env.filters["usd"] = usd


def mcap(x) -> str:
    """Market cap in the market's unit: ₹ crore for India; $ M, B or T for the US."""
    if x is None:
        return "–"
    if not IS_US:
        return f"₹{x:,.0f} Cr"
    return f"${x / 1e6:,.2f} T" if x >= 1e6 else f"${x / 1e3:,.1f} B" if x >= 1e3 else f"${x:,.0f} M"


tpl.env.filters["mcap"] = mcap
tpl.env.globals.update(group=lambda v: VOTE_GROUP.get(str(v or "").lower(), ""), short_name=short_name,
                       key_numbers=key_numbers,
                       FILTER_LABELS=FILTER_LABELS)

ICON_FILES = {
    "favicon.ico": ("favicon-32.png", "image/png"), "favicon-32.png": ("favicon-32.png", "image/png"),
    "apple-touch-icon.png": ("apple-touch-icon.png", "image/png"),
    "apple-touch-icon-precomposed.png": ("apple-touch-icon.png", "image/png"),
    "icon-192.png": ("icon-192.png", "image/png"), "icon-512.png": ("icon-512.png", "image/png"),
    "icon-maskable-512.png": ("icon-maskable-512.png", "image/png"), "icon.svg": ("icon.svg", "image/svg+xml"),
}
MANIFEST = {
    "name": "Stock Council", "short_name": "Council",
    "description": "Daily volume-shock stock screen, judged by a council of AI analysts.",
    "start_url": "/", "scope": "/", "display": "standalone", "lang": "en",
    "background_color": "#f6f4ef", "theme_color": "#13263f",
    "icons": [{"src": "/icon-192.png", "sizes": "192x192", "type": "image/png", "purpose": "any"},
              {"src": "/icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any"},
              {"src": "/icon-maskable-512.png", "sizes": "512x512", "type": "image/png", "purpose": "maskable"}],
}


@app.on_event("startup")
def _warm():
    """Compute the Screener's price metrics in the background so the first visit is instant."""
    def go():
        import time
        from ..universe import price_metrics
        while True:  # recompute soon after the evening files land, not on a visitor's click
            try:
                price_metrics(db())
            except Exception:
                pass
            time.sleep(600)
    threading.Thread(target=go, daemon=True).start()


US_UPSTREAM = os.environ.get("SC_US_UPSTREAM", "http://127.0.0.1:8307")
HOP = {"connection", "keep-alive", "transfer-encoding", "content-encoding", "content-length", "host"}

if not IS_US:
    @app.api_route("/us", methods=["GET", "POST"], include_in_schema=False)
    @app.api_route("/us/{path:path}", methods=["GET", "POST"], include_in_schema=False)
    async def us_proxy(request: Request, path: str = ""):
        """The US market runs as its own copy of this app (own database); India passes /us/* to it."""
        import httpx
        from fastapi.responses import Response
        url = f"{US_UPSTREAM}/{path}" + (f"?{request.url.query}" if request.url.query else "")
        headers = {k: v for k, v in request.headers.items() if k.lower() not in HOP}
        try:
            async with httpx.AsyncClient(timeout=120) as c:
                r = await c.request(request.method, url, headers=headers, content=await request.body())
        except httpx.HTTPError:
            return tpl.TemplateResponse(request, "message.html", {"title": "US market is starting",
                                        "text": "The US side isn't answering yet. Try again in a minute."}, status_code=503)
        out = {k: v for k, v in r.headers.items() if k.lower() not in HOP}
        return Response(r.content, status_code=r.status_code, headers=out)


_JSON_PATHS = ("/holdings/", "/version", "/screener/draft", "/orders", "/healthz")


def _friendly(request: Request, status: int, title: str, text: str):
    if request.url.path.startswith(_JSON_PATHS):
        return JSONResponse({"ok": False, "error": title}, status_code=status)
    return tpl.TemplateResponse(request, "message.html", {"title": title, "text": text}, status_code=status)


@app.middleware("http")
async def _crash(request: Request, call_next):
    """Never a bare 'Internal Server Error': log it and show a page with a way back. Handled here (not with an
    exception handler) so the connection stays healthy for the next request."""
    try:
        return await call_next(request)
    except Exception:
        import logging
        logging.getLogger("stock_council.web").exception("page failed: %s", request.url.path)
        return _friendly(request, 500, "Something went wrong", "This page hit an error. It has been logged; try again in a minute.")


from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException


@app.exception_handler(StarletteHTTPException)
async def _http_error(request: Request, exc: StarletteHTTPException):
    if exc.status_code == 404:
        return _friendly(request, 404, "Not found", "That page doesn't exist.")
    return _friendly(request, exc.status_code, "Can't do that", str(exc.detail))


@app.exception_handler(RequestValidationError)
async def _bad_input(request: Request, exc: RequestValidationError):
    return _friendly(request, 400, "Not found", "That link doesn't point to anything.")


@app.get("/manifest.webmanifest")
def manifest():
    return JSONResponse(MANIFEST, media_type="application/manifest+json",
                        headers={"cache-control": "public, max-age=86400"})


def page_404(request: Request):
    return tpl.TemplateResponse(request, "message.html", {"title": "Not found", "text": "That page doesn't exist."},
                                status_code=404)


# ---------- data helpers ----------

def _load(row) -> dict:
    p = dict(row)
    for k in ("sheet", "news", "opinions", "verdict"):
        p[k] = json.loads(p[k]) if p.get(k) else None
    p["status"] = p.get("status") or ("error" if p.get("error") else "done")
    return p


def _run(conn, run_id: int) -> dict | None:
    r = conn.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
    if not r:
        return None
    run = dict(r)
    run["funnel"] = json.loads(run["funnel"]) if run.get("funnel") else None
    run["models"] = json.loads(run["models"]) if run.get("models") else COUNCIL
    run["filters"] = json.loads(run["filters"]) if run.get("filters") else DEFAULT_FILTERS
    run["stage"] = run.get("stage") or "done"
    return run


ORDER = {"BUY": 0, "NO CONSENSUS": 1, "HOLD": 2, "SELL": 3}


def _picks(conn, run_id: int, sort: bool = True) -> list[dict]:
    out = [_load(r) for r in conn.execute("SELECT * FROM picks WHERE run_id=? ORDER BY coalesce(seq, rowid)", (run_id,))]
    if sort:
        out.sort(key=lambda p: (p["status"] != "done", ORDER.get((p["verdict"] or {}).get("call"), 9),
                                -((p["verdict"] or {}).get("avg_score") or 0)))
    return out


def _latest_screen(conn):
    r = conn.execute("SELECT id FROM runs WHERE kind IN ('daily','manual') AND status='done' ORDER BY day DESC, id DESC LIMIT 1").fetchone()
    return _run(conn, r["id"]) if r else None


def _running(conn) -> list[dict]:
    return [_run(conn, r["id"]) for r in conn.execute("SELECT id FROM runs WHERE status='running' ORDER BY id DESC")]


# ---------- pages ----------

@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    conn = db()
    run = _latest_screen(conn)
    checks = [dict(r) | {"verdict": json.loads(r["verdict"]) if r["verdict"] else {}} for r in conn.execute(
        "SELECT r.id, r.day, r.status, p.name, p.symbol, p.isin, p.verdict, p.price FROM runs r JOIN picks p ON p.run_id=r.id "
        "WHERE r.kind='on_demand' ORDER BY r.id DESC LIMIT 6")]
    return tpl.TemplateResponse(request, "home.html", {
        "run": run, "picks": _picks(conn, run["id"]) if run else [], "running": _running(conn),
        "checks": checks, "nav": "today", "track": _track_buys(conn), "avg_stock": _avg_stock()})


def _track_buys(conn) -> dict | None:
    try:
        from ..track import calls, summary
        b = next((c for c in summary(calls(conn))["by_call"] if c["call"] == "BUY"), None)
        return b and {"n": b["n"], "up": b["up"], "avg": b["avg"]}
    except Exception:
        return None


def _avg_stock() -> float | None:
    try:
        from ..telemetry import summary as spend
        return spend(30)["per_stock"]
    except Exception:
        return None


@app.get("/run/{run_id}", response_class=HTMLResponse)
def run_page(request: Request, run_id: int):
    conn = db()
    run = _run(conn, run_id)
    if not run:
        return page_404(request)
    picks = _picks(conn, run_id, sort=False)
    if run["kind"] == "on_demand" and run["status"] == "done" and picks:
        return RedirectResponse(B + f"/stock/{run_id}/{picks[0]['isin']}", status_code=303)
    return tpl.TemplateResponse(request, "run.html", {"run": run, "picks": picks, "nav": "today", "calls": _calls(run_id)})


@app.get("/run/{run_id}/live", response_class=HTMLResponse)
def run_live(request: Request, run_id: int):
    """The process panel alone; the run page swaps it in every few seconds while a run is going."""
    conn = db()
    run = _run(conn, run_id)
    if not run:
        return HTMLResponse("", status_code=404)
    return tpl.TemplateResponse(request, "_process.html", {"run": run, "picks": _picks(conn, run_id, sort=False)},
                                headers={"x-run-status": run["status"], "cache-control": "no-store"})


@app.get("/stock/{run_id}/{isin}", response_class=HTMLResponse)
def stock(request: Request, run_id: int, isin: str):
    conn = db()
    run = _run(conn, run_id)
    row = conn.execute("SELECT * FROM picks WHERE run_id=? AND isin=?", (run_id, isin)).fetchone()
    if not run or not row:
        return page_404(request)
    p = _load(row)
    if p["status"] in ("queued", "facts"):
        return RedirectResponse(B + f"/run/{run_id}", status_code=303)
    return tpl.TemplateResponse(request, "stock.html", {
        "run": run, "p": p, "nums": key_numbers(p.get("sheet") or {}), "why": why(p), "nav": "today",
        "chart": price_chart(conn, isin, run["day"]), "peers": peers(conn, isin), "calls": _calls(run_id, isin),
        "broker": _broker(conn), "orders": _orders(isin)})


def _calls(run_id: int, isin: str | None = None) -> list[dict]:
    try:
        from ..telemetry import calls_for
        return calls_for(run_id, isin)
    except Exception:
        return []


def _broker(conn) -> dict:
    st = get_settings(conn)
    return {"to": st["broker_to"], "accounts": st["accounts"], "gmail": st["gmail_account"]}


def _orders(isin: str | None = None, limit: int = 50) -> list[dict]:
    conn = orders_db()
    q = "SELECT * FROM orders" + (" WHERE isin=?" if isin else "") + " ORDER BY id DESC LIMIT ?"
    rows = conn.execute(q, (isin, limit) if isin else (limit,)).fetchall()
    return [dict(r) | {"lines": json.loads(r["lines"])} for r in rows]


@app.post("/orders")
async def save_order(request: Request):
    import math
    try:
        d = await request.json()
        raw = d.get("lines") if isinstance(d, dict) else None
        lines = []
        for l in raw if isinstance(raw, list) else []:
            q, pr = float(str(l.get("qty", "")).strip()), float(str(l.get("price", "")).strip())
            if not (math.isfinite(q) and math.isfinite(pr)) or q <= 0 or pr <= 0:
                raise ValueError
            lines.append({"qty": int(q) if q == int(q) else q, "price": pr})
    except (ValueError, TypeError, AttributeError):
        return JSONResponse({"ok": False, "error": "bad order"}, status_code=400)
    if not isinstance(d, dict) or d.get("side") not in ("buy", "sell") or not lines:
        return JSONResponse({"ok": False}, status_code=400)
    oid = log_order(d.get("run_id"), d.get("isin"), d.get("symbol"), d.get("name"), d["side"],
                    d.get("account"), lines, d.get("subject"))
    return {"ok": True, "id": oid}


@app.post("/check")
def check(symbol: str = Form(...)):
    sym = symbol.strip().upper().removesuffix(".NS").removesuffix(".BO")
    run_id = start_run("on_demand")
    threading.Thread(target=_quiet, args=(run_one, sym, run_id), daemon=True).start()
    return RedirectResponse(B + f"/run/{run_id}", status_code=303)


@app.post("/screen")
def screen_now():
    conn = db()
    busy = conn.execute("SELECT id FROM runs WHERE status='running' AND kind IN ('daily','manual')").fetchone()
    if busy:
        return RedirectResponse(B + f"/run/{busy['id']}", status_code=303)
    run_id = start_run("manual")
    threading.Thread(target=_quiet, args=(lambda rid: run_screen_job("manual", run_id=rid), run_id), daemon=True).start()
    return RedirectResponse(B + f"/run/{run_id}", status_code=303)


def _quiet(fn, *args):
    try:
        fn(*args)
    except Exception:
        pass  # stored on the run row and shown on its page


@app.get("/how", response_class=HTMLResponse)
def how(request: Request):
    conn = db()
    st = get_settings(conn)
    run = _latest_screen(conn)
    return tpl.TemplateResponse(request, "how.html", {"st": st, "run": run, "nav": "how", "avg_stock": _avg_stock()})


@app.get("/history", response_class=HTMLResponse)
def history(request: Request):
    conn = db()
    rows = []
    for r in conn.execute("SELECT * FROM runs ORDER BY id DESC LIMIT 200"):
        run = dict(r)
        verdicts = [json.loads(v["verdict"]) for v in conn.execute(
            "SELECT verdict FROM picks WHERE run_id=? AND verdict IS NOT NULL", (run["id"],))]
        run["n"] = conn.execute("SELECT count(*) FROM picks WHERE run_id=?", (run["id"],)).fetchone()[0]
        run["buys"] = sum(1 for v in verdicts if v.get("call") == "BUY")
        run["sells"] = sum(1 for v in verdicts if v.get("call") == "SELL")
        if run["kind"] == "on_demand":
            p = conn.execute("SELECT name, symbol, isin, verdict FROM picks WHERE run_id=?", (run["id"],)).fetchone()
            run["pick"] = dict(p) | {"verdict": json.loads(p["verdict"]) if p and p["verdict"] else {}} if p else None
        rows.append(run)
    return tpl.TemplateResponse(request, "history.html", {"runs": rows, "orders": _orders(limit=30), "nav": "history"})


@app.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request, saved: str = "", sent: str = ""):
    st = get_settings(db())
    from ..council import EFFORTS, catalogue
    cat = catalogue()
    known = {m["id"] for m in cat}
    cat += [{"id": m["model"], "name": m["model"], "est": None, "reasoning": True, "json": True}
            for m in st["council_all"] if m["model"] not in known]
    try:
        from ..mailer import address_status
        status = address_status()
    except Exception:
        status = None
    times = [f"{h:02d}:{m:02d}" for h in range(17, 24) for m in (0, 15, 30, 45)]
    from ..telemetry import backfill_from_picks, openrouter_key, summary as spend_summary
    backfill_from_picks()
    spend = spend_summary(30)
    names = {m["model"]: m["name"] for m in st["council_all"]}
    for r in spend["by_model"]:
        r["name"] = "News search" if r["model"] == "news" else names.get(r["model"], r["model"].split("/")[-1])
    return tpl.TemplateResponse(request, "settings.html", {
        "st": st, "catalogue": cat, "efforts": EFFORTS, "saved": saved, "sent": sent, "nav": "settings",
        "spend": spend, "key": openrouter_key(),
        "addr_status": status, "times": times,
        "daynames": ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]})


@app.post("/settings")
async def save_settings(request: Request):
    form = await request.form()
    conn = db()
    if form.get("reset") == "prompt":
        save_setting(conn, "prompt", DEFAULT_PROMPT)
        return RedirectResponse(B + "/settings?saved=prompt", status_code=303)
    import math
    import re as _re

    def num(x, default):
        try:
            v = float(x)
            return v if math.isfinite(v) else default
        except (TypeError, ValueError):
            return default
    current = get_settings(conn)
    if any(k in form for k in DEFAULT_FILTERS):        # only fields the form actually sent are changed
        filters = dict(current["filters"])
        for k, default in DEFAULT_FILTERS.items():
            if k in form:
                filters[k] = type(default)(num(form.get(k), filters.get(k, default)))
        save_setting(conn, "filters", filters)
    if "prompt" in form:
        save_setting(conn, "prompt", (form.get("prompt") or "").strip() or DEFAULT_PROMPT)
    if "cm_model" in form:
        models = []
        for name, model, effort, on in zip(form.getlist("cm_name"), form.getlist("cm_model"), form.getlist("cm_effort"),
                                           form.getlist("cm_on")):
            model, name = model.strip(), (name.strip() or model.split("/")[-1])[:30]
            if "/" in model:
                models.append({"name": name, "model": model, "effort": effort or "default", "enabled": on == "1"})
        if models:
            save_setting(conn, "models", models)
            save_setting(conn, "council_version", 2)
        if form.get("thinking") in ("off", "low", "medium", "high"):
            save_setting(conn, "thinking", form["thinking"])
    sent = []
    if "sched_time" in form:
        t = form.get("sched_time") or ""
        save_setting(conn, "schedule", {
            "enabled": form.get("sched_on") == "1",
            "time": t if _re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", t) else current["schedule"]["time"],
            "days": sorted({int(d) for d in form.getlist("sched_days") if d.isdigit() and int(d) <= 6}),
            "max_stocks": max(1, min(30, int(num(form.get("max_stocks"), current["schedule"]["max_stocks"]))))})
        split = lambda s: [x.strip() for x in (s or "").replace("\n", ",").split(",") if "@" in x]
        to, cc = split(form.get("email_to")), split(form.get("email_cc"))
        save_setting(conn, "email", {"enabled": form.get("email_on") == "1", "to": to, "cc": cc})
        save_setting(conn, "whatsapp", {"enabled": form.get("wa_on") == "1"})
        save_setting(conn, "telemetry", {"show_costs": form.get("costs_on") == "1"})
        try:
            from ..mailer import ensure_addresses
            sent = ensure_addresses(to + cc)   # Cloudflare emails each new address a verify link
        except Exception:
            pass
    if "broker_to" in form:
        save_setting(conn, "broker_to", ", ".join(x.strip() for x in form["broker_to"].replace("\n", ",").split(",") if x.strip()))
        save_setting(conn, "accounts", [x.strip() for x in (form.get("accounts") or "").replace(",", "\n").splitlines() if x.strip()])
        save_setting(conn, "gmail_account", (form.get("gmail_account") or "").strip())
    if request.headers.get("x-autosave"):
        return JSONResponse({"ok": True})
    from urllib.parse import quote
    return RedirectResponse(B + "/settings?saved=1" + (f"&sent={quote(', '.join(sent))}" if sent else ""), status_code=303)


# ---------- screener ----------

def _rules_from(params) -> list:
    ms, os_, vs = params.getlist("m"), params.getlist("o"), params.getlist("v")
    rules = []
    for m, o, v in zip(ms, os_, vs):
        v = (v or "").strip()
        if m not in METRICS or o not in (">", "<") or not v:
            continue
        if v.startswith("@"):
            if v[1:] in METRICS:
                rules.append([m, o, v])       # compare with another measure
            continue
        try:
            x = float(v)
        except ValueError:
            continue
        if x == x and abs(x) != float("inf"):     # no nan / inf: they'd filter silently behind a blank box
            rules.append([m, o, x])
    return rules


DEFAULT_COLS = [k for k in ["price", "change_1d", "mcap", "pe", "roe", "de", "spike_x", "delivery"] if k in METRICS]


def _screen_state(conn) -> dict:
    """What the Screener shows when opened: the last screen used and every screen's edited rules.
    Kept on the server, so an edit made by Dad or Parth is still there for both next time."""
    st = get_settings(conn).get("screener_state") or {}
    return {"last": st.get("last") or "preset:" + PRESETS[0]["id"], "edits": st.get("edits") or {}}


def _screen_base(key: str, saved: list) -> dict | None:
    kind, _, ident = (key or "").partition(":")
    if kind == "preset":
        p = next((p for p in PRESETS if p["id"] == ident), None)
        return p and {**p, "key": key, "kind": "preset"}
    if kind == "s":
        x = next((x for x in saved if x["id"] == ident), None)
        return x and {**x, "key": key, "kind": "saved"}
    return None


@app.get("/screener", response_class=HTMLResponse)
def screener(request: Request):
    conn = db()
    st = get_settings(conn)
    q = request.query_params
    saved = st.get("screens") or []
    state = _screen_state(conn)
    key = ("preset:" + q["preset"]) if q.get("preset") else ("s:" + q["s"]) if q.get("s") else (q.get("from") or state["last"])
    active = _screen_base(key, saved) or _screen_base("preset:" + PRESETS[0]["id"], saved)
    key = active["key"]
    if q.getlist("m"):                      # the rules form was submitted: these are the edits
        rules = _rules_from(q)
        if rules != active["rules"]:
            state["edits"][key] = rules
        else:
            state["edits"].pop(key, None)
    else:
        rules = state["edits"][key] if key in state["edits"] else active["rules"]
    rules = [r for r in rules if r[0] in METRICS and not (isinstance(r[2], str) and r[2][1:] not in METRICS)]
    state["last"] = key
    save_setting(conn, "screener_state", state)
    edited = key in state["edits"]
    res = run_rules(rules, conn) if rules else None
    extra = [k for k, _, _ in rules] + [v[1:] for _, _, v in rules if isinstance(v, str)]
    cols = DEFAULT_COLS + [k for k in dict.fromkeys(extra) if k not in DEFAULT_COLS]
    for x in saved:
        x["last"] = saved_screen_history(conn, x["id"])
    from ..universe import SHOWN_PRESETS
    shown = [p for p in PRESETS if p["id"] in SHOWN_PRESETS or "preset:" + p["id"] == key]
    return tpl.TemplateResponse(request, "screener.html", {"avg_stock": _avg_stock(),
        "rules": rules, "res": res, "cols": cols, "metrics": METRICS, "presets": shown, "edited_keys": list(state["edits"]),
        "saved": saved, "active": active, "edited": edited, "nav": "screener"})


@app.post("/screener/draft")
async def screener_draft(request: Request):
    """Called as you type: keeps unsubmitted edits so they survive leaving the page."""
    form = await request.form()
    conn = db()
    state = _screen_state(conn)
    base = _screen_base(form.get("from") or "", get_settings(conn).get("screens") or [])
    if not base:
        return JSONResponse({"ok": False}, status_code=400)
    rules = _rules_from(form)
    if rules == base["rules"]:
        state["edits"].pop(base["key"], None)
    else:
        state["edits"][base["key"]] = rules       # an empty list too: every rule removed on purpose
    state["last"] = base["key"]
    save_setting(conn, "screener_state", state)
    return {"ok": True, "edited": base["key"] in state["edits"]}


@app.post("/screener/reset")
def screener_reset(key: str = Form(...)):
    conn = db()
    state = _screen_state(conn)
    state["edits"].pop(key, None)
    save_setting(conn, "screener_state", state)
    kind, _, ident = key.partition(":")
    return RedirectResponse(B + ("/screener?preset=" if kind == "preset" else "/screener?s=") + ident, status_code=303)


@app.post("/screener/save")
async def screener_save(request: Request):
    """Save the current rules: over the open saved screen (sid), or as a new one (name)."""
    form = await request.form()
    rules = _rules_from(form)
    conn = db()
    screens = get_settings(conn).get("screens") or []
    state = _screen_state(conn)
    sid = (form.get("sid") or "").strip()
    name = (form.get("name") or "").strip()[:60]
    if not rules:
        return RedirectResponse(B + "/screener", status_code=303)
    if sid and any(x["id"] == sid for x in screens) and not name:
        for x in screens:
            if x["id"] == sid:
                x["rules"] = rules
                x["daily"] = form.get("daily") == "1"
    elif name:
        sid = uuid.uuid4().hex[:8]
        screens.append({"id": sid, "name": name, "rules": rules, "daily": form.get("daily") == "1"})
        if form.get("from"):
            state["edits"].pop(form["from"], None)   # the edits now live in the new screen
    else:
        return RedirectResponse(B + "/screener", status_code=303)
    state["edits"].pop("s:" + sid, None)
    state["last"] = "s:" + sid
    save_setting(conn, "screens", screens)
    save_setting(conn, "screener_state", state)
    return RedirectResponse(B + f"/screener?s={sid}", status_code=303)


@app.post("/screener/delete")
def screener_delete(sid: str = Form(...)):
    conn = db()
    save_setting(conn, "screens", [x for x in get_settings(conn).get("screens") or [] if x["id"] != sid])
    state = _screen_state(conn)
    state["edits"].pop("s:" + sid, None)
    if state["last"] == "s:" + sid:
        state["last"] = "preset:" + PRESETS[0]["id"]
    save_setting(conn, "screener_state", state)
    return RedirectResponse(B + "/screener", status_code=303)


@app.post("/council")
async def council_batch(request: Request):
    form = await request.form()
    picks = [x.split("|", 1) for x in form.getlist("pick") if "|" in x][:10]
    if not picks:
        return RedirectResponse(B + "/screener", status_code=303)
    if len(picks) == 1:
        run_id = start_run("on_demand")
        threading.Thread(target=_quiet, args=(run_one, picks[0][1], run_id), daemon=True).start()
    else:
        run_id = start_run("custom")
        items = [{"isin": i, "symbol": s, "name": s} for i, s in picks]
        threading.Thread(target=_quiet, args=(run_custom, run_id, items), daemon=True).start()
    return RedirectResponse(B + f"/run/{run_id}", status_code=303)


# ---------- track record, chart, peers, csv ----------

@app.get("/track", response_class=HTMLResponse)
def track_page(request: Request):
    from ..track import calls, summary
    conn = db()
    df = calls(conn)
    rows = df.drop_duplicates(["isin", "day"]).to_dict("records") if not df.empty else []
    return tpl.TemplateResponse(request, "track.html", {"rows": rows, "sum": summary(df),
                                                        "max_days": int(df["days"].max()) if rows else 0, "nav": "history"})


def price_chart(conn, isin: str, mark_day: str | None = None) -> dict | None:
    """One year of split-adjusted closes, drawn in a 760×210 box: price axis on the right, volume underneath."""
    import math
    import pandas as pd
    from ..actions import adjust, aliases
    ids = aliases(conn, isin)
    df = pd.read_sql_query(
        f"SELECT date, '{isin}' isin, COALESCE(MAX(CASE WHEN exch='{PX}' THEN close END), MAX(close)) close, "
        f"SUM(volume) vol FROM daily WHERE isin IN ({','.join('?' * len(ids))}) GROUP BY date ORDER BY date",
        conn, params=ids)
    if len(df) < 20:
        return None
    df = adjust(df, conn, price_cols=("close",), volume_cols=("vol",))
    W, H, L, R, T, VOL = 760.0, 210.0, 4.0, 58.0, 10.0, 22.0
    plot_b = H - VOL - 24                       # bottom of the price area
    lo, hi = float(df["close"].min()), float(df["close"].max())
    raw = (hi - lo) / 3 or max(hi, 1) / 10
    mag = 10 ** math.floor(math.log10(raw))
    step = next(m * mag for m in (1, 2, 2.5, 5, 10) if m * mag >= raw)
    a, b = math.floor(lo / step) * step, math.ceil(hi / step) * step
    y = lambda v: T + (plot_b - T) * (1 - (v - a) / ((b - a) or 1))
    n = len(df)
    x = lambda i: L + i * (W - L - R) / (n - 1)
    xs = [x(i) for i in range(n)]
    ys = [y(c) for c in df["close"]]
    path = "M" + " L".join(f"{p:.1f},{q:.1f}" for p, q in zip(xs, ys))
    vmax = df["vol"].max() or 1
    med = df["vol"].median()
    bars = [(p, VOL * v / vmax, v > 4 * med) for p, v in zip(xs, df["vol"])]
    ticks = []
    v = a
    while v <= b + step / 2:
        ticks.append((round(y(v), 1), f"{v:,.0f}" if step >= 1 else f"{v:,.2f}"))
        v += step
    months, seen = [], set()
    for i, d in enumerate(df["date"]):
        key = d[:7]
        if key not in seen and d[8:10] <= "07":
            seen.add(key)
            months.append((round(xs[i], 1), date.fromisoformat(d).strftime("%b")))
    months = months[::2] if len(months) > 7 else months
    mark = next(((p, q) for p, q, d in zip(xs, ys, df["date"]) if d == mark_day), None)
    last = float(df["close"].iloc[-1])
    return {"path": path, "area": path + f" L{xs[-1]:.1f},{plot_b:.1f} L{L},{plot_b:.1f} Z", "bars": bars, "mark": mark,
            "ticks": ticks, "months": months, "lo": round(lo, 2), "hi": round(hi, 2), "last": last,
            "last_y": round(ys[-1], 1), "last_x": round(xs[-1], 1), "plot_b": plot_b, "vol_b": H - 20,
            "up": last >= df["close"].iloc[0], "W": W, "H": H, "L": L, "R": R, "T": T}


def peers(conn, isin: str, n: int = 6) -> list[dict]:
    try:
        from ..universe import table
        row = conn.execute("SELECT industry FROM universe WHERE isin=?", (isin,)).fetchone()
        if not row or not row["industry"]:
            return []
        _, df = table(conn)
        g = df[(df["industry"] == row["industry"])].sort_values("mcap", ascending=False)
        return g.head(n + 1).reset_index().to_dict("records")
    except Exception:
        return []


@app.get("/screener.csv")
def screener_csv(request: Request):
    import csv, io
    rules = _rules_from(request.query_params)
    q = request.query_params
    st = get_settings(db())
    if q.get("preset"):
        rules = next((p["rules"] for p in PRESETS if p["id"] == q["preset"]), rules)
    elif q.get("s"):
        rules = next((s["rules"] for s in st.get("screens") or [] if s["id"] == q["s"]), rules)
    res = run_rules(rules, db(), limit=5000)
    keys = ["symbol", "name", "sector", "industry"] + list(METRICS)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(keys)
    for h in res["hits"]:
        w.writerow(["" if h.get(k) is None or h.get(k) != h.get(k) else (round(h[k], 2) if isinstance(h[k], float) else h[k]) for k in keys])
    from fastapi.responses import Response
    return Response(buf.getvalue(), media_type="text/csv",
                    headers={"content-disposition": f'attachment; filename="screen-{res["day"]}.csv"'})


@app.get("/holdings/{symbol}")
def holdings_api(symbol: str, bse: str = ""):
    """12 quarters of shareholding with named holders (India). JSON for the stock page's chart/table."""
    if IS_US:
        return JSONResponse({"quarters": []})
    from ..holdings import history
    try:
        return JSONResponse({"quarters": history(symbol.upper(), bse or None)})
    except Exception as e:
        return JSONResponse({"quarters": [], "error": str(e)[:200]}, status_code=502)


@app.get("/healthz")
def healthz():
    return {"ok": True}


@app.get("/version")
def version():
    """The page polls this; a new value means a deploy happened and the page offers a reload."""
    return JSONResponse({"build": BUILD}, headers={"cache-control": "no-store"})


# Last, so it never shadows a page route.
@app.get("/{name}", include_in_schema=False)
def icon_file(name: str, request: Request):
    if name not in ICON_FILES:
        return page_404(request)
    f, mime = ICON_FILES[name]
    return FileResponse(STATIC / f, media_type=mime, headers={"cache-control": "public, max-age=604800"})
