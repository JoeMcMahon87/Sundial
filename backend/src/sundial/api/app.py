"""The ``api`` Lambda: one handler with an internal router (§4.1)."""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable

from fastapi import FastAPI
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from sundial.api import csrf, routes_auth, routes_me
from sundial.core import errors
from sundial.core import logging as slog


def create_app() -> FastAPI:
    slog.configure()
    app = FastAPI(
        title="Sundial",
        root_path="/api",
        docs_url=None,
        redoc_url=None,
        openapi_url="/openapi.json",
    )

    @app.middleware("http")
    async def correlate(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        correlation_id = request.headers.get("X-Amzn-Trace-Id") or str(uuid.uuid4())
        slog.set_correlation_id(correlation_id)
        response = await call_next(request)
        response.headers["X-Correlation-Id"] = correlation_id
        return response

    app.add_middleware(BaseHTTPMiddleware, dispatch=csrf.middleware)

    errors.install(app)
    app.include_router(routes_auth.router)
    app.include_router(routes_me.router)

    @app.get("/health", include_in_schema=False)
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
