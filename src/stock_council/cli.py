"""Command line: stock-council update | screen | sheet SYMBOL"""
from __future__ import annotations

import argparse
import json
import logging


def main() -> None:
    ap = argparse.ArgumentParser(prog="stock-council")
    sub = ap.add_subparsers(dest="cmd", required=True)
    u = sub.add_parser("update", help="download NSE/BSE daily files (first run backfills ~1 year)")
    u.add_argument("--days", type=int, default=380)
    sub.add_parser("screen", help="run the volume-shock screen on the latest day")
    s = sub.add_parser("sheet", help="build the fact sheet for one NSE symbol")
    s.add_argument("symbol")
    s.add_argument("--json", action="store_true")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    if a.cmd == "update":
        from .exchanges import update
        print(update(history_days=a.days))
    elif a.cmd == "screen":
        from .screen import run_screen, render
        print(render(run_screen()))
    elif a.cmd == "sheet":
        from .factsheet import build, render_markdown
        sheet = build(a.symbol.upper())
        print(json.dumps(sheet, indent=2, default=str) if a.json else render_markdown(sheet))
