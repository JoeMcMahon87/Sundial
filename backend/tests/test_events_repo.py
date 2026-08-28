"""Event storage: the soft-delete filter, delete + put, and all-day windows."""

from __future__ import annotations

import datetime as dt
from typing import Any

import pytest
from tests.conftest import UID

from sundial.core import events as repo
from sundial.core.models import Event, Kind, Origin, Transparency, new_event_id


def timed(start: str, end: str, **overrides: Any) -> Event:
    return Event(
        event_id=overrides.pop("event_id", new_event_id()),
        origin=Origin.GOOGLE,
        kind=Kind.APPOINTMENT,
        google_calendar_id=overrides.pop("calendar_id", "primary"),
        title=overrides.pop("title", "Standup"),
        start=start,
        end=end,
        tz=overrides.pop("tz", "UTC"),
        **overrides,
    )


def all_day(date: str, end_date: str, **overrides: Any) -> Event:
    return Event(
        event_id=new_event_id(),
        origin=Origin.GOOGLE,
        kind=Kind.BUSY,
        google_calendar_id="primary",
        title=overrides.pop("title", "Conference"),
        start=date,
        end=end_date,
        all_day=True,
        tz=overrides.pop("tz", "America/Los_Angeles"),
        **overrides,
    )


def window(day: str, timezone: str = "UTC") -> tuple[dt.datetime, dt.datetime]:
    from zoneinfo import ZoneInfo

    zone = ZoneInfo(timezone)
    start = dt.datetime.fromisoformat(day).replace(tzinfo=zone)
    return start, start + dt.timedelta(days=1)


def test_round_trips_through_the_item_shape(aws: dict[str, Any]) -> None:
    event = timed("2026-08-28T16:00:00Z", "2026-08-28T17:00:00Z", location="Room 2")
    repo.put(UID, event)

    found = repo.get(UID, event.event_id, start=event.start, all_day=False)
    assert found == event


def test_soft_deleted_events_are_filtered_from_range_reads(aws: dict[str, Any]) -> None:
    """The row stays inside the sort-key range, so a missing filter shows up as
    phantom busy time rather than as an error (§3.2)."""
    live = timed("2026-08-28T16:00:00Z", "2026-08-28T17:00:00Z", title="Live")
    gone = timed("2026-08-28T18:00:00Z", "2026-08-28T19:00:00Z", title="Cancelled")
    repo.put(UID, live)
    repo.put(UID, gone.soft_deleted())

    found = repo.between(UID, *window("2026-08-28"))
    assert [e.title for e in found] == ["Live"]

    assert len(repo.between(UID, *window("2026-08-28"), include_deleted=True)) == 2


def test_soft_deleted_events_are_not_returned_by_point_reads(aws: dict[str, Any]) -> None:
    event = timed("2026-08-28T16:00:00Z", "2026-08-28T17:00:00Z")
    repo.put(UID, event.soft_deleted())
    assert repo.get(UID, event.event_id, start=event.start, all_day=False) is None


def test_moving_an_event_leaves_no_duplicate(aws: dict[str, Any]) -> None:
    """The sort key embeds the start, so a reschedule is delete + put. An
    UpdateExpression would leave the old item in the range (§3.2)."""
    from dataclasses import replace

    original = timed("2026-08-28T16:00:00Z", "2026-08-28T17:00:00Z")
    repo.put(UID, original)

    moved = replace(original, start="2026-08-28T20:00:00Z", end="2026-08-28T21:00:00Z")
    repo.move(UID, original, moved)

    found = repo.between(UID, *window("2026-08-28"))
    assert len(found) == 1
    assert found[0].start == "2026-08-28T20:00:00Z"
    assert found[0].event_id == original.event_id


def test_gsi1_finds_an_event_by_its_google_id(aws: dict[str, Any]) -> None:
    event = timed("2026-08-28T16:00:00Z", "2026-08-28T17:00:00Z", google_event_id="abc123")
    repo.put(UID, event)

    assert repo.by_google_id(UID, "abc123") is not None
    assert repo.by_google_id(UID, "nope") is None


def test_gsi1_still_finds_soft_deleted_events(aws: dict[str, Any]) -> None:
    """Sync reconciliation needs to know Sundial has seen this id before —
    that is the reason the row is kept at all."""
    event = timed("2026-08-28T16:00:00Z", "2026-08-28T17:00:00Z", google_event_id="abc123")
    repo.put(UID, event.soft_deleted())
    assert repo.by_google_id(UID, "abc123") is not None


