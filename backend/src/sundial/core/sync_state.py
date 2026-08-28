"""``SYNC#<resource>`` items: Google sync tokens and, from M1 step 4, watch
channel expiry (§3.2)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Self

from sundial.core import store


@dataclass(frozen=True, slots=True)
class SyncState:
    resource: str
    sync_token: str | None = None
    last_synced_at: str | None = None

    @classmethod
    def load(cls, uid: str, resource: str) -> Self:
        item = store.get(uid, store.sk_sync(resource))
        if item is None:
            return cls(resource=resource)
        return cls(
            resource=resource,
            sync_token=item.get("sync_token"),
            last_synced_at=item.get("last_synced_at"),
        )

    def save(self, uid: str) -> None:
        item: dict[str, object] = {
            "resource": self.resource,
            "last_synced_at": self.last_synced_at or store.iso(store.now()),
        }
        if self.sync_token:
            item["sync_token"] = self.sync_token
        store.put(uid, store.sk_sync(self.resource), item)

    def cleared(self) -> Self:
        """Drop the token after a 410, so the next run does a full resync."""
        return type(self)(resource=self.resource, last_synced_at=self.last_synced_at)
