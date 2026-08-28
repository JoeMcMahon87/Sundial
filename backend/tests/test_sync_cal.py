"""Calendar discovery and the §6.2 pull loop, against recorded Google shapes."""

from __future__ import annotations

import datetime as dt
from typing import Any

import httpx
import pytest
import respx
from tests.conftest import UID

from sundial.core import events as events_repo
from sundial.core.models import Kind, Origin, Transparency
from sundial.core.sync_state import SyncState
from sundial.oauth import tokens
from sundial.sync_cal import calendars
from sundial.sync_cal.client import CalendarClient, RateLimitedError
from sundial.sync_cal.pull import normalise, sync_calendar

EVENTS_URL = "https://www.googleapis.com/calendar/v3/calendars/primary/events"
CALENDAR_LIST_URL = "https://www.googleapis.com/calendar/v3/users/me/calendarList"
CALENDARS_URL = "https://www.googleapis.com/calendar/v3/calendars"
TOKEN_URL = "https://oauth2.googleapis.com/token"

NOW = dt.datetime(2026, 8, 28, 12, 0, tzinfo=dt.UTC)

# Pacific wall-clock windows on the sync day, so the UTC conversion in §6.7
# is exercised rather than sidestepped.
MORNING = ("2026-08-28T09:00:00-07:00", "2026-08-28T10:00:00-07:00")
MIDDAY = ("2026-08-28T11:00:00-07:00", "2026-08-28T12:00:00-07:00")
AFTERNOON = ("2026-08-28T14:00:00-07:00", "2026-08-28T15:00:00-07:00")
UTC_MORNING = ("2026-08-28T09:00:00Z", "2026-08-28T10:00:00Z")


@pytest.fixture
def connected(aws: dict[str, Any]) -> None:
    tokens.save(
        UID,
        refresh_token="1//refresh",
        google_account_id=UID,
        email="someone@example.com",
        scopes=("openid",),
    )
    tokens.cache_access_token(UID, "ya29.access", expires_in=3600)


@pytest.fixture
def client(connected: None) -> CalendarClient:
    # No real sleeping in the backoff path.
    return CalendarClient(UID, sleep=lambda _seconds: None)


def timed_event(event_id: str, window: tuple[str, str], **extra: Any) -> dict[str, Any]:
    return {
        "id": event_id,
        "etag": f'"{event_id}-1"',
        "status": "confirmed",
        "summary": extra.pop("summary", "Standup"),
        "iCalUID": extra.pop("ical_uid", f"{event_id}@google.com"),
        "start": {"dateTime": window[0], "timeZone": "America/Los_Angeles"},
        "end": {"dateTime": window[1], "timeZone": "America/Los_Angeles"},
        **extra,
    }


def page(*items: dict[str, Any], **tokens: str) -> dict[str, Any]:
    return {"items": list(items), **tokens}


def calendar_entry(calendar_id: str, summary: str, **extra: Any) -> dict[str, Any]:
    return {
        "id": calendar_id,
        "summary": summary,
        "timeZone": "America/Los_Angeles",
        "accessRole": "owner",
        **extra,
    }


# --- normalisation ----------------------------------------------------------


def test_timed_events_are_stored_as_utc_with_the_authored_zone() -> None:
    """§6.7: store UTC, and also store the zone the event was authored in."""
    event = normalise(
        timed_event("a", MORNING),
        "primary",
        "UTC",
    )
    assert event.start == "2026-08-28T16:00:00Z"
    assert event.tz == "America/Los_Angeles"
    assert event.all_day is False


def test_all_day_events_are_date_only_and_never_converted() -> None:
    event = normalise(
        {
            "id": "b",
            "status": "confirmed",
            "summary": "Conference",
            "start": {"date": "2026-08-28"},
            "end": {"date": "2026-08-29"},
        },
        "primary",
        "America/Los_Angeles",
    )
    assert (event.start, event.end, event.all_day) == ("2026-08-28", "2026-08-29", True)
    assert event.tz == "America/Los_Angeles"


