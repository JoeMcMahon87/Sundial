"""A thin Google Calendar v3 client.

Only the calls M1 steps 1-3 need. Everything is synchronous: a Lambda has one
request in flight at a time and an async client would buy nothing.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Iterator
from typing import Any

import httpx

from sundial.core import logging as slog
from sundial.oauth import google

BASE = "https://www.googleapis.com/calendar/v3"

log = logging.getLogger(__name__)

_MAX_ATTEMPTS = 5
_BACKOFF_BASE_SECONDS = 0.5


class SyncTokenExpiredError(Exception):
    """``410 Gone``. The token is stale; fall back to a bounded full list (§6.2)."""


class RateLimitedError(Exception):
    """Retries exhausted against ``403 rateLimitExceeded``."""


def _is_rate_limit(response: httpx.Response) -> bool:
    if response.status_code == 429:
        return True
    if response.status_code != 403:
        return False
    try:
        reasons = {
            error.get("reason") for error in response.json().get("error", {}).get("errors", [])
        }
    except ValueError:
        return False
    return bool(reasons & {"rateLimitExceeded", "userRateLimitExceeded"})


class CalendarClient:
    def __init__(self, uid: str, *, sleep: Any = time.sleep) -> None:
        self._uid = uid
        self._sleep = sleep

    def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        for attempt in range(_MAX_ATTEMPTS):
            response = httpx.request(
                method,
                f"{BASE}{path}",
                headers={"Authorization": f"Bearer {google.access_token(self._uid)}"},
                timeout=20.0,
                **kwargs,
            )
            if response.status_code == 410:
                raise SyncTokenExpiredError(path)
            if _is_rate_limit(response) or response.status_code >= 500:
                delay = _BACKOFF_BASE_SECONDS * (2**attempt)
                log.warning(
                    "google backoff",
                    extra=slog.extra(status=response.status_code, attempt=attempt, delay=delay),
                )
                self._sleep(delay)
                continue
            response.raise_for_status()
            return dict(response.json())

        raise RateLimitedError(path)

    def calendar_list(self) -> list[dict[str, Any]]:
        entries: list[dict[str, Any]] = []
        page_token: str | None = None
        while True:
            params: dict[str, Any] = {"maxResults": 250}
            if page_token:
                params["pageToken"] = page_token
            payload = self._request("GET", "/users/me/calendarList", params=params)
            entries.extend(payload.get("items", []))
            page_token = payload.get("nextPageToken")
            if not page_token:
                return entries

    def create_calendar(self, summary: str, time_zone: str) -> dict[str, Any]:
        return self._request(
            "POST", "/calendars", json={"summary": summary, "timeZone": time_zone}
        )

    def events(
        self,
        calendar_id: str,
        *,
        sync_token: str | None = None,
        time_min: str | None = None,
        time_max: str | None = None,
    ) -> Iterator[tuple[list[dict[str, Any]], str | None]]:
        """Yield ``(page_items, next_sync_token)``; the token is set on the last page.

        ``singleEvents=true`` is what keeps an RRULE engine out of v1 (§6.6),
        and ``showDeleted=true`` is what makes cancellations visible instead of
        merely absent.
        """
        from urllib.parse import quote

        path = f"/calendars/{quote(calendar_id, safe='')}/events"
        page_token: str | None = None

        while True:
            params: dict[str, Any] = {
                "maxResults": 250,
                "showDeleted": "true",
                "singleEvents": "true",
            }
            if sync_token:
                # Google rejects a request carrying both a syncToken and a
                # time window, so these are mutually exclusive by construction.
                params["syncToken"] = sync_token
            else:
                params["timeMin"] = time_min
                params["timeMax"] = time_max
            if page_token:
                params["pageToken"] = page_token

            payload = self._request("GET", path, params=params)
            page_token = payload.get("nextPageToken")
            yield payload.get("items", []), payload.get("nextSyncToken")
            if not page_token:
                return
