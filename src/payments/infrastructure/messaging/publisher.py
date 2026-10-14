"""Publication of outbox events to RabbitMQ."""

from __future__ import annotations

from typing import Any

from faststream.rabbit import RabbitBroker

from payments.application.ports.event_publisher import EventPublisherProtocol
from payments.domain.entities import OutboxEvent
from payments.infrastructure.messaging.topology import (
    ATTEMPT_HEADER,
    EVENT_ID_HEADER,
    EVENT_TYPE_HEADER,
    Topology,
)


class RabbitEventPublisher(EventPublisherProtocol):
    """Publishes an outbox event and waits for the broker confirmation."""

    def __init__(self, broker: RabbitBroker, topology: Topology) -> None:
        self._broker = broker
        self._topology = topology

    async def publish(self, event: OutboxEvent) -> None:
        """Send the event persistently; an error is raised for the relay to record."""
        await self._broker.publish(
            dict(event.payload),
            exchange=self._topology.exchange,
            routing_key=event.routing_key,
            headers=_event_headers(event),
            persist=True,
        )


def _event_headers(event: OutboxEvent) -> dict[str, Any]:
    """Headers that travel with the event from the very first publication."""
    return {
        ATTEMPT_HEADER: 0,
        EVENT_ID_HEADER: str(event.id),
        EVENT_TYPE_HEADER: event.event_type.value,
    }


__all__ = ["RabbitEventPublisher"]
