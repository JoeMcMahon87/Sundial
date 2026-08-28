"""The Google refresh token at rest (§5.4).

The refresh token is encrypted under the customer-managed KMS key and lives in
the ``AUTH#google`` item — deliberately *not* in Secrets Manager (§12's secrets
inventory has four entries and this is not one of them). Access tokens are
cached in the execution environment's memory and never persisted.
"""

from __future__ import annotations

import base64
import datetime as dt
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

import boto3

from sundial.core import store
from sundial.core.config import settings

_ENCRYPTION_CONTEXT = {"purpose": "google-refresh-token"}


class ConnectionState(StrEnum):
    CONNECTED = "connected"
    NEEDS_RECONNECT = "needs_reconnect"
    DISCONNECTED = "disconnected"


@dataclass(frozen=True, slots=True)
class Connection:
    state: ConnectionState
    google_account_id: str | None = None
    email: str | None = None
    scopes: tuple[str, ...] = ()
    connected_at: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "state": str(self.state),
            "email": self.email,
            "scopes": list(self.scopes),
            "connected_at": self.connected_at,
        }


def _kms() -> Any:
    return boto3.client("kms")


def _encrypt(plaintext: str) -> bytes:
    response = _kms().encrypt(
        KeyId=settings().kms_key_id,
        Plaintext=plaintext.encode(),
        EncryptionContext=_ENCRYPTION_CONTEXT,
    )
    return bytes(response["CiphertextBlob"])


def _decrypt(blob: bytes) -> str:
    response = _kms().decrypt(
        CiphertextBlob=blob,
        EncryptionContext=_ENCRYPTION_CONTEXT,
    )
    return str(response["Plaintext"].decode())


def save(
    uid: str,
    *,
    refresh_token: str,
    google_account_id: str,
    email: str,
    scopes: tuple[str, ...],
) -> None:
    store.put(
        uid,
        store.sk_auth(),
        {
            "state": str(ConnectionState.CONNECTED),
            "refresh_token_ciphertext": base64.b64encode(_encrypt(refresh_token)).decode(),
            "google_account_id": google_account_id,
            "email": email,
            "scopes": list(scopes),
            "connected_at": store.iso(store.now()),
        },
    )


def load(uid: str) -> Connection:
    item = store.get(uid, store.sk_auth())
    if item is None:
        return Connection(state=ConnectionState.DISCONNECTED)
    return Connection(
        state=ConnectionState(item["state"]),
        google_account_id=item.get("google_account_id"),
        email=item.get("email"),
        scopes=tuple(item.get("scopes", ())),
        connected_at=item.get("connected_at"),
    )


def refresh_token(uid: str) -> str | None:
    """Plaintext refresh token, or None when there is nothing usable stored."""
    item = store.get(uid, store.sk_auth())
    if item is None or item.get("state") != ConnectionState.CONNECTED:
        return None
    return _decrypt(base64.b64decode(item["refresh_token_ciphertext"]))


def mark_needs_reconnect(uid: str, reason: str) -> None:
    """Called on ``invalid_grant``. Sundial halts sync rather than retrying
    blindly (§5.4); the UI and a push notification carry the prompt."""
    store.table().update_item(
        Key={"PK": store.pk(uid), "SK": store.sk_auth()},
        UpdateExpression="SET #s = :s, disconnect_reason = :r, disconnected_at = :t",
        ExpressionAttributeNames={"#s": "state"},
        ExpressionAttributeValues={
            ":s": str(ConnectionState.NEEDS_RECONNECT),
            ":r": reason,
            ":t": store.iso(store.now()),
        },
    )


# Access tokens: memory only, per execution environment (§5.4).
_access_cache: dict[str, tuple[str, dt.datetime]] = {}


def cached_access_token(uid: str) -> str | None:
    entry = _access_cache.get(uid)
    if entry is None:
        return None
    token, expires_at = entry
    if expires_at <= store.now() + dt.timedelta(seconds=60):
        del _access_cache[uid]
        return None
    return token


def cache_access_token(uid: str, token: str, expires_in: int) -> None:
    _access_cache[uid] = (token, store.now() + dt.timedelta(seconds=expires_in))
