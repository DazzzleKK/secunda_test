"""Publication of outbox events to RabbitMQ."""

from __future__ import annotations

import json
from typing import Any, Final

from faststream.rabbit import RabbitBroker

from payments.application.ports.event_publisher import EventPublisherProtocol
from payments.domain.entities import OutboxEvent
from payments.domain.exceptions import EventPayloadInvalidError
from payments.infrastructure.messaging.topology import (
    ATTEMPT_HEADER,
    EVENT_ID_HEADER,
    EVENT_TYPE_HEADER,
    Topology,
)

PUBLISH_TIMEOUT_SECONDS: Final[float] = 5.0
JSON_CONTENT_TYPE: Final[str] = "application/json"
MAX_STORED_ERROR_LENGTH: Final[int] = 500


class RabbitEventPublisher(EventPublisherProtocol):
    """Publishes an outbox event and waits for the broker confirmation."""

    def __init__(
        self,
        broker: RabbitBroker,
        topology: Topology,
        *,
        timeout_seconds: float = PUBLISH_TIMEOUT_SECONDS,
    ) -> None:
        self._broker = broker
        self._topology = topology
        self._timeout = timeout_seconds

    async def publish(self, event: OutboxEvent) -> None:
        """Send the event persistently and wait for the broker confirmation.

        The body is serialised here, before the network call: a payload that cannot
        be serialised can never be published, so it is reported as a *permanent*
        failure. Everything the broker does afterwards is transport trouble and is
        bounded by the timeout — the relay keeps the event and retries on its next
        tick instead of blocking on a dead connection.
        """
        body = _serialise(event)
        await self._broker.publish(
            body,
            exchange=self._topology.exchange,
            routing_key=event.routing_key,
            headers=_event_headers(event),
            content_type=JSON_CONTENT_TYPE,
            persist=True,
            timeout=self._timeout,
        )


def _serialise(event: OutboxEvent) -> bytes:
    """Render the payload as JSON bytes, or report it as unpublishable."""
    try:
        return json.dumps(dict(event.payload), separators=(",", ":"), ensure_ascii=False).encode(
            "utf-8"
        )
    except (TypeError, ValueError) as exc:
        raise EventPayloadInvalidError(event.id, reason=str(exc)[:MAX_STORED_ERROR_LENGTH]) from exc


def _event_headers(event: OutboxEvent) -> dict[str, Any]:
    """Headers that travel with the event from the very first publication."""
    return {
        ATTEMPT_HEADER: 0,
        EVENT_ID_HEADER: str(event.id),
        EVENT_TYPE_HEADER: event.event_type.value,
    }


__all__ = ["RabbitEventPublisher"]
