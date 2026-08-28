"""Domain entities that outlive a single request (§3.1).

Serialisation is explicit rather than inferred: DynamoDB hands back ``Decimal``
for every number and drops empty values, so a round-trip through an inferred
mapper is where the surprises live.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field, replace
from enum import StrEnum
from typing import Any, Self

from ulid import ULID

from sundial.core import store


class Origin(StrEnum):
    """Immutable after creation, and it decides authority (§2.1).

    ``sundial`` means Sundial writes to Google; ``google`` means Google
    overwrites Sundial. There is exactly one writer per event.
    """

    SUNDIAL = "sundial"
    GOOGLE = "google"


class Kind(StrEnum):
    APPOINTMENT = "appointment"
    BLOCK = "block"
    BUSY = "busy"


class Transparency(StrEnum):
    BUSY = "busy"
    FREE = "free"


def new_event_id() -> str:
    return str(ULID())


def sort_start(start: str, all_day: bool) -> str:
    """The instant embedded in the ``EVENT#`` sort key.

    All-day events are stored date-only and never converted (§6.7), but a sort
    key has to be comparable against timed events, so the *key* uses
    ``<date>T00:00:00Z`` while the ``start`` attribute keeps the bare date.
    This is a lexicographic device, not a timezone conversion — which is why
    range reads widen the window and then re-filter with date semantics; see
    ``events.between``.
    """
    return f"{start}T00:00:00Z" if all_day else start


@dataclass(frozen=True, slots=True)
class Event:
    event_id: str
    origin: Origin
    kind: Kind
    google_calendar_id: str
    title: str
    start: str
    """UTC instant (``...Z``) when timed; a bare ``YYYY-MM-DD`` when all-day."""
    end: str
    tz: str
    """The IANA zone the event was authored in (§6.7)."""
    all_day: bool = False
    location: str | None = None
    google_event_id: str | None = None
    google_etag: str | None = None
    ical_uid: str | None = None
    task_id: str | None = None
    transparency: Transparency = Transparency.BUSY
    locked: bool = False
    deleted_at: str | None = None
    updated_at: str = field(default_factory=lambda: store.iso(store.now()))

    @property
    def sort_key(self) -> str:
        return store.sk_event(sort_start(self.start, self.all_day), self.event_id)

    @property
    def blocks_time(self) -> bool:
        return self.transparency is Transparency.BUSY and self.deleted_at is None

    def soft_deleted(self, when: dt.datetime | None = None) -> Self:
        """Soft delete, because sync reconciliation still needs the row (§3.1)."""
        return replace(self, deleted_at=store.iso(when or store.now()))

    def to_item(self) -> dict[str, Any]:
        item: dict[str, Any] = {
            "event_id": self.event_id,
            "origin": str(self.origin),
            "kind": str(self.kind),
            "google_calendar_id": self.google_calendar_id,
            "title": self.title,
            "start": self.start,
            "end": self.end,
            "tz": self.tz,
            "all_day": self.all_day,
            "transparency": str(self.transparency),
            "locked": self.locked,
            "updated_at": self.updated_at,
        }
        for name in ("location", "google_event_id", "google_etag", "ical_uid", "task_id"):
            value = getattr(self, name)
            if value is not None:
                item[name] = value

        # Absence, not an explicit null: DynamoDB filter expressions can test
        # `attribute_not_exists` but cannot compare against NULL (§3.2).
        if self.deleted_at is not None:
            item["deleted_at"] = self.deleted_at

        if self.google_event_id:
            # GSI1 answers "do I already know this event?" on every sync page.
            item["GSI1PK"] = f"GEVT#{self.google_event_id}"
            item["GSI1SK"] = self.event_id
        return item

    @classmethod
    def from_item(cls, item: dict[str, Any]) -> Self:
        return cls(
            event_id=str(item["event_id"]),
            origin=Origin(item["origin"]),
            kind=Kind(item["kind"]),
            google_calendar_id=str(item["google_calendar_id"]),
            title=str(item.get("title", "")),
            start=str(item["start"]),
            end=str(item["end"]),
            tz=str(item["tz"]),
            all_day=bool(item.get("all_day", False)),
            location=item.get("location"),
            google_event_id=item.get("google_event_id"),
            google_etag=item.get("google_etag"),
            ical_uid=item.get("ical_uid"),
            task_id=item.get("task_id"),
            transparency=Transparency(item.get("transparency", "busy")),
            locked=bool(item.get("locked", False)),
            deleted_at=item.get("deleted_at"),
            updated_at=str(item.get("updated_at", "")),
        )


@dataclass(frozen=True, slots=True)
class Calendar:
    """A Google calendar Sundial knows about.

    §3.2's entity table has no Calendar row, but §6.1 requires recording the
    id of the ``Sundial`` calendar and §3.1's ``blocking_calendar_ids``
    presupposes a discovered list. The spec needs a row adding for this.
    """

    calendar_id: str
    summary: str
    primary: bool = False
    is_sundial: bool = False
    access_role: str = "reader"
    time_zone: str = "UTC"

    @property
    def sort_key(self) -> str:
        return store.sk_calendar(self.calendar_id)

    def to_item(self) -> dict[str, Any]:
        return {
            "calendar_id": self.calendar_id,
            "summary": self.summary,
            "primary": self.primary,
            "is_sundial": self.is_sundial,
            "access_role": self.access_role,
            "time_zone": self.time_zone,
        }

    @classmethod
    def from_item(cls, item: dict[str, Any]) -> Self:
        return cls(
            calendar_id=str(item["calendar_id"]),
            summary=str(item.get("summary", "")),
            primary=bool(item.get("primary", False)),
            is_sundial=bool(item.get("is_sundial", False)),
            access_role=str(item.get("access_role", "reader")),
            time_zone=str(item.get("time_zone", "UTC")),
        )
