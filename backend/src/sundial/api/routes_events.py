"""``GET /events?from=&to=`` (§11) — the merged Sundial + Google view."""

from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Query, Request

from sundial.api.deps import current_uid
from sundial.core import events as events_repo
from sundial.core.errors import ProblemError
from sundial.core.models import Event
from sundial.sync_cal import calendars

router = APIRouter(tags=["events"])

MAX_WINDOW = dt.timedelta(days=90)


def _serialise(event: Event) -> dict[str, object]:
    return {
        "event_id": event.event_id,
        "origin": str(event.origin),
        "kind": str(event.kind),
        "title": event.title,
        "start": event.start,
        "end": event.end,
        "all_day": event.all_day,
        "tz": event.tz,
        "location": event.location,
        "transparency": str(event.transparency),
        "locked": event.locked,
        "task_id": event.task_id,
        "calendar_id": event.google_calendar_id,
    }


@router.get("/events")
def list_events(
    request: Request,
    from_: dt.datetime = Query(alias="from"),
    to: dt.datetime = Query(),
    timezone: str = Query(default="UTC"),
) -> dict[str, object]:
    if to <= from_:
        raise ProblemError(400, "`to` must be after `from`", problem_type="bad-window")
    if to - from_ > MAX_WINDOW:
        raise ProblemError(
            400,
            "Window is too wide",
            f"At most {MAX_WINDOW.days} days may be requested at once.",
            "bad-window",
        )

    uid = current_uid(request)
    try:
        found = events_repo.between(uid, from_, to, timezone=timezone)
    except Exception as exc:  # ZoneInfo raises its own error type for bad zones
        raise ProblemError(
            400, "Unknown time zone", str(exc), problem_type="bad-timezone"
        ) from exc

    primary = calendars.primary_id(calendars.stored(uid))
    return {"events": [_serialise(e) for e in events_repo.deduplicate(found, primary)]}
