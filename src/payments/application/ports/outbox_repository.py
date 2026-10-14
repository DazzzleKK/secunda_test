"""Persistence port for the transactional outbox."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import Protocol
from uuid import UUID

from payments.domain.entities import OutboxEvent


class OutboxRepositoryProtocol(Protocol):
    """Outbox storage used by the create use case and the relay."""

    async def add(self, event: OutboxEvent) -> None:
        """Append an event to the outbox inside the current transaction."""
        ...

    async def fetch_unpublished(self, limit: int) -> Sequence[OutboxEvent]:
        """Lock and return up to `limit` unpublished events, oldest first.

        The adapter uses `FOR UPDATE SKIP LOCKED`, so two relays never take the
        same row.
        """
        ...

    async def mark_published(self, event_id: UUID, *, now: datetime) -> None:
        """Stamp `published_at` after the broker confirmed the message."""
        ...

    async def register_failure(self, event_id: UUID, error: str) -> None:
        """Record a publication failure: bump attempts and store the error."""
        ...

    async def oldest_unpublished_age(self) -> timedelta | None:
        """Return the age of the oldest unpublished event, or None."""
        ...


__all__ = ["OutboxRepositoryProtocol"]
