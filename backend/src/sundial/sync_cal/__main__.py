"""``python -m sundial.sync_cal`` — run a sync against the dev environment."""

from __future__ import annotations

from sundial.core.config import settings
from sundial.sync_cal.handler import run

for result in run(settings().allowed_google_account_id):
    print(  # noqa: T201 — this is a CLI
        f"{result.calendar_id}: +{result.created} ~{result.updated} -{result.deleted}"
        f"{' (full resync)' if result.full_resync else ''}"
    )
