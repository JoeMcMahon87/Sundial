"""Sundial's own session cookie (§5.1) and the CSRF pair (§12).

RS256, 30-day expiry, signing key in Secrets Manager. The cookie is
``HttpOnly; Secure; SameSite=Lax``; because CloudFront serves the SPA and the
API from one origin it is first-party and survives iOS Safari.
"""

from __future__ import annotations

import datetime as dt
import functools
import json
import secrets
from typing import Any

import boto3
import jwt

from sundial.core.config import settings

SESSION_COOKIE = "sundial_session"
CSRF_COOKIE = "sundial_csrf"
CSRF_HEADER = "X-CSRF-Token"
_ALGORITHM = "RS256"
_ISSUER = "sundial"


class SessionInvalidError(Exception):
    """The presented token is absent, malformed, expired, or wrongly signed."""


@functools.lru_cache(maxsize=1)
def _keypair() -> tuple[str, str]:
    """(private_pem, public_pem) from Secrets Manager, cached per environment."""
    client = boto3.client("secretsmanager")
    raw = client.get_secret_value(SecretId=settings().session_key_secret_arn)["SecretString"]
    material = json.loads(raw)
    return material["private_key"], material["public_key"]


def signing_key() -> str:
    """RS256 private key. Also signs the short-lived OAuth flow cookie, which
    lives inside the same trust boundary."""
    return _keypair()[0]


def verifying_key() -> str:
    return _keypair()[1]


def mint(uid: str) -> str:
    issued = dt.datetime.now(dt.UTC)
    claims = {
        "iss": _ISSUER,
        "sub": uid,
        "iat": issued,
        "exp": issued + dt.timedelta(days=settings().session_ttl_days),
    }
    return jwt.encode(claims, _keypair()[0], algorithm=_ALGORITHM)


def verify(token: str) -> dict[str, Any]:
    try:
        return jwt.decode(
            token,
            _keypair()[1],
            algorithms=[_ALGORITHM],
            issuer=_ISSUER,
            options={"require": ["exp", "iat", "sub", "iss"]},
        )
    except jwt.PyJWTError as exc:
        raise SessionInvalidError(str(exc)) from exc


def new_csrf_token() -> str:
    return secrets.token_urlsafe(32)


def csrf_ok(cookie_value: str | None, header_value: str | None) -> bool:
    """Double-submit check (§12). Compared in constant time."""
    if not cookie_value or not header_value:
        return False
    return secrets.compare_digest(cookie_value, header_value)


def cookie_kwargs(*, http_only: bool) -> dict[str, Any]:
    """Shared cookie attributes. ``secure`` is relaxed only on localhost,
    because Safari will not store a Secure cookie over plain http."""
    config = settings()
    return {
        "httponly": http_only,
        "secure": not config.is_local,
        "samesite": "lax",
        "path": "/",
        "domain": config.cookie_domain,
        "max_age": config.session_ttl_days * 24 * 3600,
    }
