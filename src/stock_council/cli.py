"""Command line.

  stock-council update            download NSE/BSE daily files (first run backfills ~1 year)
  stock-council screen            print today's screen
  stock-council sheet SYMBOL      print one fact sheet
  stock-council analyse SYMBOL    fact sheet + council for one stock (saved)
  stock-council daily [--no-email] the evening job: update, screen, council, email
  stock-council universe          refresh Screener fundamentals (oldest first)
  stock-council retry RUN_ID      re-run the stocks of a run that failed
  stock-council email RUN_ID      (re)send the email for a run
  stock-council serve             the website on 127.0.0.1:8306
"""
from __future__ import annotations

import argparse
import json
import logging
import os


def send_run_email(run_id: int) -> None:
    from .daily import db
    from .mailer import build_email, send
    from .web.app import _picks
    conn = db()
    run = dict(conn.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone())
    from .daily import saved_screen_history
    from .settings import get_settings
    extras = []
    for s in get_settings(conn).get("screens") or []:
        h = saved_screen_history(conn, s["id"])
        if s.get("daily") and h:
            extras.append({"name": s["name"], "id": s["id"], "count": h["count"], "new": len(h.get("new") or [])})
    subject, body_html, body_txt = build_email(run, _picks(conn, run_id), extras)
    send(subject, body_html, body_txt)
    conn.execute("UPDATE runs SET emailed=datetime('now') WHERE id=?", (run_id,))
    conn.commit()


def main() -> None:
    ap = argparse.ArgumentParser(prog="stock-council")
    sub = ap.add_subparsers(dest="cmd", required=True)
    u = sub.add_parser("update")
    u.add_argument("--days", type=int, default=380)
    sub.add_parser("screen")
    s = sub.add_parser("sheet")
    s.add_argument("symbol")
    s.add_argument("--json", action="store_true")
    a1 = sub.add_parser("analyse")
    a1.add_argument("symbol")
    d = sub.add_parser("daily")
    d.add_argument("--no-email", action="store_true")
    d.add_argument("--max", type=int, default=20)
    d.add_argument("--require-today", action="store_true", help="skip quietly if today's files are not out")
    uv = sub.add_parser("universe", help="refresh Screener fundamentals (oldest first)")
    uv.add_argument("--limit", type=int, default=None)
    uv.add_argument("--max-age", type=int, default=7, help="re-fetch anything older than this many days (0 = all)")
    ow = sub.add_parser("ownership", help="refresh promoter/fund/pledge data (bulk, then per-company detail)")
    ow.add_argument("--limit", type=int, default=None)
    rt = sub.add_parser("retry", help="re-run failed stocks of a run")
    rt.add_argument("run_id", type=int)
    e = sub.add_parser("email")
    e.add_argument("run_id", type=int)
    sv = sub.add_parser("serve")
    sv.add_argument("--port", type=int, default=int(os.environ.get("SC_PORT", 8306)))
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

    if a.cmd == "update":
        from .exchanges import update
        print(update(history_days=a.days))
    elif a.cmd == "screen":
        from .screen import render, run_screen
        print(render(run_screen()))
    elif a.cmd == "sheet":
        from .factsheet import build, render_markdown
        sheet = build(a.symbol.upper())
        print(json.dumps(sheet, indent=2, default=str) if a.json else render_markdown(sheet))
    elif a.cmd == "analyse":
        from .daily import run_one
        print("run", run_one(a.symbol))
    elif a.cmd == "daily":
        from .daily import NothingToDo, run_daily
        try:
            run_id = run_daily(max_stocks=a.max, require_today=a.require_today)
        except NothingToDo as e:
            print("nothing to do:", e)
            return
        print("run", run_id)
        try:
            from .daily import run_saved_screens
            print("saved screens", run_saved_screens())   # cheap, offline; their new matches go in the email
        except Exception as e:
            logging.exception("saved screens failed: %s", e)
        if not a.no_email and os.environ.get("SC_EMAIL_TO"):
            send_run_email(run_id)
            print("emailed")
        from .upkeep import after_screen
        print("upkeep", after_screen())
    elif a.cmd == "universe":
        from .universe import refresh
        print(refresh(limit=a.limit, max_age_days=a.max_age))
    elif a.cmd == "ownership":
        from .ownership import refresh_bulk, refresh_detail
        print(refresh_bulk())
        print(refresh_detail(limit=a.limit))
    elif a.cmd == "retry":
        from .daily import retry_failed
        print("retried", retry_failed(a.run_id))
    elif a.cmd == "email":
        send_run_email(a.run_id)
    elif a.cmd == "serve":
        import uvicorn
        uvicorn.run("stock_council.web.app:app", host="127.0.0.1", port=a.port, proxy_headers=True)
