"""Structured JSON logging with a correlation id (§12).

Never log email bodies, tokens, or full event titles at INFO. ``redact_title``
exists so that call sites do not have to remember the rule.
"""

from __future__ import annotations

import json
import logging
import os
from contextvars import ContextVar
from typing import Any

_correlation_id: ContextVar[str] = ContextVar("correlation_id", default="-")

_STANDARD = frozenset(logging.LogRecord("", 0, "", 0, "", None, None).__dict__) | {
    "asctime",
    "message",
    "taskName",
}

# The names `logging.Logger.makeRecord` refuses to let `extra=` overwrite.
_RESERVED = _STANDARD


def extra(**fields: Any) -> dict[str, Any]:
    """Build a safe ``extra=`` mapping for ``logging``.

    ``LogRecord`` already owns attributes named ``created``, ``module``,
    ``args``, ``name``, ``process`` and a dozen more, and passing one through
    ``extra=`` raises ``KeyError`` inside ``makeRecord``. That turns a log line
    into a crash at exactly the moment something interesting was happening —
    a sync that created events, for instance. Colliding names are suffixed
    rather than dropped, because losing the field silently is its own bug.
    """
    return {(f"{k}_" if k in _RESERVED else k): v for k, v in fields.items()}


def set_correlation_id(value: str) -> None:
    _correlation_id.set(value)


def get_correlation_id() -> str:
    return _correlation_id.get()


def redact_title(title: str | None) -> str:
    """Event and task titles are not safe to log in full (§12)."""
    if not title:
        return ""
    head = title[:12]
    return head if len(title) <= 12 else f"{head}…({len(title)})"


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            "correlation_id": _correlation_id.get(),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        for key, value in record.__dict__.items():
            if key not in _STANDARD and not key.startswith("_"):
                payload[key] = value
        return json.dumps(payload, default=str, separators=(",", ":"))


def configure() -> None:
    """Idempotent; Lambda re-imports modules across warm invocations."""
    root = logging.getLogger()
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    root.handlers = [handler]
    root.setLevel(os.environ.get("SUNDIAL_LOG_LEVEL", "INFO"))
