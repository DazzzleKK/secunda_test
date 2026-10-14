"""Consumer of `payments.new`: one handler that does the whole job.

The handler is also where the transport policy lives: what to acknowledge, what to
retry with a delay and what to park in the dead letter queue. The use cases below it
know nothing about RabbitMQ.

Rules of the policy:

* business outcome (approved or declined) is never retried — it is a result;
* a technical failure (`PaymentProcessingError`, `WebhookDeliveryError`, database or
  broker trouble) goes to the next retry tier;
* when the tiers are exhausted the message goes to `payments.dlq` with the reason;
* a poison message (unknown payment, unparseable body) goes to the DLQ at once;
* `requeue=True` is never used: it turns a bad message into an endless loop.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any, Final
from uuid import UUID

from faststream import AckPolicy
from faststream.rabbit import RabbitBroker, RabbitExchange, RabbitMessage

from payments.application.dto.payment_created_event import PaymentCreatedEvent
from payments.application.use_cases.deliver_webhook import DeliverWebhookUseCase
from payments.application.use_cases.process_payment import ProcessPaymentUseCase
from payments.domain.exceptions import PaymentNotFoundError
from payments.infrastructure.messaging.topology import (
    ATTEMPT_HEADER,
    DLQ_ROUTING_KEY,
    ERROR_HEADER,
    FAILED_AT_HEADER,
    Topology,
)
from shared.exceptions import AppError, InfraError

logger = logging.getLogger(__name__)

MAX_STORED_ERROR_LENGTH: Final[int] = 500
PUBLISH_TIMEOUT_SECONDS: Final[float] = 5.0


class InvalidEventPayloadError(Exception):
    """The message is not a payment event; it must not be retried."""


def parse_event(body: Any, headers: Mapping[str, Any]) -> PaymentCreatedEvent:
    """Build the event; the attempt counter is read from the header, not the body."""
    try:
        document: Mapping[str, Any] = (
            json.loads(body) if isinstance(body, (bytes, bytearray, str)) else dict(body)
        )
        attempt = int(headers.get(ATTEMPT_HEADER, document.get("attempt", 0)))
        return PaymentCreatedEvent(
            event_id=UUID(str(document["event_id"])),
            payment_id=UUID(str(document["payment_id"])),
            attempt=attempt,
        )
    except (AttributeError, KeyError, TypeError, ValueError) as exc:
        raise InvalidEventPayloadError(str(exc)) from exc


class PaymentConsumer:
    """Processes one payment event and confirms the message accordingly."""

    def __init__(
        self,
        *,
        broker: RabbitBroker,
        topology: Topology,
        process_payment: ProcessPaymentUseCase,
        deliver_webhook: DeliverWebhookUseCase,
    ) -> None:
        self._broker = broker
        self._topology = topology
        self._process_payment = process_payment
        self._deliver_webhook = deliver_webhook

    def register(self, broker: RabbitBroker) -> None:
        """Attach the handler to the broker (called once, at startup)."""
        topology = self._topology

        @broker.subscriber(
            queue=topology.work_queue,
            exchange=topology.exchange,
            channel=topology.channel,
            ack_policy=AckPolicy.MANUAL,
        )
        async def handle(message: RabbitMessage) -> None:
            await self.handle(message)

    async def handle(self, message: RabbitMessage) -> None:
        """Run the use cases and confirm, retry or park the message."""
        try:
            event = parse_event(message.body, message.headers)
        except InvalidEventPayloadError as exc:
            logger.warning("poison message parked", extra={"error": str(exc)})
            await self._park(message, reason=f"invalid payload: {exc}", attempt=0)
            return

        try:
            await self._process_payment.execute(event)
            await self._deliver_webhook.execute(event)
        except PaymentNotFoundError as exc:
            logger.warning(
                "unknown payment parked",
                extra={"payment_id": str(event.payment_id), "event_id": str(event.event_id)},
            )
            await self._park(message, reason=exc.message, attempt=event.attempt)
            return
        except InfraError as exc:
            # The database or the broker is in trouble, not this payment: deferring
            # keeps the work without burning one of the three attempts, so a short
            # outage cannot park healthy payments in the DLQ.
            await self._defer(message, exc, event)
            return
        except Exception as exc:  # every failure must be routed, not lost
            await self._retry(message, exc, event)
            return

        await message.ack()

    async def _retry(
        self,
        message: RabbitMessage,
        exc: BaseException,
        event: PaymentCreatedEvent,
    ) -> None:
        """Move the message to the next tier, or park it when they are exhausted."""
        reason = _describe(exc)
        target = self._topology.retry_target(event.attempt)
        if target is None:
            logger.error(
                "attempts exhausted, parking the message",
                extra={
                    "payment_id": str(event.payment_id),
                    "event_id": str(event.event_id),
                    "attempts": self._topology.max_attempts,
                    "error": reason,
                },
            )
            await self._park(message, reason=reason, attempt=event.attempt + 1)
            return

        logger.warning(
            "retrying the message",
            extra={
                "payment_id": str(event.payment_id),
                "event_id": str(event.event_id),
                "tier": target.tier,
                "delay_ms": target.delay_ms,
                "error": reason,
            },
        )
        await self._republish(
            message,
            exchange=self._topology.retry_exchange,
            routing_key=target.routing_key,
            headers=_forwarded_headers(message, attempt=event.attempt + 1, reason=reason),
        )

    async def _defer(
        self,
        message: RabbitMessage,
        exc: BaseException,
        event: PaymentCreatedEvent,
    ) -> None:
        """Send the message to the slowest tier without consuming an attempt."""
        target = self._topology.retry_tiers[-1]
        reason = _describe(exc)
        logger.warning(
            "infrastructure failure, deferring the message",
            extra={
                "payment_id": str(event.payment_id),
                "event_id": str(event.event_id),
                "delay_ms": target.delay_ms,
                "error": reason,
            },
        )
        await self._republish(
            message,
            exchange=self._topology.retry_exchange,
            routing_key=target.routing_key,
            headers=_forwarded_headers(message, attempt=event.attempt, reason=reason),
        )

    async def _park(self, message: RabbitMessage, *, reason: str, attempt: int) -> None:
        """Send the message to the dead letter queue and confirm the original."""
        await self._republish(
            message,
            exchange=self._topology.dlx_exchange,
            routing_key=DLQ_ROUTING_KEY,
            headers=_forwarded_headers(message, attempt=attempt, reason=reason),
        )

    async def _republish(
        self,
        message: RabbitMessage,
        *,
        exchange: RabbitExchange,
        routing_key: str,
        headers: dict[str, Any],
    ) -> None:
        """Republish the message and confirm the original only if that worked.

        Publishing is bounded by a timeout: a broker that stopped answering must not
        hold the consumer slot forever. When the republish fails the message stays
        unacked, so the broker redelivers it instead of losing it.
        """
        try:
            await self._broker.publish(
                _body_of(message),
                exchange=exchange,
                routing_key=routing_key,
                headers=headers,
                persist=True,
                timeout=PUBLISH_TIMEOUT_SECONDS,
            )
        except Exception as exc:
            logger.error(
                "could not republish the message, it stays unacked for redelivery: %s",
                _describe(exc),
            )
            return
        await message.ack()


def _body_of(message: RabbitMessage) -> Any:
    """Raw body of the message, ready to be republished as is."""
    body = message.body
    if isinstance(body, (bytes, bytearray, str)):
        return body
    return json.dumps(body, ensure_ascii=False).encode("utf-8")


def _forwarded_headers(
    message: RabbitMessage,
    *,
    attempt: int,
    reason: str,
) -> dict[str, Any]:
    """Original headers plus the attempt counter and the failure context."""
    headers: dict[str, Any] = {str(key): value for key, value in (message.headers or {}).items()}
    headers[ATTEMPT_HEADER] = attempt
    headers[ERROR_HEADER] = reason
    headers[FAILED_AT_HEADER] = datetime.now(UTC).isoformat()
    return headers


def _describe(exc: BaseException) -> str:
    """Short, safe description of a failure for the message headers and the logs."""
    if isinstance(exc, AppError):
        return f"{exc.code}: {exc.message}"[:MAX_STORED_ERROR_LENGTH]
    return f"{type(exc).__name__}: {exc}"[:MAX_STORED_ERROR_LENGTH]


__all__ = ["InvalidEventPayloadError", "PaymentConsumer", "parse_event"]
