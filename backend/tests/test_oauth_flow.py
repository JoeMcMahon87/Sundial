"""The authorization-code round trip (§5.1), driven end to end against respx."""

from __future__ import annotations

import base64
import hashlib
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
import respx
from fastapi.testclient import TestClient
from tests.conftest import UID

from sundial.oauth import google, session, tokens


@pytest.fixture
def client(aws: dict[str, Any]) -> TestClient:
    from sundial.api.app import create_app

    return TestClient(create_app(), base_url="http://localhost:5173/api")


def _start_login(client: TestClient) -> tuple[str, str]:
    """Returns (state, code_verifier) by replaying the PKCE challenge."""
    response = client.get("/auth/login", follow_redirects=False)
    assert response.status_code == 302

    params = parse_qs(urlparse(response.headers["location"]).query)
    assert params["access_type"] == ["offline"]
    assert params["prompt"] == ["consent"]
    assert params["code_challenge_method"] == ["S256"]

    sealed = response.cookies["sundial_oauth_flow"]
    state, verifier = None, None
    import jwt

    claims = jwt.decode(sealed, session.verifying_key(), algorithms=["RS256"])
    state, verifier = claims["state"], claims["verifier"]

    # The challenge Google was given must be S256(verifier), or the exchange
    # is rejected at the far end.
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    assert params["code_challenge"] == [base64.urlsafe_b64encode(digest).rstrip(b"=").decode()]
    return state, verifier


def test_login_requests_calendar_scopes_only(client: TestClient) -> None:
    """Gmail's restricted scopes are deliberately absent until M5 (§16)."""
    response = client.get("/auth/login", follow_redirects=False)
    scopes = parse_qs(urlparse(response.headers["location"]).query)["scope"][0].split()

    assert "https://www.googleapis.com/auth/calendar.events" in scopes
    assert not [s for s in scopes if "gmail" in s]


@respx.mock
def test_callback_stores_the_connection_and_sets_both_cookies(client: TestClient) -> None:
    state, _ = _start_login(client)
    respx.post(google.TOKEN_ENDPOINT).mock(
        return_value=httpx.Response(
            200,
            json={
                "access_token": "ya29.access",
                "refresh_token": "1//refresh",
                "expires_in": 3599,
                "scope": "openid https://www.googleapis.com/auth/calendar.events",
            },
        )
    )
    respx.get(google.USERINFO_ENDPOINT).mock(
        return_value=httpx.Response(200, json={"sub": UID, "email": "someone@example.com"})
    )

    response = client.get(
        f"/auth/callback?code=auth-code&state={state}", follow_redirects=False
    )

    assert response.status_code == 302
    assert response.headers["location"] == "http://localhost:5173"
    assert tokens.load(UID).state is tokens.ConnectionState.CONNECTED

    jar = response.headers.get_list("set-cookie")
    assert any("sundial_session=" in c and "HttpOnly" in c for c in jar)
    # The CSRF cookie must be readable by the SPA to be echoed back (§12).
    assert any("sundial_csrf=" in c and "HttpOnly" not in c for c in jar)


@respx.mock
def test_a_different_google_account_is_rejected_outright(client: TestClient) -> None:
    """Single-entry allowlist; no user record is created for anyone else (§5.1)."""
    state, _ = _start_login(client)
    respx.post(google.TOKEN_ENDPOINT).mock(
        return_value=httpx.Response(
            200,
            json={"access_token": "ya29.access", "refresh_token": "1//r", "expires_in": 3599},
        )
    )
    respx.get(google.USERINFO_ENDPOINT).mock(
        return_value=httpx.Response(
            200, json={"sub": "999", "email": "someone-else@example.com"}
        )
    )

    response = client.get(f"/auth/callback?code=c&state={state}", follow_redirects=False)

    assert response.status_code == 403
    assert response.headers["content-type"].startswith("application/problem+json")
    assert tokens.load("999").state is tokens.ConnectionState.DISCONNECTED


@respx.mock
def test_callback_without_a_refresh_token_fails_loudly(client: TestClient) -> None:
    """Silently accepting this would mean background sync dies at the first
    access-token expiry, hours later and far from the cause."""
    state, _ = _start_login(client)
    respx.post(google.TOKEN_ENDPOINT).mock(
        return_value=httpx.Response(
            200, json={"access_token": "ya29.access", "expires_in": 3599}
        )
    )
    respx.get(google.USERINFO_ENDPOINT).mock(
        return_value=httpx.Response(200, json={"sub": UID, "email": "someone@example.com"})
    )

    response = client.get(f"/auth/callback?code=c&state={state}", follow_redirects=False)
    assert response.status_code == 502


def test_state_mismatch_is_rejected(client: TestClient) -> None:
    _start_login(client)
    response = client.get("/auth/callback?code=c&state=not-the-state", follow_redirects=False)
    assert response.status_code == 400


def test_callback_without_a_flow_cookie_is_rejected(client: TestClient) -> None:
    response = client.get("/auth/callback?code=c&state=s", follow_redirects=False)
    assert response.status_code == 400


@respx.mock
def test_invalid_grant_on_refresh_marks_the_connection_dead(aws: dict[str, Any]) -> None:
    tokens.save(
        UID,
        refresh_token="1//stale",
        google_account_id=UID,
        email="someone@example.com",
        scopes=("openid",),
    )
    respx.post(google.TOKEN_ENDPOINT).mock(
        return_value=httpx.Response(400, json={"error": "invalid_grant"})
    )

    with pytest.raises(google.InvalidGrantError):
        google.access_token(UID)

    assert tokens.load(UID).state is tokens.ConnectionState.NEEDS_RECONNECT
