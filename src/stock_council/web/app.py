"""screen.parthchandak.info: today's list, one page per stock, run-any-stock, settings, history.

The password page in front of it is the shared site-gate service, not this app.
"""
from __future__ import annotations

import json
import threading
from pathlib import Path

import markdown
from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from ..config import DEFAULT_FILTERS
from ..council import COUNCIL, DEFAULT_PROMPT, VOTE_GROUP
from ..daily import db, run_one
from ..settings import get_settings, save_setting

HERE = Path(__file__).parent
app = FastAPI(title="Stock Council", docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
tpl = Jinja2Templates(directory=HERE / "templates")
tpl.env.filters["md"] = lambda s: markdown.markdown(s or "", extensions=["tables"])
tpl.env.globals["group"] = lambda v: VOTE_GROUP.get(str(v or "").lower(), "")

FILTER_LABELS = {
    "min_market_cap_cr": "Market cap above (₹ Cr)",
    "min_volume": "Today's volume above (shares)",
    "max_pe": "P/E below",
    "min_roe_pct": "ROE above (%)",
    "max_debt_to_equity": "Debt / equity below",
    "volume_spike_multiple": "1-week avg volume above 1-year avg ×",
}


def _picks(conn, run_id: int) -> list[dict]:
    out = []
    for r in conn.execute("SELECT * FROM picks WHERE run_id=? ORDER BY rowid", (run_id,)):
        p = dict(r)
        for k in ("sheet", "news", "opinions", "verdict"):
            p[k] = json.loads(p[k]) if p.get(k) else None
        out.append(p)
    order = {"BUY": 0, "NO CONSENSUS": 1, "HOLD": 2, "SELL": 3}
    out.sort(key=lambda p: (order.get((p["verdict"] or {}).get("call"), 9), -((p["verdict"] or {}).get("avg_score") or 0)))
    return out


@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    conn = db()
    run = conn.execute("SELECT * FROM runs WHERE kind='daily' AND status!='running' ORDER BY id DESC LIMIT 1").fetchone()
    running = conn.execute("SELECT * FROM runs WHERE status='running' ORDER BY id DESC").fetchall()
    recent = conn.execute("SELECT r.*, p.name, p.symbol, p.isin, p.verdict FROM runs r JOIN picks p ON p.run_id=r.id "
                          "WHERE r.kind='on_demand' ORDER BY r.id DESC LIMIT 8").fetchall()
    return tpl.TemplateResponse(request, "home.html", {
        "run": dict(run) if run else None, "picks": _picks(conn, run["id"]) if run else [],
        "running": [dict(r) for r in running],
        "recent": [{**dict(r), "verdict": json.loads(r["verdict"]) if r["verdict"] else {}} for r in recent]})


@app.get("/stock/{run_id}/{isin}", response_class=HTMLResponse)
def stock(request: Request, run_id: int, isin: str):
    conn = db()
    run = conn.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
    pick = next((p for p in _picks(conn, run_id) if p["isin"] == isin), None)
    if not run or not pick:
        return HTMLResponse("Not found", status_code=404)
    return tpl.TemplateResponse(request, "stock.html", {"run": dict(run), "p": pick})


@app.post("/analyse")
def analyse(symbol: str = Form(...)):
    sym = symbol.strip().upper().removesuffix(".NS")
    threading.Thread(target=_safe_run_one, args=(sym,), daemon=True).start()
    return RedirectResponse("/?started=" + sym, status_code=303)


def _safe_run_one(sym: str) -> None:
    try:
        run_one(sym)
    except Exception:
        pass  # the failure is stored on the run row and shown on the page


@app.get("/run/{run_id}")
def run_page(run_id: int):
    conn = db()
    p = conn.execute("SELECT isin FROM picks WHERE run_id=? LIMIT 1", (run_id,)).fetchone()
    return RedirectResponse(f"/stock/{run_id}/{p['isin']}" if p else "/", status_code=303)


@app.get("/history", response_class=HTMLResponse)
def history(request: Request):
    conn = db()
    runs = conn.execute("SELECT r.*, (SELECT count(*) FROM picks p WHERE p.run_id=r.id) AS n FROM runs r "
                        "ORDER BY id DESC LIMIT 200").fetchall()
    return tpl.TemplateResponse(request, "history.html", {"runs": [dict(r) for r in runs]})


@app.get("/day/{run_id}", response_class=HTMLResponse)
def day(request: Request, run_id: int):
    conn = db()
    run = conn.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
    if not run:
        return HTMLResponse("Not found", status_code=404)
    return tpl.TemplateResponse(request, "home.html", {"run": dict(run), "picks": _picks(conn, run_id),
                                                       "running": [], "recent": [], "past": True})


@app.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request, saved: str = ""):
    st = get_settings(db())
    models_text = "\n".join(f"{m['name']} | {m['model']}" for m in st["models"])
    return tpl.TemplateResponse(request, "settings.html", {
        "st": st, "labels": FILTER_LABELS, "models_text": models_text, "saved": saved,
        "default_prompt": DEFAULT_PROMPT, "default_filters": DEFAULT_FILTERS})


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
    prompt = (form.get("prompt") or "").strip()
    save_setting(conn, "prompt", prompt or DEFAULT_PROMPT)
    models = []
    for line in (form.get("models") or "").splitlines():
        if "|" in line:
            name, model = (x.strip() for x in line.split("|", 1))
            if name and "/" in model:
                models.append({"name": name, "model": model})
    save_setting(conn, "models", models or COUNCIL)
    return RedirectResponse("/settings?saved=1", status_code=303)


@app.get("/healthz")
def healthz():
    return {"ok": True}
