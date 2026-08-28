"""RFC 9457 problem+json error responses (§11)."""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

CONTENT_TYPE = "application/problem+json"
_BASE_URI = "https://sundial.invalid/problems"


class ProblemError(Exception):
    """An error with an RFC 9457 representation."""

    def __init__(
        self,
        status: int,
        title: str,
        detail: str | None = None,
        problem_type: str = "about:blank",
        **extra: Any,
    ) -> None:
        super().__init__(detail or title)
        self.status = status
        self.title = title
        self.detail = detail
        self.problem_type = (
            problem_type if problem_type == "about:blank" else f"{_BASE_URI}/{problem_type}"
        )
        self.extra = extra

    def to_response(self, instance: str) -> JSONResponse:
        body: dict[str, Any] = {
            "type": self.problem_type,
            "title": self.title,
            "status": self.status,
            "instance": instance,
        }
        if self.detail:
            body["detail"] = self.detail
        body.update(self.extra)
        return JSONResponse(body, status_code=self.status, media_type=CONTENT_TYPE)


class NotConnectedError(ProblemError):
    def __init__(self, state: str) -> None:
        super().__init__(
            status=409,
            title="Google account is not connected",
            detail=f"Connection state is {state!r}; reconnect before syncing.",
            problem_type="google-not-connected",
            connection_state=state,
        )


class ForbiddenAccountError(ProblemError):
    """Someone other than the single allowlisted account tried to sign in (§5.1)."""

    def __init__(self) -> None:
        super().__init__(
            status=403,
            title="This Sundial instance is single-user",
            detail="The Google account that consented is not the allowlisted account.",
            problem_type="account-not-allowed",
        )


def install(app: FastAPI) -> None:
    @app.exception_handler(ProblemError)
    async def _problem(request: Request, exc: ProblemError) -> JSONResponse:
        return exc.to_response(request.url.path)

    @app.exception_handler(StarletteHTTPException)
    async def _http(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        problem = ProblemError(status=exc.status_code, title=str(exc.detail))
        return problem.to_response(request.url.path)

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError) -> JSONResponse:
        problem = ProblemError(
            status=422,
            title="Request body failed validation",
            problem_type="validation-error",
            errors=[
                {"loc": list(e["loc"]), "msg": e["msg"], "type": e["type"]}
                for e in exc.errors()
            ],
        )
        return problem.to_response(request.url.path)
