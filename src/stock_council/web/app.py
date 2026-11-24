"""screen.parthchandak.info: today's list, the live process, one page per stock, how it works.

The password page in front of it is the shared site-gate service. The tunnel sends only the icon
and manifest paths straight here, so phones and browsers can show the app icon before sign-in.
"""
from __future__ import annotations

import json
import threading
from datetime import date
from pathlib import Path

import markdown
from fastapi import FastAPI, Form, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from ..config import DEFAULT_FILTERS
from ..council import COUNCIL, DEFAULT_PROMPT, VOTE_GROUP
import uuid

from ..daily import db, run_custom, run_one, run_screen_job, saved_screen_history, start_run
from ..universe import METRICS, PRESETS, run_rules
from ..settings import get_settings, save_setting
from .view import FILTER_LABELS, key_numbers, nice_name, short_fail, short_name, why

HERE = Path(__file__).parent
STATIC = HERE / "static"
app = FastAPI(title="Stock Council", docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=STATIC), name="static")
tpl = Jinja2Templates(directory=HERE / "templates")
tpl.env.filters["md"] = lambda s: markdown.markdown(s or "", extensions=["tables"])
tpl.env.filters["inr"] = lambda x: "-" if x is None else f"₹{x:,.2f}".rstrip("0").rstrip(".") if x < 1000 else f"₹{x:,.0f}"
tpl.env.filters["nice"] = nice_name
tpl.env.filters["fail"] = short_fail
tpl.env.filters["num"] = lambda x: "-" if x is None else f"{x:,}"
tpl.env.filters["day"] = lambda s: date.fromisoformat(s[:10]).strftime("%a %-d %b") if s else ""
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
        try:
            from ..universe import price_metrics
            price_metrics(db())
        except Exception:
            pass
    threading.Thread(target=go, daemon=True).start()


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
        "checks": checks, "nav": "today"})


@app.get("/run/{run_id}", response_class=HTMLResponse)
def run_page(request: Request, run_id: int):
    conn = db()
    run = _run(conn, run_id)
    if not run:
        return page_404(request)
    picks = _picks(conn, run_id, sort=False)
    if run["kind"] == "on_demand" and run["status"] == "done" and picks:
        return RedirectResponse(f"/stock/{run_id}/{picks[0]['isin']}", status_code=303)
    return tpl.TemplateResponse(request, "run.html", {"run": run, "picks": picks, "nav": "today"})


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
        return RedirectResponse(f"/run/{run_id}", status_code=303)
    return tpl.TemplateResponse(request, "stock.html", {
        "run": run, "p": p, "nums": key_numbers(p.get("sheet") or {}), "why": why(p), "nav": "today"})


@app.post("/check")
def check(symbol: str = Form(...)):
    sym = symbol.strip().upper().removesuffix(".NS").removesuffix(".BO")
    run_id = start_run("on_demand")
    threading.Thread(target=_quiet, args=(run_one, sym, run_id), daemon=True).start()
    return RedirectResponse(f"/run/{run_id}", status_code=303)


@app.post("/screen")
def screen_now():
    conn = db()
    busy = conn.execute("SELECT id FROM runs WHERE status='running' AND kind IN ('daily','manual')").fetchone()
    if busy:
        return RedirectResponse(f"/run/{busy['id']}", status_code=303)
    run_id = start_run("manual")
    threading.Thread(target=_quiet, args=(lambda rid: run_screen_job("manual", run_id=rid), run_id), daemon=True).start()
    return RedirectResponse(f"/run/{run_id}", status_code=303)


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
    return tpl.TemplateResponse(request, "how.html", {"st": st, "run": run, "nav": "how"})


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
    return tpl.TemplateResponse(request, "history.html", {"runs": rows, "nav": "history"})


@app.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request, saved: str = ""):
    st = get_settings(db())
    models_text = "\n".join(f"{m['name']} | {m['model']}" for m in st["models"])
    return tpl.TemplateResponse(request, "settings.html", {
        "st": st, "models_text": models_text, "saved": saved, "nav": "settings"})


