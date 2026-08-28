"""Refresh token at rest (§5.4) and connection state."""

from __future__ import annotations

from typing import Any

from tests.conftest import UID

from sundial.core import store
from sundial.oauth import tokens


def _save() -> None:
    tokens.save(
        UID,
        refresh_token="1//refresh-token-value",
        google_account_id=UID,
        email="someone@example.com",
        scopes=("openid", "https://www.googleapis.com/auth/calendar.events"),
    )


def test_refresh_token_round_trips_through_kms(aws: dict[str, Any]) -> None:
    _save()
    assert tokens.refresh_token(UID) == "1//refresh-token-value"


def test_refresh_token_is_never_stored_in_plaintext(aws: dict[str, Any]) -> None:
    _save()
    item = store.get(UID, store.sk_auth())
    assert item is not None
    assert "1//refresh-token-value" not in str(item)
    assert "refresh_token" not in item


def test_unconnected_user_reports_disconnected(aws: dict[str, Any]) -> None:
    connection = tokens.load(UID)
    assert connection.state is tokens.ConnectionState.DISCONNECTED
    assert tokens.refresh_token(UID) is None


def test_invalid_grant_halts_rather_than_retries(aws: dict[str, Any]) -> None:
    _save()
    tokens.mark_needs_reconnect(UID, "invalid_grant")

    connection = tokens.load(UID)
    assert connection.state is tokens.ConnectionState.NEEDS_RECONNECT
    # The token is still on disk, but refusing to hand it out is what stops the
    # sync loops from grinding against a dead grant.
    assert tokens.refresh_token(UID) is None


def test_connection_dict_never_leaks_the_token(aws: dict[str, Any]) -> None:
    _save()
    assert set(tokens.load(UID).as_dict()) == {"state", "email", "scopes", "connected_at"}


def test_access_tokens_are_memory_only(aws: dict[str, Any]) -> None:
    tokens.cache_access_token(UID, "ya29.access", expires_in=3600)
    assert tokens.cached_access_token(UID) == "ya29.access"
    assert store.get(UID, store.sk_auth()) is None


def test_nearly_expired_access_token_is_not_reused(aws: dict[str, Any]) -> None:
    """A 30s-remaining token would expire mid-request; the 60s skew rejects it."""
    tokens.cache_access_token(UID, "ya29.access", expires_in=30)
    assert tokens.cached_access_token(UID) is None
