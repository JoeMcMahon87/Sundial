"""Authentication endpoints (§11: ``/auth/*``)."""

from __future__ import annotations

import datetime as dt
import logging
import secrets

import jwt
from fastapi import APIRouter, Request, Response
from fastapi.responses import RedirectResponse

from sundial.api.deps import current_uid
from sundial.core.config import settings
from sundial.core.errors import ForbiddenAccountError, ProblemError
from sundial.oauth import google, session, tokens

log = logging.getLogger(__name__)
router = APIRouter(prefix="/auth", tags=["auth"])

_FLOW_COOKIE = "sundial_oauth_flow"
_FLOW_TTL = dt.timedelta(minutes=10)


def _seal_flow(state: str, verifier: str) -> str:
    """The PKCE verifier has to survive the round trip to Google. A signed,
    short-lived, HttpOnly cookie keeps it out of the database entirely."""
    now = dt.datetime.now(dt.UTC)
    return jwt.encode(
        {"state": state, "verifier": verifier, "iat": now, "exp": now + _FLOW_TTL},
        session.signing_key(),
        algorithm="RS256",
    )


def _open_flow(sealed: str) -> tuple[str, str]:
    claims = jwt.decode(sealed, session.verifying_key(), algorithms=["RS256"])
    return str(claims["state"]), str(claims["verifier"])


@router.get("/login")
def login() -> RedirectResponse:
    state = secrets.token_urlsafe(32)
    pkce = google.new_pkce()
    response = RedirectResponse(
        google.authorization_url(state=state, challenge=pkce.challenge),
        status_code=302,
    )
    response.set_cookie(
        _FLOW_COOKIE,
        _seal_flow(state, pkce.verifier),
        httponly=True,
        secure=not settings().is_local,
        samesite="lax",
        path="/api/auth",
        max_age=int(_FLOW_TTL.total_seconds()),
    )
    return response


@router.get("/callback")
def callback(request: Request, code: str | None = None, state: str | None = None) -> Response:
    config = settings()
    sealed = request.cookies.get(_FLOW_COOKIE)
    if not code or not state or not sealed:
        raise ProblemError(400, "Incomplete OAuth callback", problem_type="oauth-callback")

    try:
        expected_state, verifier = _open_flow(sealed)
    except jwt.PyJWTError as exc:
        raise ProblemError(400, "OAuth flow expired", str(exc), "oauth-callback") from exc

    if not secrets.compare_digest(expected_state, state):
        raise ProblemError(400, "OAuth state mismatch", problem_type="oauth-callback")

    granted = google.exchange_code(code=code, verifier=verifier)
    profile = google.userinfo(granted["access_token"])
    account_id = str(profile["sub"])

    # Single-entry allowlist. Anyone else is rejected outright — no user record
    # is created (§5.1).
    if not secrets.compare_digest(account_id, config.allowed_google_account_id):
        log.warning("rejected sign-in for non-allowlisted account")
        raise ForbiddenAccountError

    refresh = granted.get("refresh_token")
    if not refresh:
        # Only happens if prompt=consent was dropped or consent was reused.
        raise ProblemError(
            502,
            "Google returned no refresh token",
            "Re-consent with prompt=consent; background sync needs offline access.",
            "oauth-callback",
        )

    tokens.save(
        account_id,
        refresh_token=str(refresh),
        google_account_id=account_id,
        email=str(profile.get("email", "")),
        scopes=tuple(str(granted.get("scope", "")).split()),
    )
    tokens.cache_access_token(
        account_id, str(granted["access_token"]), int(granted.get("expires_in", 3600))
    )

    response = RedirectResponse(config.app_base_url, status_code=302)
    response.set_cookie(
        session.SESSION_COOKIE,
        session.mint(account_id),
        **session.cookie_kwargs(http_only=True),
    )
    # Readable by the SPA on purpose: it echoes this back in X-CSRF-Token (§12).
    response.set_cookie(
        session.CSRF_COOKIE,
        session.new_csrf_token(),
        **session.cookie_kwargs(http_only=False),
    )
    response.delete_cookie(_FLOW_COOKIE, path="/api/auth")
    log.info("google connected", extra={"email_domain": str(profile.get("hd", "-"))})
    return response


@router.post("/logout")
def logout() -> Response:
    response = Response(status_code=204)
    for name in (session.SESSION_COOKIE, session.CSRF_COOKIE):
        response.delete_cookie(name, path="/", domain=settings().cookie_domain)
    return response


@router.get("/google/status")
def google_status(request: Request) -> dict[str, object]:
    """``connected`` | ``needs_reconnect`` | ``disconnected`` (§11)."""
    return tokens.load(current_uid(request)).as_dict()
