"""When the evening screen last ran, where we are now, and when it runs next (the Today timeline).

Next run follows Settings (on/off, days, time) and skips exchange holidays: NSE's own holiday list
(fetched weekly) for India, the NYSE calendar for the US.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

from .config import IS_US, MARKET, market_now
from .store import cache_get, cache_put

# NYSE full-day closures (nyse.com/markets/hours-calendars)
NYSE = {"2026-01-01", "2026-01-19", "2026-02-16", "2026-04-03", "2026-05-25", "2026-06-19", "2026-07-03",
        "2026-09-07", "2026-11-26", "2026-12-25", "2027-01-01", "2027-01-18", "2027-02-15", "2027-03-26",
        "2027-05-31", "2027-06-18", "2027-07-05", "2027-09-06", "2027-11-25", "2027-12-24"}


def holidays(conn) -> set[str]:
    if IS_US:
        return NYSE
    hit = cache_get(conn, "nse:holidays", max_age_days=7)
    if hit is None:
        try:
            from .nse_api import NSE
            rows = NSE()._get_json("holiday-master?type=trading").get("CM") or []
            hit = sorted({datetime.strptime(r["tradingDate"], "%d-%b-%Y").date().isoformat() for r in rows})
            cache_put(conn, "nse:holidays", hit)
        except Exception:
            hit = cache_get(conn, "nse:holidays", max_age_days=10_000) or []
    return set(hit)


def _local(iso: str | None):
    if not iso:
        return None
    d = datetime.fromisoformat(iso)
    from zoneinfo import ZoneInfo
    return d.astimezone(ZoneInfo(MARKET["tz"]))


def build(conn, st: dict) -> dict:
    now = market_now()
    sc = st["schedule"]
    last = conn.execute("SELECT id, day, kind, status, started, finished, funnel FROM runs "
                        "WHERE kind IN ('daily','manual') ORDER BY id DESC LIMIT 1").fetchone()
    nxt, why = None, None
    if not sc.get("enabled"):
        why = "Automatic run is off"
    elif not sc.get("days"):
        why = "No days chosen"
    else:
        hol = holidays(conn)
        hh, mm = (int(x) for x in sc.get("time", MARKET["default_time"]).split(":"))
        ran_today = conn.execute("SELECT 1 FROM runs WHERE kind='daily' AND day=? AND status='done'",
                                 (now.date().isoformat(),)).fetchone()
        for i in range(0, 15):
            d = now.date() + timedelta(days=i)
            if d.weekday() not in sc["days"] or d.isoformat() in hol:
                continue
            at = now.replace(year=d.year, month=d.month, day=d.day, hour=hh, minute=mm, second=0, microsecond=0)
            if i == 0 and (ran_today or at < now - timedelta(hours=4)):
                continue        # today's already done (or its window has passed)
            nxt = at
            break
    picks = 0
    if last:
        picks = conn.execute("SELECT count(*) FROM picks WHERE run_id=?", (last["id"],)).fetchone()[0]
    close_h, close_m = (16, 0) if IS_US else (15, 30)
    close = now.replace(hour=close_h, minute=close_m, second=0, microsecond=0)
    trading_today = now.weekday() < 5 and now.date().isoformat() not in holidays(conn)
    return {
        "now": now.isoformat(),
        "last": _local(last["started"]).isoformat() if last else None,
        "last_status": last["status"] if last else None,
        "last_picks": picks, "last_run": last["id"] if last else None,
        "next": nxt.isoformat() if nxt else None, "why": why,
        "close": close.isoformat() if trading_today else None,
        "holiday_today": now.weekday() < 5 and not trading_today,
        "tz": "New York" if IS_US else "India",
    }