@app.post("/settings")
async def save_settings(request: Request):
    form = await request.form()
    conn = db()
    if form.get("reset") == "prompt":
        save_setting(conn, "prompt", DEFAULT_PROMPT)
        return RedirectResponse("/settings?saved=prompt", status_code=303)
    filters = {}
    for k, default in DEFAULT_FILTERS.items():
        try:
            filters[k] = type(default)(float(form.get(k, default)))
        except (TypeError, ValueError):
            filters[k] = default
    save_setting(conn, "filters", filters)
    save_setting(conn, "prompt", (form.get("prompt") or "").strip() or DEFAULT_PROMPT)
    models = []
    for line in (form.get("models") or "").splitlines():
        if "|" in line:
            name, model = (x.strip() for x in line.split("|", 1))
            if name and "/" in model:
                models.append({"name": name, "model": model})
    save_setting(conn, "models", models or COUNCIL)
    return RedirectResponse("/settings?saved=1", status_code=303)


# ---------- screener ----------

def _rules_from(params) -> list:
    ms, os_, vs = params.getlist("m"), params.getlist("o"), params.getlist("v")
    rules = []
    for m, o, v in zip(ms, os_, vs):
        try:
            if m in METRICS and o in (">", "<") and v.strip() != "":
                rules.append([m, o, float(v)])
        except ValueError:
            pass
    return rules


DEFAULT_COLS = ["price", "change_1d", "mcap", "pe", "roe", "de", "spike_x", "delivery"]


@app.get("/screener", response_class=HTMLResponse)
def screener(request: Request):
    conn = db()
    st = get_settings(conn)
    q = request.query_params
    saved = st.get("screens") or []
    active = None
    if q.get("preset"):
        active = next((p for p in PRESETS if p["id"] == q["preset"]), None)
        rules = active["rules"] if active else []
    elif q.get("s"):
        active = next((s for s in saved if s["id"] == q["s"]), None)
        rules = active["rules"] if active else []
    else:
        rules = _rules_from(q)
    if not rules and not q:
        active, rules = PRESETS[0], PRESETS[0]["rules"]
    res = run_rules(rules, conn) if rules else None
    cols = DEFAULT_COLS + [k for k, _, _ in rules if k not in DEFAULT_COLS]
    for s in saved:
        s["last"] = saved_screen_history(conn, s["id"])
    return tpl.TemplateResponse(request, "screener.html", {
        "rules": rules, "res": res, "cols": cols, "metrics": METRICS, "presets": PRESETS,
        "saved": saved, "active": active, "nav": "screener"})


@app.post("/screener/save")
async def screener_save(request: Request):
    form = await request.form()
    rules = _rules_from(form)
    name = (form.get("name") or "").strip()[:60]
    if not rules or not name:
        return RedirectResponse("/screener", status_code=303)
    conn = db()
    screens = get_settings(conn).get("screens") or []
    sid = uuid.uuid4().hex[:8]
    screens.append({"id": sid, "name": name, "rules": rules, "daily": form.get("daily") == "1"})
    save_setting(conn, "screens", screens)
    return RedirectResponse(f"/screener?s={sid}", status_code=303)


@app.post("/screener/delete")
def screener_delete(sid: str = Form(...)):
    conn = db()
    save_setting(conn, "screens", [s for s in get_settings(conn).get("screens") or [] if s["id"] != sid])
    return RedirectResponse("/screener", status_code=303)


@app.post("/council")
async def council_batch(request: Request):
    form = await request.form()
    picks = [x.split("|", 1) for x in form.getlist("pick") if "|" in x][:10]
    if not picks:
        return RedirectResponse("/screener", status_code=303)
    if len(picks) == 1:
        run_id = start_run("on_demand")
        threading.Thread(target=_quiet, args=(run_one, picks[0][1], run_id), daemon=True).start()
    else:
        run_id = start_run("custom")
        items = [{"isin": i, "symbol": s, "name": s} for i, s in picks]
        threading.Thread(target=_quiet, args=(run_custom, run_id, items), daemon=True).start()
    return RedirectResponse(f"/run/{run_id}", status_code=303)


@app.get("/healthz")
def healthz():
    return {"ok": True}


# Last, so it never shadows a page route.
@app.get("/{name}", include_in_schema=False)
def icon_file(name: str, request: Request):
    if name not in ICON_FILES:
        return page_404(request)
    f, mime = ICON_FILES[name]
    return FileResponse(STATIC / f, media_type=mime, headers={"cache-control": "public, max-age=604800"})
