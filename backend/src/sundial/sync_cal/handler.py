"""The ``sync_cal`` function.

M1 step 4 drives this from SQS behind the Google webhook plus a safety-net
EventBridge rule. Until then it is invoked directly, and locally via
``python -m sundial.sync_cal``.
"""

from __future__ import annotations

import logging
from typing import Any

from sundial.core import logging as slog
from sundial.core.config import settings
from sundial.sync_cal import calendars
from sundial.sync_cal.client import CalendarClient
from sundial.sync_cal.pull import SyncResult, sync_calendar

slog.configure()
log = logging.getLogger(__name__)


def run(uid: str) -> list[SyncResult]:
    """Discover calendars, ensure the Sundial calendar exists, then pull each.

    Every calendar is read. Which of them count as *blocking* is a policy
    question (`blocking_calendar_ids`, §3.1) answered at scheduling time, not
    at sync time — reading them all keeps that decision reversible without a
    resync.
    """
    client = CalendarClient(uid)
    calendars.ensure_sundial_calendar(uid, client)

    results: list[SyncResult] = []
    for calendar in calendars.stored(uid):
        results.append(
            sync_calendar(uid, calendar.calendar_id, client, default_tz=calendar.time_zone)
        )
    return results


def handler(event: dict[str, Any], _context: object) -> dict[str, Any]:
    slog.set_correlation_id(str(event.get("correlation_id", "-")))
    uid = str(event.get("uid") or settings().allowed_google_account_id)
    results = run(uid)
    return {
        "calendars": len(results),
        "created": sum(r.created for r in results),
        "updated": sum(r.updated for r in results),
        "deleted": sum(r.deleted for r in results),
    }
