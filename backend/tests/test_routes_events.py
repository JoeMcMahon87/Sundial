"""``GET /events`` (§11)."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient
from tests.conftest import UID
from tests.test_events_repo import all_day, timed

from sundial.core import events as events_repo
from sundial.core import store
from sundial.core.models import Calendar
from sundial.oauth import session


@pytest.fixture
def client(aws: dict[str, Any]) -> TestClient:
    from sundial.api.app import create_app

    signed_in = TestClient(create_app(), base_url="http://localhost:5173/api")
    signed_in.cookies.set("sundial_session", session.mint(UID))
    return signed_in


def test_requires_a_session(aws: dict[str, Any]) -> None:
    from sundial.api.app import create_app

    anonymous = TestClient(create_app(), base_url="http://localhost:5173/api")
    response = anonymous.get("/events?from=2026-08-28T00:00:00Z&to=2026-08-29T00:00:00Z")
    assert response.status_code == 401


def test_returns_events_in_the_window(client: TestClient) -> None:
    events_repo.put(UID, timed("2026-08-28T16:00:00Z", "2026-08-28T17:00:00Z", title="Standup"))
    events_repo.put(UID, timed("2026-08-30T16:00:00Z", "2026-08-30T17:00:00Z", title="Later"))

    body = client.get("/events?from=2026-08-28T00:00:00Z&to=2026-08-29T00:00:00Z").json()
    assert [e["title"] for e in body["events"]] == ["Standup"]


def test_soft_deleted_events_never_appear(client: TestClient) -> None:
    """The failure mode is phantom busy time, not an error (§3.2)."""
    events_repo.put(UID, timed("2026-08-28T16:00:00Z", "2026-08-28T17:00:00Z").soft_deleted())
    body = client.get("/events?from=2026-08-28T00:00:00Z&to=2026-08-29T00:00:00Z").json()
    assert body["events"] == []


def test_duplicate_invites_are_collapsed(client: TestClient) -> None:
    store.put(UID, "CAL#primary", Calendar("primary", "Joe", primary=True).to_item())
    for calendar_id, title in (("work@example.com", "Invite"), ("primary", "Primary")):
        events_repo.put(
            UID,
            timed(
                "2026-08-28T16:00:00Z",
                "2026-08-28T17:00:00Z",
                calendar_id=calendar_id,
                ical_uid="shared",
                title=title,
            ),
        )

    body = client.get("/events?from=2026-08-28T00:00:00Z&to=2026-08-29T00:00:00Z").json()
    assert [e["title"] for e in body["events"]] == ["Primary"]


def test_all_day_events_respect_the_requested_zone(client: TestClient) -> None:
    events_repo.put(UID, all_day("2026-08-28", "2026-08-29"))

    inside = client.get(
        "/events?from=2026-08-28T07:00:00Z&to=2026-08-29T07:00:00Z&timezone=America/Los_Angeles"
    ).json()
    assert [e["title"] for e in inside["events"]] == ["Conference"]

    outside = client.get(
        "/events?from=2026-08-29T07:00:00Z&to=2026-08-30T07:00:00Z&timezone=America/Los_Angeles"
    ).json()
    assert outside["events"] == []


def test_results_are_sorted_by_start(client: TestClient) -> None:
    events_repo.put(UID, timed("2026-08-28T18:00:00Z", "2026-08-28T19:00:00Z", title="Later"))
    events_repo.put(UID, timed("2026-08-28T16:00:00Z", "2026-08-28T17:00:00Z", title="Earlier"))
    events_repo.put(UID, all_day("2026-08-28", "2026-08-29", title="All day"))

    body = client.get("/events?from=2026-08-28T00:00:00Z&to=2026-08-29T00:00:00Z").json()
    assert [e["title"] for e in body["events"]] == ["All day", "Earlier", "Later"]


def test_a_backwards_window_is_rejected(client: TestClient) -> None:
    response = client.get("/events?from=2026-08-29T00:00:00Z&to=2026-08-28T00:00:00Z")
    assert response.status_code == 400
    assert response.headers["content-type"].startswith("application/problem+json")


def test_an_absurd_window_is_rejected(client: TestClient) -> None:
    response = client.get("/events?from=2020-01-01T00:00:00Z&to=2026-08-28T00:00:00Z")
    assert response.status_code == 400


def test_an_unknown_timezone_is_rejected(client: TestClient) -> None:
    response = client.get(
        "/events?from=2026-08-28T00:00:00Z&to=2026-08-29T00:00:00Z&timezone=Mars/Olympus"
    )
    assert response.status_code == 400
