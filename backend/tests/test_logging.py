"""Structured logging and the §12 redaction rules."""

from __future__ import annotations

import json
import logging

from sundial.core import logging as slog


def test_records_are_json_with_a_correlation_id() -> None:
    slog.set_correlation_id("corr-123")
    record = logging.LogRecord("sundial", logging.INFO, __file__, 1, "synced", None, None)
    payload = json.loads(slog.JsonFormatter().format(record))

    assert payload["msg"] == "synced"
    assert payload["correlation_id"] == "corr-123"
    assert payload["level"] == "INFO"


def test_extra_fields_survive() -> None:
    record = logging.LogRecord("sundial", logging.INFO, __file__, 1, "synced", None, None)
    record.__dict__["page"] = 3
    assert json.loads(slog.JsonFormatter().format(record))["page"] == 3


def test_titles_are_truncated_not_logged_whole() -> None:
    assert slog.redact_title("Standup") == "Standup"
    long = slog.redact_title("Quarterly planning with the whole team")
    assert long.startswith("Quarterly pl")
    assert "whole team" not in long
    assert slog.redact_title(None) == ""
