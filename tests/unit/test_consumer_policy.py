"""Transport policy of the consumer: what is acked, retried, deferred or parked."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import pytest

from payments.domain.exceptions import (
    PaymentNotFoundError,
    WebhookDeliveryError,
)
from payments.infrastructure.messaging.consumer import PaymentConsumer, parse_event
from payments.infrastructure.messaging.topology import (
    ATTEMPT_HEADER,
    DLQ_NAME,
    ERROR_HEADER,
    Topology,
)
from shared.exceptions import InfraError

RETRY_DELAYS_MS = (300, 900)
PAYMENT_ID = UUID(int=1)
EVENT_ID = UUID(int=2)


class StubMessage:
    """Minimal stand-in for a RabbitMQ message."""

    def __init__(self, *, attempt: int = 0) -> None:
        self.body = json.dumps(
            {
                "event_id": str(EVENT_ID),
                "event": "payment.created",
                "payment_id": str(PAYMENT_ID),
                "attempt": 0,
            }
        ).encode("utf-8")
        self.headers = {ATTEMPT_HEADER: attempt}
        self.acked = False
        self.nacked = False

    async def ack(self) -> None:
        self.acked = True

    async def nack(self, requeue: bool = False) -> None:
        self.nacked = True


class StubBroker:
    """Records what the consumer republished."""

    def __init__(self, *, fail: bool = False) -> None:
        self.published: list[dict[str, Any]] = []
        self._fail = fail

    async def publish(self, body: Any, **kwargs: Any) -> None:
        if self._fail:
            raise ConnectionError("broker is unreachable")
        self.published.append({"body": body, **kwargs})


class StubUseCase:
    """A use case that raises a configured error."""

    def __init__(self, error: BaseException | None = None) -> None:
        self._error = error
        self.calls = 0

    async def execute(self, event: Any) -> None:
        self.calls += 1
        if self._error is not None:
            raise self._error


def _consumer(
    *,
    process_error: BaseException | None = None,
    webhook_error: BaseException | None = None,
    broker: StubBroker | None = None,
) -> tuple[PaymentConsumer, StubBroker, StubUseCase]:
    resolved_broker = broker if broker is not None else StubBroker()
    process = StubUseCase(process_error)
    consumer = PaymentConsumer(
        broker=resolved_broker,
        topology=Topology(retry_delays_ms=RETRY_DELAYS_MS),
        process_payment=process,
        deliver_webhook=StubUseCase(webhook_error),
    )
    return consumer, resolved_broker, process


def test_the_event_is_parsed_from_the_body_and_the_attempt_from_the_header() -> None:
    event = parse_event(
        json.dumps({"event_id": str(EVENT_ID), "payment_id": str(PAYMENT_ID)}),
        {ATTEMPT_HEADER: 2},
    )

    assert event.event_id == EVENT_ID
    assert event.payment_id == PAYMENT_ID
    assert event.attempt == 2


def test_a_body_without_the_ids_is_not_an_event() -> None:
    from payments.infrastructure.messaging.consumer import InvalidEventPayloadError

    with pytest.raises(InvalidEventPayloadError):
        parse_event(b'{"event": "payment.created"}', {})


async def test_a_processed_message_is_acked() -> None:
    consumer, broker, process = _consumer()
    message = StubMessage()

    await consumer.handle(message)  # type: ignore[arg-type]

    assert process.calls == 1
    assert message.acked is True
    assert broker.published == []


async def test_a_technical_failure_goes_to_the_next_tier_with_a_new_attempt() -> None:
    consumer, broker, _ = _consumer(
        webhook_error=WebhookDeliveryError(PAYMENT_ID, attempts=1, reason="HTTP 500")
    )
    message = StubMessage(attempt=0)

    await consumer.handle(message)  # type: ignore[arg-type]

    assert len(broker.published) == 1
    published = broker.published[0]
    assert published["routing_key"] == "retry.1"
    assert published["headers"][ATTEMPT_HEADER] == 1
    assert message.acked is True


async def test_the_last_attempt_parks_the_message() -> None:
    consumer, broker, _ = _consumer(
        webhook_error=WebhookDeliveryError(PAYMENT_ID, attempts=3, reason="HTTP 500")
    )
    message = StubMessage(attempt=2)

    await consumer.handle(message)  # type: ignore[arg-type]

    assert len(broker.published) == 1
    assert broker.published[0]["routing_key"] == DLQ_NAME
    assert broker.published[0]["headers"][ATTEMPT_HEADER] == 3
    assert message.acked is True


async def test_an_infrastructure_failure_is_deferred_without_burning_an_attempt() -> None:
    """A database outage must not park healthy payments in the DLQ."""
    consumer, broker, _ = _consumer(process_error=InfraError("database is unavailable"))
    message = StubMessage(attempt=0)

    await consumer.handle(message)  # type: ignore[arg-type]

    assert len(broker.published) == 1
    published = broker.published[0]
    assert published["routing_key"] == "retry.2"
    assert published["headers"][ATTEMPT_HEADER] == 0
    assert "infra_error" in published["headers"][ERROR_HEADER]
    assert message.acked is True


async def test_an_infrastructure_failure_never_parks_even_on_the_last_attempt() -> None:
    consumer, broker, _ = _consumer(process_error=InfraError("database is unavailable"))
    message = StubMessage(attempt=2)

    await consumer.handle(message)  # type: ignore[arg-type]

    assert broker.published[0]["routing_key"] == "retry.2"
    assert message.acked is True


async def test_an_unknown_payment_is_parked_immediately() -> None:
    consumer, broker, _ = _consumer(process_error=PaymentNotFoundError(PAYMENT_ID))
    message = StubMessage(attempt=0)

    await consumer.handle(message)  # type: ignore[arg-type]

    assert broker.published[0]["routing_key"] == DLQ_NAME
    assert broker.published[0]["headers"][ATTEMPT_HEADER] == 0


async def test_a_poison_body_is_parked_immediately() -> None:
    consumer, broker, _ = _consumer()
    message = StubMessage()
    message.body = b'{"not": "an event"}'

    await consumer.handle(message)  # type: ignore[arg-type]

    assert broker.published[0]["routing_key"] == DLQ_NAME
    assert message.acked is True


async def test_a_failed_republish_leaves_the_message_unacked() -> None:
    """If the broker does not take the message, it must stay for redelivery."""
    consumer, _, _ = _consumer(
        webhook_error=WebhookDeliveryError(PAYMENT_ID, attempts=1, reason="HTTP 500"),
        broker=StubBroker(fail=True),
    )
    message = StubMessage(attempt=0)

    await consumer.handle(message)  # type: ignore[arg-type]

    assert message.acked is False
    assert message.nacked is False


async def test_the_failure_context_is_recorded_in_the_headers() -> None:
    consumer, broker, _ = _consumer(process_error=InfraError("database is unavailable"))

    await consumer.handle(StubMessage())  # type: ignore[arg-type]

    headers = broker.published[0]["headers"]
    assert datetime.fromisoformat(headers["x-failed-at"]).tzinfo is UTC
