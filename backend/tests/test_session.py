"""Session JWT and the CSRF double-submit pair (§5.1, §12)."""

from __future__ import annotations

import datetime as dt
from typing import Any

import jwt
import pytest
from tests.conftest import UID

from sundial.oauth import session


def test_mint_then_verify_round_trips(aws: dict[str, Any]) -> None:
    claims = session.verify(session.mint(UID))
    assert claims["sub"] == UID
    assert claims["iss"] == "sundial"


def test_expired_token_is_rejected(aws: dict[str, Any]) -> None:
    past = dt.datetime.now(dt.UTC) - dt.timedelta(days=1)
    stale = jwt.encode(
        {"iss": "sundial", "sub": UID, "iat": past - dt.timedelta(days=31), "exp": past},
        session.signing_key(),
        algorithm="RS256",
    )
    with pytest.raises(session.SessionInvalidError):
        session.verify(stale)


def test_token_signed_by_another_key_is_rejected(aws: dict[str, Any]) -> None:
    from tests.conftest import _rsa_pem_pair

    other_private, _ = _rsa_pem_pair()
    now = dt.datetime.now(dt.UTC)
    forged = jwt.encode(
        {"iss": "sundial", "sub": UID, "iat": now, "exp": now + dt.timedelta(days=1)},
        other_private,
        algorithm="RS256",
    )
    with pytest.raises(session.SessionInvalidError):
        session.verify(forged)


def test_unsigned_token_is_rejected(aws: dict[str, Any]) -> None:
    """`alg: none` is the classic JWT hole; PyJWT must refuse it here."""
    now = dt.datetime.now(dt.UTC)
    unsigned = jwt.encode(
        {"iss": "sundial", "sub": UID, "iat": now, "exp": now + dt.timedelta(days=1)},
        key="",
        algorithm="none",
    )
    with pytest.raises(session.SessionInvalidError):
        session.verify(unsigned)


@pytest.mark.parametrize(
    ("cookie", "header", "expected"),
    [
        ("abc", "abc", True),
        ("abc", "abd", False),
        (None, "abc", False),
        ("abc", None, False),
        (None, None, False),
        ("", "", False),
    ],
)
def test_csrf_double_submit(cookie: str | None, header: str | None, expected: bool) -> None:
    assert session.csrf_ok(cookie, header) is expected


def test_cookie_is_not_secure_on_localhost_but_is_elsewhere(
    aws: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    from sundial.core import config

    assert session.cookie_kwargs(http_only=True)["secure"] is False

    monkeypatch.setenv("SUNDIAL_APP_BASE_URL", "https://sundial.example.com")
    config.settings.cache_clear()
    assert session.cookie_kwargs(http_only=True)["secure"] is True
