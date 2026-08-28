"""App-level behaviour: CSRF, problem+json, and the bootstrap endpoint."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient
from tests.conftest import UID

from sundial.oauth import session, tokens


@pytest.fixture
def client(aws: dict[str, Any]) -> TestClient:
    from sundial.api.app import create_app

    return TestClient(create_app(), base_url="http://localhost:5173/api")


@pytest.fixture
def signed_in(client: TestClient) -> TestClient:
    client.cookies.set("sundial_session", session.mint(UID))
    client.cookies.set("sundial_csrf", "csrf-token-value")
    return client


def test_health_needs_no_session(client: TestClient) -> None:
    assert client.get("/health").json() == {"status": "ok"}


def test_me_without_a_session_is_401_problem_json(client: TestClient) -> None:
    response = client.get("/me")
    assert response.status_code == 401
    assert response.headers["content-type"].startswith("application/problem+json")
    body = response.json()
    assert body["status"] == 401
    assert body["instance"] == "/api/me"


def test_me_reports_the_connection_state(signed_in: TestClient) -> None:
    body = signed_in.get("/me").json()
    assert body["env"] == "dev"
    assert body["connection"]["state"] == "disconnected"

    tokens.save(
        UID,
        refresh_token="1//r",
        google_account_id=UID,
        email="someone@example.com",
        scopes=("openid",),
    )
    assert signed_in.get("/me").json()["connection"]["state"] == "connected"


def test_google_status_endpoint(signed_in: TestClient) -> None:
    assert signed_in.get("/auth/google/status").json()["state"] == "disconnected"


def test_mutating_request_without_the_csrf_header_is_rejected(signed_in: TestClient) -> None:
    response = signed_in.post("/auth/logout")
    assert response.status_code == 403
    assert response.json()["title"] == "CSRF check failed"


def test_mutating_request_with_a_mismatched_csrf_header_is_rejected(
    signed_in: TestClient,
) -> None:
    response = signed_in.post("/auth/logout", headers={"X-CSRF-Token": "something-else"})
    assert response.status_code == 403


def test_mutating_request_with_a_matching_csrf_header_succeeds(signed_in: TestClient) -> None:
    response = signed_in.post("/auth/logout", headers={"X-CSRF-Token": "csrf-token-value"})
    assert response.status_code == 204


def test_reads_do_not_require_a_csrf_header(signed_in: TestClient) -> None:
    assert signed_in.get("/me").status_code == 200


def test_login_is_csrf_exempt(client: TestClient) -> None:
    """The login redirect is the one entry point that predates having a token."""
    assert client.get("/auth/login", follow_redirects=False).status_code == 302


def test_every_response_carries_a_correlation_id(client: TestClient) -> None:
    assert client.get("/health").headers["X-Correlation-Id"]
