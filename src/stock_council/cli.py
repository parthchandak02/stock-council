"""Command line.

  stock-council update            download NSE/BSE daily files (first run backfills ~1 year)
  stock-council screen            print today's screen
  stock-council sheet SYMBOL      print one fact sheet
  stock-council analyse SYMBOL    fact sheet + council for one stock (saved)
  stock-council daily [--no-email] the evening job: update, screen, council, email
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
    subject, body_html, body_txt = build_email(run, _picks(conn, run_id))
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
        if not a.no_email and os.environ.get("SC_EMAIL_TO"):
            send_run_email(run_id)
            print("emailed")
    elif a.cmd == "retry":
        from .daily import retry_failed
        print("retried", retry_failed(a.run_id))
    elif a.cmd == "email":
        send_run_email(a.run_id)
    elif a.cmd == "serve":
        import uvicorn
        uvicorn.run("stock_council.web.app:app", host="127.0.0.1", port=a.port, proxy_headers=True)
