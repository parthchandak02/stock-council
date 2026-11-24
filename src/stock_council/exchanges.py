"""Download NSE and BSE end-of-day files (official, free) into the `daily` table.

Files per trading day:
  NSE bhavcopy (UDiFF)  - price, volume, ISIN       nsearchives.nseindia.com/content/cm/...
  NSE sec_bhavdata_full - delivery quantity         nsearchives.nseindia.com/products/content/...
  BSE bhavcopy (UDiFF)  - price, volume, ISIN       www.bseindia.com/download/BhavCopy/Equity/...
  BSE SCBSEALL          - delivery quantity         www.bseindia.com/BSEDATA/gross/...
Holidays return 404 and are recorded as 'missing' so we never ask twice.
"""
from __future__ import annotations

import csv
import io
import logging
import time
import zipfile
from datetime import date, timedelta

from curl_cffi import requests

from .store import connect

log = logging.getLogger(__name__)

NSE_BHAV = "https://nsearchives.nseindia.com/content/cm/BhavCopy_NSE_CM_0_0_0_{ymd}_F_0000.csv.zip"
NSE_DELIV = "https://nsearchives.nseindia.com/products/content/sec_bhavdata_full_{dmy}.csv"
BSE_BHAV = "https://www.bseindia.com/download/BhavCopy/Equity/BhavCopy_BSE_CM_0_0_0_{ymd}_F_0000.CSV"
BSE_DELIV = "https://www.bseindia.com/BSEDATA/gross/{yyyy}/SCBSEALL{ddmm}.zip"

# Equity series only (drops bonds, ETFs' odd series, gold bonds 'GB' etc. are not STK-equity ISINs).
NSE_SERIES = {"EQ", "BE", "BZ", "SM", "ST", "SZ"}

_session: requests.Session | None = None


def session() -> requests.Session:
    global _session
    if _session is None:
        _session = requests.Session(impersonate="chrome")
    return _session


def _get(url: str, referer: str) -> bytes | None:
    for attempt in range(3):
        try:
            r = session().get(url, headers={"Referer": referer}, timeout=60)
        except Exception as e:  # network hiccup
            log.warning("fetch %s failed (%s), retry %d", url, e, attempt + 1)
            time.sleep(3 * (attempt + 1))
            continue
        if r.status_code == 200 and r.content:
            return r.content
        if r.status_code == 404:
            return None
        log.warning("fetch %s -> HTTP %s, retry %d", url, r.status_code, attempt + 1)
        time.sleep(3 * (attempt + 1))
    raise RuntimeError(f"could not download {url}")


def _num(x: str):
    x = (x or "").strip()
    if x in ("", "-"):
        return None
    try:
        return float(x)
    except ValueError:
        return None


def _udiff_rows(text: str, exch: str, d: date):
    for r in csv.DictReader(io.StringIO(text)):
        if r.get("FinInstrmTp") != "STK" or not r.get("ISIN", "").startswith("IN"):
            continue
        series = (r.get("SctySrs") or "").strip()
        if exch == "NSE" and series not in NSE_SERIES:
            continue
        yield (
            d.isoformat(), exch, r["ISIN"].strip(), r["TckrSymb"].strip(),
            r["FinInstrmId"].strip(), series, (r.get("FinInstrmNm") or "").strip(),
            _num(r["OpnPric"]), _num(r["HghPric"]), _num(r["LwPric"]), _num(r["ClsPric"]),
            _num(r["PrvsClsgPric"]), int(_num(r["TtlTradgVol"]) or 0), _num(r["TtlTrfVal"]),
        )


def _mark(conn, d: date, kind: str, status: str) -> None:
    # A file that is not there yet for the last few days may simply not be published yet:
    # only remember 'missing' (holiday) for older dates, so recent days are retried.
    if status == "missing" and (date.today() - d).days < 4:
        return
    conn.execute("INSERT OR REPLACE INTO fetched VALUES (?,?,?)", (d.isoformat(), kind, status))


