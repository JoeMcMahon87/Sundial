"""Lambda entry point for the ``api`` function."""

from __future__ import annotations

from mangum import Mangum

from sundial.api.app import app

handler = Mangum(app, lifespan="off", api_gateway_base_path="/api")
