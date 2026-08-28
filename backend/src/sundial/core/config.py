"""Runtime configuration.

Every value arrives as an environment variable set by CDK, which itself reads
from SSM Parameter Store under ``/sundial/<env>/`` (§12: no credentials in the
repo). Nothing here has a hostname baked in — the domain is deferred (§16).
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

Env = Literal["dev", "prod"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SUNDIAL_", frozen=True)

    env: Env = "dev"

    # Storage
    table_name: str = "sundial-dev"
    kms_key_id: str = ""

    # Sundial's own session (§5.1)
    session_key_secret_arn: str = ""
    session_ttl_days: int = 30
    cookie_domain: str | None = None
    """None means host-only, which is what localhost wants."""

    # Google OAuth (§5.1, §5.2)
    google_client_id: str = ""
    google_client_secret_arn: str = ""
    google_redirect_uri: str = "http://localhost:5173/api/auth/callback"
    allowed_google_account_id: str = ""
    """Single-entry allowlist. Anyone else is rejected outright (§5.1)."""

    # Where to send the browser once the OAuth dance finishes.
    app_base_url: str = "http://localhost:5173"

    scopes: tuple[str, ...] = Field(
        default=(
            "openid",
            "email",
            "profile",
            "https://www.googleapis.com/auth/calendar.events",
            "https://www.googleapis.com/auth/calendar.calendarlist.readonly",
        )
    )
    """Calendar scopes only until M5. Gmail's restricted scopes are added when
    J3 lands, deliberately — see §16 decision 1."""

    @property
    def is_local(self) -> bool:
        return self.app_base_url.startswith("http://localhost")


@lru_cache(maxsize=1)
def settings() -> Settings:
    return Settings()