def _done(conn, d: date, kind: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM fetched WHERE date=? AND kind=?", (d.isoformat(), kind)
    ).fetchone() is not None


INSERT = """INSERT OR REPLACE INTO daily
 (date, exch, isin, symbol, code, series, name, open, high, low, close, prev_close, volume, turnover)
 VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)"""


def fetch_bhav(conn, d: date) -> None:
    ymd = d.strftime("%Y%m%d")
    if not _done(conn, d, "nse_bhav"):
        blob = _get(NSE_BHAV.format(ymd=ymd), "https://www.nseindia.com/")
        if blob:
            with zipfile.ZipFile(io.BytesIO(blob)) as z:
                text = z.read(z.namelist()[0]).decode("utf-8", "replace")
            conn.executemany(INSERT, list(_udiff_rows(text, "NSE", d)))
        _mark(conn, d, "nse_bhav", "ok" if blob else "missing")
        conn.commit()
        time.sleep(1)
    if not _done(conn, d, "bse_bhav"):
        blob = _get(BSE_BHAV.format(ymd=ymd), "https://www.bseindia.com/")
        if blob:
            conn.executemany(INSERT, list(_udiff_rows(blob.decode("utf-8", "replace"), "BSE", d)))
        _mark(conn, d, "bse_bhav", "ok" if blob else "missing")
        conn.commit()
        time.sleep(1)


def fetch_delivery(conn, d: date) -> None:
    if not _done(conn, d, "nse_deliv"):
        blob = _get(NSE_DELIV.format(dmy=d.strftime("%d%m%Y")), "https://www.nseindia.com/")
        if blob:
            rows = []
            for r in csv.reader(io.StringIO(blob.decode("utf-8", "replace"))):
                r = [c.strip() for c in r]
                if len(r) < 15 or r[0] == "SYMBOL":
                    continue
                q = _num(r[13])
                if q is not None:
                    rows.append((int(q), d.isoformat(), r[0], r[1]))
            conn.executemany(
                "UPDATE daily SET deliv_qty=? WHERE date=? AND exch='NSE' AND symbol=? AND series=?", rows
            )
        _mark(conn, d, "nse_deliv", "ok" if blob else "missing")
        conn.commit()
        time.sleep(1)
    if not _done(conn, d, "bse_deliv"):
        blob = _get(BSE_DELIV.format(yyyy=d.year, ddmm=d.strftime("%d%m")), "https://www.bseindia.com/")
        if blob:
            with zipfile.ZipFile(io.BytesIO(blob)) as z:
                text = z.read(z.namelist()[0]).decode("utf-8", "replace")
            rows = []
            for line in text.splitlines()[1:]:
                p = line.split("|")
                if len(p) >= 5 and p[0] == d.strftime("%d%m%Y"):
                    rows.append((int(p[2]), d.isoformat(), p[1].strip()))
            conn.executemany(
                "UPDATE daily SET deliv_qty=? WHERE date=? AND exch='BSE' AND code=?", rows
            )
        _mark(conn, d, "bse_deliv", "ok" if blob else "missing")
        conn.commit()
        time.sleep(1)


def weekdays_back(end: date, n_calendar_days: int):
    d = end
    stop = end - timedelta(days=n_calendar_days)
    while d > stop:
        if d.weekday() < 5:
            yield d
        d -= timedelta(days=1)


def update(end: date | None = None, history_days: int = 380, delivery_days: int = 45) -> dict:
    """Bring the database up to date: ~1 year of bhavcopies, ~6 weeks of delivery data."""
    end = end or date.today()
    conn = connect()
    stats = {"days": 0}
    for i, d in enumerate(weekdays_back(end, history_days)):
        fetch_bhav(conn, d)
        if (end - d).days <= delivery_days:
            fetch_delivery(conn, d)
        stats["days"] += 1
        if i and i % 25 == 0:
            log.info("…%s", d)
    stats["latest"] = latest_date(conn)
    return stats


def latest_date(conn) -> str | None:
    row = conn.execute("SELECT max(date) AS d FROM daily").fetchone()
    return row["d"] if row else None
