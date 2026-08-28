"""Lambda authorizer for ``/api/*`` (§5.1 step 4).

Validates the Sundial session JWT and nothing else. The CSRF half of §12 is
enforced in the app rather than here — see ``sundial.api.csrf`` for why.

Payload format 2.0, simple response. The result is cached for 300s against the
``Cookie`` header, so the session cookie is the whole of the identity source.
"""

from __future__ import annotations

import logging
from http.cookies import SimpleCookie
from typing import Any

from sundial.core import logging as slog
from sundial.oauth import session

slog.configure()
log = logging.getLogger(__name__)

_DENY: dict[str, Any] = {"isAuthorized": False}


def _session_cookie(event: dict[str, Any]) -> str | None:
    for raw in event.get("cookies") or []:
        jar = SimpleCookie()
        jar.load(raw)
        if session.SESSION_COOKIE in jar:
            return jar[session.SESSION_COOKIE].value

    header = event.get("headers", {}).get("cookie") or event.get("headers", {}).get("Cookie")
    if header:
        jar = SimpleCookie()
        jar.load(header)
        if session.SESSION_COOKIE in jar:
            return jar[session.SESSION_COOKIE].value
    return None


def handler(event: dict[str, Any], _context: object) -> dict[str, Any]:
    slog.set_correlation_id(str(event.get("requestContext", {}).get("requestId", "-")))

    token = _session_cookie(event)
    if not token:
        return _DENY

    try:
        claims = session.verify(token)
    except session.SessionInvalidError as exc:
        log.info("session rejected", extra={"reason": str(exc)})
        return _DENY

    return {"isAuthorized": True, "context": {"uid": claims["sub"]}}
