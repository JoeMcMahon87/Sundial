"""Calendar discovery, and the one calendar Sundial owns (§6.1).

Sundial creates a single Google calendar named ``Sundial`` at first run and
records its id. **Everything Sundial writes goes there and nowhere else** —
that is what makes an entire class of "did Sundial mangle my work calendar"
bugs impossible. Every other calendar is a read-only source of busy time.
"""

from __future__ import annotations

import logging

from sundial.core import logging as slog
from sundial.core import store
from sundial.core.models import Calendar
from sundial.sync_cal.client import CalendarClient

SUNDIAL_CALENDAR_SUMMARY = "Sundial"

log = logging.getLogger(__name__)


def discover(uid: str, client: CalendarClient) -> list[Calendar]:
    """Refresh the stored calendar list from Google.

    The ``is_sundial`` flag is matched on summary, because the id is exactly
    what is not yet known the first time this runs.
    """
    calendars = [
        Calendar(
            calendar_id=str(entry["id"]),
            summary=str(entry.get("summary", "")),
            primary=bool(entry.get("primary", False)),
            is_sundial=entry.get("summary") == SUNDIAL_CALENDAR_SUMMARY,
            access_role=str(entry.get("accessRole", "reader")),
            time_zone=str(entry.get("timeZone", "UTC")),
        )
        for entry in client.calendar_list()
        if not entry.get("deleted")
    ]

    for calendar in calendars:
        store.put(uid, calendar.sort_key, calendar.to_item())

    log.info("calendars discovered", extra=slog.extra(count=len(calendars)))
    return calendars


def stored(uid: str) -> list[Calendar]:
    return [Calendar.from_item(item) for item in store.query_prefix(uid, "CAL#")]


def primary_id(calendars: list[Calendar]) -> str | None:
    return next((c.calendar_id for c in calendars if c.primary), None)


def sundial_id(calendars: list[Calendar]) -> str | None:
    return next((c.calendar_id for c in calendars if c.is_sundial), None)


def ensure_sundial_calendar(uid: str, client: CalendarClient) -> Calendar:
    """Idempotent: returns the existing ``Sundial`` calendar or creates it.

    Creation is followed by a re-discovery rather than by constructing the
    Calendar locally, so the stored ``accessRole`` and ``timeZone`` are
    Google's values and not a guess.
    """
    calendars = discover(uid, client)
    existing = next((c for c in calendars if c.is_sundial), None)
    if existing is not None:
        return existing

    time_zone = next((c.time_zone for c in calendars if c.primary), "UTC")
    created = client.create_calendar(SUNDIAL_CALENDAR_SUMMARY, time_zone)
    log.info("created the Sundial calendar")

    refreshed = discover(uid, client)
    found = next((c for c in refreshed if c.calendar_id == created["id"]), None)
    if found is not None:
        return found

    # calendarList can lag calendars.insert by a beat; the row still has to
    # exist so nothing downstream has to cope with "no Sundial calendar yet".
    fallback = Calendar(
        calendar_id=str(created["id"]),
        summary=SUNDIAL_CALENDAR_SUMMARY,
        is_sundial=True,
        access_role="owner",
        time_zone=str(created.get("timeZone", time_zone)),
    )
    store.put(uid, fallback.sort_key, fallback.to_item())
    return fallback