def test_origin_is_always_google_on_the_pull_path() -> None:
    event = normalise(timed_event("a", UTC_MORNING), "c", "UTC")
    assert event.origin is Origin.GOOGLE


def test_events_with_attendees_are_appointments_and_others_are_busy() -> None:
    meeting = normalise(
        timed_event("a", UTC_MORNING, attendees=[{"email": "x"}]),
        "primary",
        "UTC",
    )
    solo = normalise(timed_event("b", UTC_MORNING), "primary", "UTC")
    assert (meeting.kind, solo.kind) == (Kind.APPOINTMENT, Kind.BUSY)


def test_transparent_events_are_marked_free() -> None:
    event = normalise(
        timed_event("a", UTC_MORNING, transparency="transparent"),
        "primary",
        "UTC",
    )
    assert event.transparency is Transparency.FREE


# --- discovery --------------------------------------------------------------


@respx.mock
def test_discovery_creates_the_sundial_calendar_once(client: CalendarClient) -> None:
    listings = [
        {
            "items": [
                {
                    "id": "primary",
                    "summary": "Joe",
                    "primary": True,
                    "timeZone": "America/Los_Angeles",
                    "accessRole": "owner",
                }
            ]
        },
        {
            "items": [
                {
                    "id": "primary",
                    "summary": "Joe",
                    "primary": True,
                    "timeZone": "America/Los_Angeles",
                    "accessRole": "owner",
                },
                {
                    "id": "sundial@group.calendar.google.com",
                    "summary": "Sundial",
                    "timeZone": "America/Los_Angeles",
                    "accessRole": "owner",
                },
            ]
        },
    ]
    respx.get(CALENDAR_LIST_URL).mock(
        side_effect=[httpx.Response(200, json=page) for page in listings]
    )
    create = respx.post(CALENDARS_URL).mock(
        return_value=httpx.Response(
            200, json={"id": "sundial@group.calendar.google.com", "summary": "Sundial"}
        )
    )

    created = calendars.ensure_sundial_calendar(UID, client)
    assert created.is_sundial and created.calendar_id.startswith("sundial@")
    assert create.call_count == 1

    # Second run finds it and must not create another (§6.1: exactly one).
    respx.get(CALENDAR_LIST_URL).mock(return_value=httpx.Response(200, json=listings[1]))
    again = calendars.ensure_sundial_calendar(UID, client)
    assert again.calendar_id == created.calendar_id
    assert create.call_count == 1


@respx.mock
def test_discovery_stores_every_calendar_and_marks_the_primary(client: CalendarClient) -> None:
    respx.get(CALENDAR_LIST_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "items": [
                    {"id": "primary", "summary": "Joe", "primary": True, "accessRole": "owner"},
                    {"id": "work@example.com", "summary": "Work", "accessRole": "reader"},
                    {"id": "old@example.com", "summary": "Old", "deleted": True},
                ]
            },
        )
    )
    found = calendars.discover(UID, client)

    assert {c.calendar_id for c in found} == {"primary", "work@example.com"}
    assert calendars.primary_id(calendars.stored(UID)) == "primary"


# --- the pull loop ----------------------------------------------------------


@respx.mock
def test_first_sync_creates_events_and_stores_the_token(client: CalendarClient) -> None:
    respx.get(EVENTS_URL).mock(
        return_value=httpx.Response(
            200,
            json=page(
                timed_event("a", MORNING), timed_event("b", MIDDAY), nextSyncToken="token-1"
            ),
        )
    )
    result = sync_calendar(UID, "primary", client, now=NOW)

    assert (result.created, result.updated, result.deleted) == (2, 0, 0)
    assert SyncState.load(UID, "primary").sync_token == "token-1"
    assert result.blocking_changed is True


