"""Bootstrap endpoint (§11: ``GET /me``)."""

from __future__ import annotations

from fastapi import APIRouter, Request

from sundial.api.deps import current_uid
from sundial.core.config import settings
from sundial.oauth import tokens

router = APIRouter(tags=["me"])


@router.get("/me")
def me(request: Request) -> dict[str, object]:
    """§11 specifies this returns policy, calendars, and connection state.

    Policy arrives with M2 and the calendar list with M1; until those exist the
    keys are absent rather than stubbed, so the SPA can feature-detect instead
    of unpicking a placeholder shape later.
    """
    connection = tokens.load(current_uid(request))
    return {
        "env": settings().env,
        "connection": connection.as_dict(),
    }
