"""The Lambda authorizer (§5.1 step 4)."""

from __future__ import annotations

from typing import Any

from tests.conftest import UID

from sundial.api import authorizer
from sundial.oauth import session


def _event(cookies: list[str]) -> dict[str, Any]:
    return {"cookies": cookies, "headers": {}, "requestContext": {"requestId": "req-1"}}


def test_valid_session_is_authorized(aws: dict[str, Any]) -> None:
    result = authorizer.handler(_event([f"sundial_session={session.mint(UID)}"]), None)
    assert result == {"isAuthorized": True, "context": {"uid": UID}}


def test_missing_cookie_is_denied(aws: dict[str, Any]) -> None:
    assert authorizer.handler(_event([]), None) == {"isAuthorized": False}


def test_garbage_token_is_denied(aws: dict[str, Any]) -> None:
    assert authorizer.handler(_event(["sundial_session=not-a-jwt"]), None) == {
        "isAuthorized": False
    }


def test_cookie_arriving_in_the_header_is_also_read(aws: dict[str, Any]) -> None:
    """API Gateway splits cookies into `cookies`, but a direct invoke or a
    proxied request may put them in the header instead."""
    event: dict[str, Any] = {
        "cookies": [],
        "headers": {"cookie": f"other=1; sundial_session={session.mint(UID)}"},
        "requestContext": {"requestId": "req-1"},
    }
    assert authorizer.handler(event, None)["isAuthorized"] is True


def test_the_csrf_cookie_alone_does_not_authorize(aws: dict[str, Any]) -> None:
    assert authorizer.handler(_event(["sundial_csrf=abc"]), None) == {"isAuthorized": False}
