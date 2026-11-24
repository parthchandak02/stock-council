"""Who owns each company, for the Screener: promoter, foreign and Indian funds, pledge, and their trend.

Two NSE calls give every company's latest promoter/public split and promoter pledge. The fund split
(FII, DII, mutual funds) is only in each company's shareholding filing, so a slow background crawl
reads the latest filing and the one from a year earlier, one company at a time, and only again when
a company files a new quarter.
"""
from __future__ import annotations

import logging
import time
from datetime import date

from .nse_api import NSE, _f
from .store import connect, ensure_schema

log = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS ownership (
    isin TEXT PRIMARY KEY, symbol TEXT, quarter TEXT, promoter REAL, public REAL,
    fii REAL, dii REAL, mf REAL, retail REAL,
    promoter_1y REAL, fii_1y REAL, dii_1y REAL, quarter_1y TEXT,
    pledge REAL, pledge_total REAL, xbrl TEXT, detail_quarter TEXT, updated TEXT
);
"""


def db():
    conn = connect()
    ensure_schema(conn, "ownership", SCHEMA)
    return conn


def refresh_bulk(nse: NSE | None = None) -> dict:
    """Latest promoter % and pledge for every NSE company (two requests)."""
    nse = nse or NSE()
    conn = db()
    shp = nse._get_json("corporate-share-holdings-master?index=equities")
    time.sleep(1.5)
    pledge = {r.get("comName"): r for r in (nse._get_json("corporate-pledgedata?index=equities").get("data") or [])}
    n = 0
    for r in shp:
        isin = r.get("isin")
        if not isin:
            continue
        p = pledge.get(r.get("name")) or {}
        conn.execute(
            "INSERT INTO ownership(isin, symbol, quarter, promoter, public, pledge, pledge_total, xbrl, updated) "
            "VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT(isin) DO UPDATE SET symbol=excluded.symbol, quarter=excluded.quarter, "
            "promoter=excluded.promoter, public=excluded.public, pledge=excluded.pledge, "
            "pledge_total=excluded.pledge_total, xbrl=excluded.xbrl, updated=excluded.updated",
            (isin, r.get("symbol"), r.get("date"), _f(r.get("pr_and_prgrp")), _f(r.get("public_val")),
             _f(p.get("percPromoterShares")) if p else 0.0, _f(p.get("percTotShares")) if p else 0.0,
             r.get("xbrl"), date.today().isoformat()))
        n += 1
    conn.commit()
    return {"companies": n, "with_pledge_data": len(pledge)}


def refresh_detail(limit: int | None = None, pause: float = 1.2, only: set[str] | None = None) -> dict:
    """Fund split now and a year ago, for companies whose latest filing we haven't read yet."""
    conn = db()
    rows = conn.execute("SELECT isin, symbol, quarter FROM ownership WHERE symbol IS NOT NULL AND "
                        "(detail_quarter IS NULL OR detail_quarter != quarter)").fetchall()
    if only is not None:
        rows = [r for r in rows if r["isin"] in only]
    if limit:
        rows = rows[:limit]
    nse, done, failed = NSE(), 0, 0
    for r in rows:
        try:
            hist = nse.shareholding(r["symbol"], quarters=5, xbrl_for={0, 4})   # latest and a year back
            if not hist:
                raise LookupError("no filings")
            now, ago = hist[0], (hist[4] if len(hist) > 4 else hist[-1])
            conn.execute(
                "UPDATE ownership SET fii=?, dii=?, mf=?, retail=?, promoter_1y=?, fii_1y=?, dii_1y=?, quarter_1y=?, "
                "detail_quarter=quarter WHERE isin=?",
                (now.get("fii"), now.get("dii"), now.get("mutual_funds"), now.get("retail_small"),
                 ago.get("promoter"), ago.get("fii"), ago.get("dii"), ago.get("quarter"), r["isin"]))
            conn.commit()
            done += 1
        except Exception as e:
            failed += 1
            log.warning("ownership %s: %s", r["symbol"], e)
        if (done + failed) % 100 == 0:
            log.info("ownership: %d done, %d failed of %d", done, failed, len(rows))
        time.sleep(pause)
    return {"todo": len(rows), "done": done, "failed": failed}
