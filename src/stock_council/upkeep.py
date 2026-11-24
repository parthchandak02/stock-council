"""Evening upkeep around the daily screen. Each step is independent: a failure is logged and the
rest (and the screen itself) still runs."""
from __future__ import annotations

import logging

log = logging.getLogger(__name__)


def _step(name, fn, *a, **kw):
    try:
        out = fn(*a, **kw)
        log.info("upkeep %s: %s", name, out)
        return out
    except Exception as e:
        log.exception("upkeep %s failed: %s", name, e)
        return {"error": str(e)[:200]}


def before_screen() -> dict:
    """Things the screen and fact sheets depend on: splits/bonuses, announcements, fresh results."""
    from . import actions, filings
    return {
        "splits_bonuses": _step("corporate actions", actions.update, days_back=40),
        "announcements": _step("announcements", filings.update_announcements, days=2),
        "results_refresh": _step("results-day refresh", filings.refresh_results, days=3),
    }


def after_screen() -> dict:
    """Screener upkeep, after the email has gone: rolling fundamentals, ownership, saved screens."""
    from . import ownership, universe
    from .config import IS_US
    if IS_US:
        return {"fundamentals": _step("fundamentals", universe.refresh, limit=600)}
    return {
        "fundamentals": _step("fundamentals", universe.refresh, limit=600),
        "ownership_bulk": _step("ownership bulk", ownership.refresh_bulk),
        "ownership_detail": _step("ownership detail", ownership.refresh_detail, limit=300),
    }