@respx.mock
def test_second_sync_sends_the_stored_token_and_no_time_window(client: CalendarClient) -> None:
    """Google rejects a request carrying both a syncToken and a time window."""
    route = respx.get(EVENTS_URL).mock(
        return_value=httpx.Response(200, json=page(nextSyncToken="token-2"))
    )
    SyncState(resource="primary", sync_token="token-1").save(UID)

    sync_calendar(UID, "primary", client, now=NOW)

    sent = dict(route.calls.last.request.url.params)
    assert sent["syncToken"] == "token-1"
    assert "timeMin" not in sent and "timeMax" not in sent
    assert sent["singleEvents"] == "true"  # §6.6: no RRULE engine in v1
    assert sent["showDeleted"] == "true"


@respx.mock
def test_a_moved_event_is_updated_in_place_not_duplicated(client: CalendarClient) -> None:
    respx.get(EVENTS_URL).mock(
        return_value=httpx.Response(
            200,
            json=page(timed_event("a", MORNING), nextSyncToken="token-1"),
        )
    )
    sync_calendar(UID, "primary", client, now=NOW)
    original = events_repo.by_google_id(UID, "a")
    assert original is not None

    respx.get(EVENTS_URL).mock(
        return_value=httpx.Response(
            200,
            json=page(timed_event("a", AFTERNOON), nextSyncToken="token-2"),
        )
    )
    result = sync_calendar(UID, "primary", client, now=NOW)

    assert (result.created, result.updated) == (0, 1)
    window_start = dt.datetime(2026, 8, 28, tzinfo=dt.UTC)
    found = events_repo.between(UID, window_start, window_start + dt.timedelta(days=1))
    assert len(found) == 1
    assert found[0].start == "2026-08-28T21:00:00Z"
    assert found[0].event_id == original.event_id  # the local id is stable


@respx.mock
def test_a_cancelled_event_is_soft_deleted(client: CalendarClient) -> None:
    respx.get(EVENTS_URL).mock(
        return_value=httpx.Response(
            200,
            json=page(timed_event("a", MORNING), nextSyncToken="token-1"),
        )
    )
    sync_calendar(UID, "primary", client, now=NOW)

    respx.get(EVENTS_URL).mock(
        return_value=httpx.Response(
            200,
            json=page({"id": "a", "status": "cancelled"}, nextSyncToken="token-2"),
        )
    )
    result = sync_calendar(UID, "primary", client, now=NOW)

    assert result.deleted == 1
    window_start = dt.datetime(2026, 8, 28, tzinfo=dt.UTC)
    assert events_repo.between(UID, window_start, window_start + dt.timedelta(days=1)) == []
    # The row survives, because sync reconciliation still needs it (§3.1).
    assert events_repo.by_google_id(UID, "a") is not None


@respx.mock
def test_cancelling_an_unknown_event_is_not_an_error(client: CalendarClient) -> None:
    respx.get(EVENTS_URL).mock(
        return_value=httpx.Response(
            200,
            json=page({"id": "never-seen", "status": "cancelled"}, nextSyncToken="t"),
        )
    )
    assert sync_calendar(UID, "primary", client, now=NOW).deleted == 0


@respx.mock
def test_expired_sync_token_falls_back_to_a_bounded_full_resync(client: CalendarClient) -> None:
    """410 Gone. The fallback is idempotent because google_event_id is the
    dedupe key (§6.2)."""
    SyncState(resource="primary", sync_token="stale").save(UID)

    responses = [
        httpx.Response(410, json={"error": {"message": "Sync token is no longer valid"}}),
        httpx.Response(
            200,
            json=page(timed_event("a", MORNING), nextSyncToken="fresh"),
        ),
    ]
    route = respx.get(EVENTS_URL).mock(side_effect=responses)

    result = sync_calendar(UID, "primary", client, now=NOW)

    assert result.full_resync is True
    assert result.created == 1
    assert SyncState.load(UID, "primary").sync_token == "fresh"

    retry = dict(route.calls.last.request.url.params)
    assert "syncToken" not in retry
    assert retry["timeMin"] == "2026-07-29T12:00:00Z"  # now - 30 days
    assert retry["timeMax"] == "2027-02-24T12:00:00Z"  # now + 180 days


