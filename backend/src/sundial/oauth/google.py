"""The Google authorization-code flow with PKCE (§5.1).

``access_type=offline`` plus ``prompt=consent`` is what makes Google hand back
a refresh token at all. The scope set is calendar-only until M5 (§16).
"""

from __future__ import annotations

import base64
import functools
import hashlib
import json
import secrets
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

import boto3
import httpx

from sundial.core.config import settings
from sundial.oauth import tokens

AUTH_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
USERINFO_ENDPOINT = "https://openidconnect.googleapis.com/v1/userinfo"
REVOKE_ENDPOINT = "https://oauth2.googleapis.com/revoke"


class InvalidGrantError(Exception):
    """Google rejected the refresh token; the connection is dead (§5.4)."""


@dataclass(frozen=True, slots=True)
class PkcePair:
    verifier: str
    challenge: str


def new_pkce() -> PkcePair:
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return PkcePair(verifier=verifier, challenge=challenge)


@functools.lru_cache(maxsize=1)
def _client_secret() -> str:
    client = boto3.client("secretsmanager")
    raw = client.get_secret_value(SecretId=settings().google_client_secret_arn)["SecretString"]
    try:
        return str(json.loads(raw)["client_secret"])
    except (json.JSONDecodeError, KeyError, TypeError):
        return str(raw)


def authorization_url(*, state: str, challenge: str) -> str:
    config = settings()
    params = {
        "client_id": config.google_client_id,
        "redirect_uri": config.google_redirect_uri,
        "response_type": "code",
        "scope": " ".join(config.scopes),
        "access_type": "offline",
        "prompt": "consent",
        "include_granted_scopes": "true",
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    return f"{AUTH_ENDPOINT}?{urlencode(params)}"


def _post_token(payload: dict[str, str]) -> dict[str, Any]:
    response = httpx.post(TOKEN_ENDPOINT, data=payload, timeout=10.0)
    if response.status_code == 400:
        body = response.json()
        if body.get("error") == "invalid_grant":
            raise InvalidGrantError(body.get("error_description", "invalid_grant"))
    response.raise_for_status()
    return dict(response.json())


def exchange_code(*, code: str, verifier: str) -> dict[str, Any]:
    config = settings()
    return _post_token(
        {
            "code": code,
            "client_id": config.google_client_id,
            "client_secret": _client_secret(),
            "redirect_uri": config.google_redirect_uri,
            "grant_type": "authorization_code",
            "code_verifier": verifier,
        }
    )


def userinfo(access_token: str) -> dict[str, Any]:
    response = httpx.get(
        USERINFO_ENDPOINT,
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=10.0,
    )
    response.raise_for_status()
    return dict(response.json())


def access_token(uid: str) -> str:
    """A live access token, refreshing only when the cached one is stale.

    On ``invalid_grant`` the connection is marked dead and the caller is
    expected to stop, not retry (§5.4).
    """
    cached = tokens.cached_access_token(uid)
    if cached:
        return cached

    stored = tokens.refresh_token(uid)
    if stored is None:
        raise InvalidGrantError("no usable refresh token stored")

    config = settings()
    try:
        payload = _post_token(
            {
                "refresh_token": stored,
                "client_id": config.google_client_id,
                "client_secret": _client_secret(),
                "grant_type": "refresh_token",
            }
        )
    except InvalidGrantError as exc:
        tokens.mark_needs_reconnect(uid, str(exc))
        raise

    token = str(payload["access_token"])
    tokens.cache_access_token(uid, token, int(payload.get("expires_in", 3600)))
    return token
