"""CSRF double-submit enforcement (§12).

The comparison lives here rather than in the Lambda authorizer, which §12 sets
out in full: an HTTP API authorizer caches its result against
``identitySource`` for 300s, so either the CSRF header is part of that key — in
which case every GET, carrying no such header, is rejected for a missing
identity source — or it is not, in which case one failed POST caches a denial
that locks out subsequent reads. The check has to be per-request, which the
authorizer deliberately is not. The authorizer keeps the JWT half.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from starlette.requests import Request
from starlette.responses import Response

from sundial.core.errors import ProblemError
from sundial.oauth import session

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})

EXEMPT_PATHS = frozenset(
    {
        "/api/auth/login",
        "/api/auth/callback",
        "/api/google/webhook",  # unauthenticated by necessity (§12)
    }
)


async def middleware(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    if request.method not in SAFE_METHODS and request.url.path not in EXEMPT_PATHS:
        cookie = request.cookies.get(session.CSRF_COOKIE)
        header = request.headers.get(session.CSRF_HEADER)
        if not session.csrf_ok(cookie, header):
            problem = ProblemError(
                403,
                "CSRF check failed",
                "The CSRF cookie and header are absent or disagree.",
                "csrf-failed",
            )
            return problem.to_response(request.url.path)
    return await call_next(request)