def test_events_outside_the_window_are_excluded(aws: dict[str, Any]) -> None:
    repo.put(UID, timed("2026-08-27T16:00:00Z", "2026-08-27T17:00:00Z", title="Yesterday"))
    repo.put(UID, timed("2026-08-29T16:00:00Z", "2026-08-29T17:00:00Z", title="Tomorrow"))
    repo.put(UID, timed("2026-08-28T16:00:00Z", "2026-08-28T17:00:00Z", title="Today"))

    assert [e.title for e in repo.between(UID, *window("2026-08-28"))] == ["Today"]


def test_an_event_straddling_the_window_edge_is_included(aws: dict[str, Any]) -> None:
    repo.put(UID, timed("2026-08-27T23:00:00Z", "2026-08-28T01:00:00Z", title="Overnight"))
    assert [e.title for e in repo.between(UID, *window("2026-08-28"))] == ["Overnight"]


def test_all_day_events_survive_a_western_timezone(aws: dict[str, Any]) -> None:
    """The regression this exists for: an all-day event is keyed at
    `<date>T00:00:00Z`, which is 17:00 the *previous* day in Los Angeles. A
    naive range read drops it entirely (§6.7)."""
    repo.put(UID, all_day("2026-08-28", "2026-08-29"))

    found = repo.between(
        UID, *window("2026-08-28", "America/Los_Angeles"), timezone="America/Los_Angeles"
    )
    assert [e.title for e in found] == ["Conference"]


def test_all_day_events_survive_an_eastern_timezone(aws: dict[str, Any]) -> None:
    repo.put(UID, all_day("2026-08-28", "2026-08-29", tz="Asia/Tokyo"))

    found = repo.between(UID, *window("2026-08-28", "Asia/Tokyo"), timezone="Asia/Tokyo")
    assert [e.title for e in found] == ["Conference"]

    neighbour = repo.between(UID, *window("2026-08-27", "Asia/Tokyo"), timezone="Asia/Tokyo")
    assert neighbour == []


def test_all_day_end_date_is_exclusive(aws: dict[str, Any]) -> None:
    """Google's all-day end date is the day *after* the last day."""
    repo.put(UID, all_day("2026-08-28", "2026-08-29"))
    assert repo.between(UID, *window("2026-08-29")) == []


@pytest.mark.parametrize("day", ["2026-03-07", "2026-03-08", "2026-03-09"])
def test_reads_are_correct_across_spring_forward(aws: dict[str, Any], day: str) -> None:
    """2026-03-08 is the US spring-forward day: 02:00 local does not exist and
    the day is 23 hours long (§14 requires this as a fixture, not an
    afterthought)."""
    repo.put(UID, timed("2026-03-08T17:00:00Z", "2026-03-08T18:00:00Z", title="Brunch"))
    repo.put(UID, all_day("2026-03-08", "2026-03-09", title="Race day"))

    found = repo.between(
        UID, *window(day, "America/Los_Angeles"), timezone="America/Los_Angeles"
    )
    titles = sorted(e.title for e in found)
    assert titles == (["Brunch", "Race day"] if day == "2026-03-08" else [])


def test_duplicate_invites_are_deduped_in_favour_of_the_primary_copy() -> None:
    """The same meeting on two calendars shares an iCalUID (§6.5)."""
    invite = timed(
        "2026-08-28T16:00:00Z",
        "2026-08-28T17:00:00Z",
        calendar_id="work@example.com",
        ical_uid="shared-uid",
        title="Invite copy",
    )
    personal = timed(
        "2026-08-28T16:00:00Z",
        "2026-08-28T17:00:00Z",
        calendar_id="primary",
        ical_uid="shared-uid",
        title="Primary copy",
    )

    assert [e.title for e in repo.deduplicate([invite, personal], "primary")] == [
        "Primary copy"
    ]
    assert [e.title for e in repo.deduplicate([personal, invite], "primary")] == [
        "Primary copy"
    ]


def test_events_without_an_ical_uid_are_never_collapsed() -> None:
    first = timed("2026-08-28T16:00:00Z", "2026-08-28T17:00:00Z", title="One")
    second = timed("2026-08-28T18:00:00Z", "2026-08-28T19:00:00Z", title="Two")
    assert len(repo.deduplicate([first, second], "primary")) == 2


def test_transparent_events_do_not_block(aws: dict[str, Any]) -> None:
    free = timed("2026-08-28T16:00:00Z", "2026-08-28T17:00:00Z", transparency=Transparency.FREE)
    assert free.blocks_time is False
    assert timed("2026-08-28T16:00:00Z", "2026-08-28T17:00:00Z").blocks_time is True
    assert free.soft_deleted().blocks_time is False
