"""Event publisher port."""

from __future__ import annotations

from typing import Protocol

from payments.domain.entities import OutboxEvent


class EventPublisherProtocol(Protocol):
    """Publishes outbox events to the broker."""

    async def publish(self, event: OutboxEvent) -> None:
        """Publish the event and wait for the broker confirmation."""
        ...


__all__ = ["EventPublisherProtocol"]
