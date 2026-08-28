"""Incremental pull sync (§6.2).

Google wins for anything it authored — that is D2, and it is not negotiable
here. The ``origin = "sundial"`` branches of §6.2's loop are deliberately
absent: they need echo suppression (§6.4.1), which needs a write path, which
is M3. In M1 no Sundial-origin event exists, so those branches are unreachable
rather than unimplemented. Adding them without ``is_own_echo`` would make every
block lock itself within seconds of being written.
"""

from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass, replace
from typing import Any

from sundial.core import events as events_repo
from sundial.core import logging as slog
from sundial.core import store
from sundial.core.models import Event, Kind, Origin, Transparency, new_event_id
from sundial.core.sync_state import SyncState
from sundial.sync_cal.client import CalendarClient, SyncTokenExpiredError

log = logging.getLogger(__name__)

# The window a full resync rebuilds when a sync token expires (§6.2).
RESYNC_PAST = dt.timedelta(days=30)
RESYNC_FUTURE = dt.timedelta(days=180)


@dataclass(frozen=True, slots=True)
class SyncResult:
    calendar_id: str
    created: int = 0
    updated: int = 0
    deleted: int = 0
    skipped_sundial: int = 0
    full_resync: bool = False

    @property
    def blocking_changed(self) -> bool:
        """Whether a RESCHEDULE should be enqueued (§6.2). The queue itself
        arrives with M1 step 4; this is the predicate it will read."""
        return bool(self.created or self.updated or self.deleted)


def _instant(value: dict[str, str]) -> tuple[str, bool]:
    """``(stored value, all_day)`` for a Google start/end object.

    All-day events keep their bare date and are never converted (§6.7).
    """
    if "date" in value:
        return value["date"], True
    moment = dt.datetime.fromisoformat(value["dateTime"])
    return store.iso(moment), False


def normalise(payload: dict[str, Any], calendar_id: str, default_tz: str) -> Event:
    """A Google event as Sundial stores it.

    ``kind`` is not pinned down by the spec. The rule here is that anything
    with attendees is a meeting and therefore an ``appointment``; everything
    else is opaque ``busy`` time. Both block identically — ``transparency``,
    not ``kind``, decides that — so this only affects display.
    """
    start_field: dict[str, str] = payload.get("start") or {}
    end_field: dict[str, str] = payload.get("end") or {}
    start, all_day = _instant(start_field)
    end, _ = _instant(end_field)

    return Event(
        event_id=new_event_id(),
        origin=Origin.GOOGLE,
        kind=Kind.APPOINTMENT if payload.get("attendees") else Kind.BUSY,
        google_calendar_id=calendar_id,
        google_event_id=str(payload["id"]),
        google_etag=str(payload.get("etag")) if payload.get("etag") else None,
        ical_uid=str(payload.get("iCalUID")) if payload.get("iCalUID") else None,
        title=str(payload.get("summary") or "(no title)"),
        location=str(payload["location"]) if payload.get("location") else None,
        start=start,
        end=end,
        all_day=all_day,
        tz=str(start_field.get("timeZone") or default_tz),
        transparency=(
            Transparency.FREE
            if payload.get("transparency") == "transparent"
            else Transparency.BUSY
        ),
    )


def _apply(uid: str, incoming: Event, existing: Event | None) -> str:
    """Returns the outcome: ``created`` / ``updated`` / ``skipped_sundial``."""
    if existing is None:
        events_repo.put(uid, incoming)
        return "created"

    if existing.origin is Origin.SUNDIAL:
        # §6.4.1: concluding drift here without is_own_echo would lock every
        # block Sundial writes. Left for M3, with the write path it needs.
        log.warning(
            "sundial-origin event seen on pull; deferred to M3",
            extra=slog.extra(google_event_id=incoming.google_event_id),
        )
        return "skipped_sundial"

    # Google wins, wholesale (§2.1 D2). The local id, and any user-set `locked`
    # flag, survive; every field Google owns is overwritten.
    updated = replace(
        incoming,
        event_id=existing.event_id,
        locked=existing.locked,
        task_id=existing.task_id,
    )
    events_repo.move(uid, existing, updated)
    return "updated"


def sync_calendar(
    uid: str,
    calendar_id: str,
    client: CalendarClient,
    *,
    default_tz: str = "UTC",
    now: dt.datetime | None = None,
) -> SyncResult:
    """One calendar, one incremental pass, resuming from the stored syncToken."""
    state = SyncState.load(uid, calendar_id)
    moment = now or store.now()

    try:
        return _run(uid, calendar_id, client, state, default_tz, moment, full=False)
    except SyncTokenExpiredError:
        # The token expired. Rebuild a bounded window from scratch; this is
        # idempotent because google_event_id is the dedupe key (§6.2).
        log.info("sync token expired, falling back to full resync")
        return _run(uid, calendar_id, client, state.cleared(), default_tz, moment, full=True)


def _run(
    uid: str,
    calendar_id: str,
    client: CalendarClient,
    state: SyncState,
    default_tz: str,
    moment: dt.datetime,
    *,
    full: bool,
) -> SyncResult:
    created = updated = deleted = skipped = 0
    token = state.sync_token

    pages = client.events(
        calendar_id,
        sync_token=None if full else token,
        time_min=store.iso(moment - RESYNC_PAST),
        time_max=store.iso(moment + RESYNC_FUTURE),
    )

    next_token: str | None = None
    for items, page_token in pages:
        if page_token:
            next_token = page_token
        for payload in items:
            google_event_id = str(payload["id"])
            existing = events_repo.by_google_id(uid, google_event_id)

            if payload.get("status") == "cancelled":
                # Soft delete: the row stays for sync reconciliation, and every
                # read filters it out (§3.2).
                if existing is not None and existing.deleted_at is None:
                    events_repo.put(uid, existing.soft_deleted(moment))
                    deleted += 1
                continue

            outcome = _apply(uid, normalise(payload, calendar_id, default_tz), existing)
            created += outcome == "created"
            updated += outcome == "updated"
            skipped += outcome == "skipped_sundial"

    SyncState(
        resource=calendar_id,
        sync_token=next_token or token,
        last_synced_at=store.iso(moment),
    ).save(uid)

    result = SyncResult(
        calendar_id=calendar_id,
        created=created,
        updated=updated,
        deleted=deleted,
        skipped_sundial=skipped,
        full_resync=full,
    )
    log.info(
        "calendar synced",
        extra=slog.extra(
            calendar=calendar_id,
            created=created,
            updated=updated,
            deleted=deleted,
            full_resync=full,
        ),
    )
    return result