@respx.mock
def test_full_resync_over_existing_events_creates_no_duplicates(client: CalendarClient) -> None:
    payload = page(timed_event("a", MORNING), nextSyncToken="token-1")
    respx.get(EVENTS_URL).mock(return_value=httpx.Response(200, json=payload))
    sync_calendar(UID, "primary", client, now=NOW)

    respx.get(EVENTS_URL).mock(
        side_effect=[httpx.Response(410, json={}), httpx.Response(200, json=payload)]
    )
    sync_calendar(UID, "primary", client, now=NOW)

    window_start = dt.datetime(2026, 8, 28, tzinfo=dt.UTC)
    assert len(events_repo.between(UID, window_start, window_start + dt.timedelta(days=1))) == 1


@respx.mock
def test_rate_limiting_is_retried_then_succeeds(client: CalendarClient) -> None:
    rate_limited = httpx.Response(
        403, json={"error": {"errors": [{"reason": "rateLimitExceeded"}], "code": 403}}
    )
    respx.get(EVENTS_URL).mock(
        side_effect=[
            rate_limited,
            rate_limited,
            httpx.Response(200, json=page(nextSyncToken="token-1")),
        ]
    )
    assert sync_calendar(UID, "primary", client, now=NOW).created == 0


@respx.mock
def test_persistent_rate_limiting_raises_rather_than_looping(client: CalendarClient) -> None:
    respx.get(EVENTS_URL).mock(
        return_value=httpx.Response(429, json={"error": {"message": "slow down"}})
    )
    with pytest.raises(RateLimitedError):
        sync_calendar(UID, "primary", client, now=NOW)


@respx.mock
def test_a_403_that_is_not_a_rate_limit_is_not_retried(client: CalendarClient) -> None:
    """An insufficient-scope 403 would otherwise be retried five times before
    surfacing, which turns a clear error into a slow one."""
    route = respx.get(EVENTS_URL).mock(
        return_value=httpx.Response(
            403, json={"error": {"errors": [{"reason": "insufficientPermissions"}]}}
        )
    )
    with pytest.raises(httpx.HTTPStatusError):
        sync_calendar(UID, "primary", client, now=NOW)
    assert route.call_count == 1


@respx.mock
def test_paging_collects_every_page_and_the_final_token(client: CalendarClient) -> None:
    respx.get(EVENTS_URL).mock(
        side_effect=[
            httpx.Response(
                200,
                json=page(
                    timed_event("a", MORNING),
                    nextPageToken="page-2",
                ),
            ),
            httpx.Response(
                200,
                json=page(
                    timed_event("b", MIDDAY),
                    nextSyncToken="token-final",
                ),
            ),
        ]
    )
    result = sync_calendar(UID, "primary", client, now=NOW)

    assert result.created == 2
    assert SyncState.load(UID, "primary").sync_token == "token-final"


@respx.mock
def test_a_sundial_origin_event_is_skipped_until_m3(client: CalendarClient) -> None:
    """Concluding drift here without is_own_echo (§6.4.1) would lock every
    block Sundial writes within seconds. The branch waits for the write path."""
    from sundial.core.models import Event

    events_repo.put(
        UID,
        Event(
            event_id="01J000000000000000000000",
            origin=Origin.SUNDIAL,
            kind=Kind.BLOCK,
            google_calendar_id="primary",
            google_event_id="a",
            title="Deep work",
            start="2026-08-28T16:00:00Z",
            end="2026-08-28T17:00:00Z",
            tz="America/Los_Angeles",
        ),
    )
    respx.get(EVENTS_URL).mock(
        return_value=httpx.Response(
            200,
            json=page(timed_event("a", AFTERNOON), nextSyncToken="token-1"),
        )
    )
    result = sync_calendar(UID, "primary", client, now=NOW)

    assert result.skipped_sundial == 1
    assert result.updated == 0
    unchanged = events_repo.by_google_id(UID, "a")
    assert unchanged is not None
    assert unchanged.start == "2026-08-28T16:00:00Z"
    assert unchanged.locked is False
