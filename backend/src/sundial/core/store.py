"""DynamoDB single-table access (§3.2).

The table is ``sundial``; the partition key is coarse by design because there
is exactly one user. Item-shaped repositories live next to the code that owns
them — this module holds only the primitives and the key builders, so that
sort-key construction is in one place rather than sprinkled across handlers.
"""

from __future__ import annotations

import datetime as dt
from typing import TYPE_CHECKING, Any

import boto3

from sundial.core.config import settings

if TYPE_CHECKING:
    from mypy_boto3_dynamodb.service_resource import Table


def pk(uid: str) -> str:
    return f"USER#{uid}"


def sk_task(task_id: str) -> str:
    return f"TASK#{task_id}"


def sk_event(iso_start: str, event_id: str) -> str:
    """Event sort keys embed the start time, which is why a reschedule is a
    delete + put and never an update (§3.2, invariant 5)."""
    return f"EVENT#{iso_start}#{event_id}"


def sk_policy() -> str:
    return "POLICY#v1"


def sk_auth() -> str:
    return "AUTH#google"


def sk_sync(resource: str) -> str:
    return f"SYNC#{resource}"


def sk_idempotency(key: str) -> str:
    return f"IDEM#{key}"


def now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


def iso(moment: dt.datetime) -> str:
    return moment.astimezone(dt.UTC).isoformat().replace("+00:00", "Z")


_table: Table | None = None


def table() -> Table:
    """Cached per execution environment; boto3 clients are expensive to build."""
    global _table
    if _table is None:
        _table = boto3.resource("dynamodb").Table(settings().table_name)
    return _table


def get(uid: str, sort_key: str) -> dict[str, Any] | None:
    response = table().get_item(Key={"PK": pk(uid), "SK": sort_key})
    return response.get("Item")


def put(uid: str, sort_key: str, item: dict[str, Any]) -> None:
    table().put_item(Item={"PK": pk(uid), "SK": sort_key, **item})
