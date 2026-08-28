"""Reading and writing Event items (§3.2).

Two rules are enforced here rather than left to call sites, because both fail
silently everywhere else:

* **Soft-deleted events stay inside the sort-key range.** Every read filters
  them out; missing the filter shows up as phantom busy time, not an error.
* **Moving an event is a delete + put.** The sort key embeds the start, so an
  ``UpdateExpression`` on ``start`` would leave the old item behind and mint a
  duplicate.
"""

from __future__ import annotations

import datetime as dt
from typing import Any
from zoneinfo import ZoneInfo

from boto3.dynamodb.conditions import Attr, Key

from sundial.core import store
from sundial.core.models import Event, sort_start

_NOT_DELETED = Attr("deleted_at").not_exists()


def get(uid: str, event_id: str, *, start: str, all_day: bool) -> Event | None:
    item = store.get(uid, store.sk_event(sort_start(start, all_day), event_id))
    if item is None or "deleted_at" in item:
        return None
    return Event.from_item(item)


def by_google_id(uid: str, google_event_id: str) -> Event | None:
    """The GSI1 lookup run on every inbound sync page (§3.2).

    Soft-deleted rows *are* returned here: sync reconciliation needs to know
    that Sundial has seen this Google id before, which is the whole reason the
    row is kept.
    """
    response = store.table().query(
        IndexName="GSI1",
        KeyConditionExpression=Key("GSI1PK").eq(f"GEVT#{google_event_id}"),
        # The index is not partitioned by user; the filter is what keeps this
        # honest if Sundial ever stops being single-user.
        FilterExpression=Attr("PK").eq(store.pk(uid)),
        Limit=1,
    )
    items = response.get("Items", [])
    return Event.from_item(items[0]) if items else None


def put(uid: str, event: Event) -> None:
    store.put(uid, event.sort_key, event.to_item())


def move(uid: str, previous: Event, updated: Event) -> None:
    """Reschedule as a transactional delete + put (§3.2).

    A no-op when the key is unchanged, so callers do not have to check.
    """
    if previous.sort_key == updated.sort_key:
        put(uid, updated)
        return

    store.table().meta.client.transact_write_items(
        TransactItems=[
            {
                "Delete": {
                    "TableName": store.table().name,
                    "Key": {"PK": store.pk(uid), "SK": previous.sort_key},
                }
            },
            {
                "Put": {
                    "TableName": store.table().name,
                    "Item": {
                        "PK": store.pk(uid),
                        "SK": updated.sort_key,
                        **updated.to_item(),
                    },
                }
            },
        ]
    )


def between(
    uid: str,
    start: dt.datetime,
    end: dt.datetime,
    *,
    timezone: str = "UTC",
    include_deleted: bool = False,
) -> list[Event]:
    """Events overlapping ``[start, end)``.

    The sort-key window is widened by a day at each edge before filtering. An
    all-day event is keyed at ``<date>T00:00:00Z``, which is not the same
    instant as local midnight anywhere but UTC, so a naive range read drops
    all-day events for any user west of Greenwich and duplicates them for any
    user east of it. Widening and re-filtering with date semantics is the
    correction (§6.7).
    """
    zone = ZoneInfo(timezone)
    low = store.iso(start - dt.timedelta(days=1))
    high = store.iso(end + dt.timedelta(days=1))

    kwargs: dict[str, Any] = {
        "KeyConditionExpression": Key("PK").eq(store.pk(uid))
        & Key("SK").between(f"EVENT#{low}", f"EVENT#{high}"),
    }
    if not include_deleted:
        kwargs["FilterExpression"] = _NOT_DELETED

    items: list[dict[str, Any]] = []
    while True:
        response = store.table().query(**kwargs)
        items.extend(response.get("Items", []))
        cursor = response.get("LastEvaluatedKey")
        if not cursor:
            break
        kwargs["ExclusiveStartKey"] = cursor

    events = [Event.from_item(item) for item in items]
    return [event for event in events if _overlaps(event, start, end, zone)]


def _overlaps(event: Event, start: dt.datetime, end: dt.datetime, zone: ZoneInfo) -> bool:
    if event.all_day:
        # Both sides are half-open. Google's all-day end date is already
        # exclusive; the requested window's end has to be rounded *up* to a
        # date, or a window ending at local midnight wrongly claims the
        # following day — which is precisely the off-by-one that makes an
        # all-day event appear on two days for a user east of UTC.
        local_from = start.astimezone(zone).date()
        end_local = end.astimezone(zone)
        local_to = end_local.date()
        if end_local.time() != dt.time.min:
            local_to += dt.timedelta(days=1)

        event_from = dt.date.fromisoformat(event.start)
        event_to = dt.date.fromisoformat(event.end)
        return event_from < local_to and event_to > local_from

    event_from = dt.datetime.fromisoformat(event.start)
    event_to = dt.datetime.fromisoformat(event.end)
    return event_from < end and event_to > start


def deduplicate(events: list[Event], primary_calendar_id: str | None) -> list[Event]:
    """Collapse the invite-plus-personal-copy case (§6.5).

    The same meeting on two calendars shares an ``iCalUID``; the copy on the
    primary calendar wins. Both rows stay in the table — dropping one would
    make the next sync recreate it — so the dedupe happens on read.
    """
    by_uid: dict[str, Event] = {}
    passthrough: list[Event] = []

    for event in events:
        if not event.ical_uid:
            passthrough.append(event)
            continue
        incumbent = by_uid.get(event.ical_uid)
        if incumbent is None or (
            primary_calendar_id is not None
            and event.google_calendar_id == primary_calendar_id
            and incumbent.google_calendar_id != primary_calendar_id
        ):
            by_uid[event.ical_uid] = event

    combined = passthrough + list(by_uid.values())
    return sorted(combined, key=lambda e: (sort_start(e.start, e.all_day), e.event_id))
