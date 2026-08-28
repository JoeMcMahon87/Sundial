"""Request-scoped dependencies."""

from __future__ import annotations

from fastapi import Request

from sundial.core.errors import ProblemError
from sundial.oauth import session


def current_uid(request: Request) -> str:
    """The signed-in user's id.

    In deployed environments the Lambda authorizer has already validated the
    session JWT and passes the subject through in the request context; locally
    there is no authorizer, so the cookie is verified here. Both paths end at
    the same claim.
    """
    claimed = (
        request.scope.get("aws.event", {})
        .get("requestContext", {})
        .get("authorizer", {})
        .get("lambda", {})
        .get("uid")
    )
    if claimed:
        return str(claimed)

    token = request.cookies.get(session.SESSION_COOKIE)
    if not token:
        raise ProblemError(401, "Not signed in", problem_type="no-session")
    try:
        return str(session.verify(token)["sub"])
    except session.SessionInvalidError as exc:
        raise ProblemError(401, "Session is not valid", str(exc), "no-session") from exc
