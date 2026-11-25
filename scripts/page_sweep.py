"""Every page of one market must render: all runs, all stocks, every Screener preset and measure.
Run with SC_DATA_DIR pointing at a COPY of the data (pages can write small state)."""
import os
import sqlite3
import sys

from fastapi.testclient import TestClient

from stock_council import universe
from stock_council.web.app import app

c = TestClient(app, raise_server_exceptions=False)
mk = os.environ.get("SC_MARKET", "in")
db = sqlite3.connect(os.path.join(os.environ["SC_DATA_DIR"], "us.db" if mk == "us" else "stock-council.db"))
urls = ["/", "/screener", "/how", "/history", "/track", "/settings", "/version", "/healthz"]
urls += [f"/run/{r}" for (r,) in db.execute("select id from runs")]
urls += [f"/run/{r}/live" for (r,) in db.execute("select id from runs")]
urls += [f"/stock/{r}/{i}" for r, i in db.execute("select run_id, isin from picks")]
urls += [f"/screener?preset={p['id']}" for p in universe.PRESETS]
urls += [f"/screener?m={k}&o=>&v=1&from=preset:volume-shock" for k in universe.METRICS]
bad = [(u, s) for u in urls if (s := c.get(u).status_code) >= 500]
print(f"page sweep {mk}: {len(urls)} pages, {len(bad)} failing", bad[:10])
sys.exit(1 if bad else 0)
