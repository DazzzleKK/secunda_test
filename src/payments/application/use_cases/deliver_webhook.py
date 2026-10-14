"""Deliver webhook use case."""

from __future__ import annotations

from uuid import UUID

from payments.application.dto.payment_created_event import PaymentCreatedEvent
from payments.application.dto.webhook_delivery_result import WebhookDeliveryResult
from payments.application.dto.webhook_payload import WebhookPayload
from payments.application.ports.clock import ClockProtocol
from payments.application.ports.unit_of_work import UnitOfWorkFactoryProtocol
from payments.application.ports.webhook_sender import WebhookSenderProtocol
from payments.domain.entities import Payment
from payments.domain.enums import EventType
from payments.domain.exceptions import (
    PaymentNotFinalizedError,
    PaymentNotFoundError,
    WebhookDeliveryError,
)
from payments.domain.value_objects import WebhookTarget

ALREADY_DELIVERED = WebhookDeliveryResult(delivered=True)


class DeliverWebhookUseCase:
    """Notify the merchant about the final status of a payment.

    The HTTP call is made outside a transaction: the sender may take seconds while
    a webhook is retried, and only its result is written back. A payment that was
    already delivered is a no-op, so a duplicate message cannot send twice.
    """

    def __init__(
        self,
        uow_factory: UnitOfWorkFactoryProtocol,
        sender: WebhookSenderProtocol,
        clock: ClockProtocol,
    ) -> None:
        self._uow_factory = uow_factory
        self._sender = sender
        self._clock = clock

    async def execute(self, event: PaymentCreatedEvent) -> WebhookDeliveryResult:
        """Deliver the webhook once and store the attempt.

        Raises:
            PaymentNotFoundError: the message references an unknown payment.
            PaymentNotFinalizedError: the payment has no final status yet.
            WebhookDeliveryError: the delivery failed (drives the retry ladder).
        """
        payment = await self._load(event.payment_id)
        if not payment.is_final:
            raise PaymentNotFinalizedError(payment.id)
        if payment.webhook_delivered_at is not None:
            return ALREADY_DELIVERED

        attempt = event.attempt + 1
        result = await self._sender.send(
            WebhookTarget(payment.webhook_url),
            self._build_payload(payment, event),
            attempt=attempt,
        )

        async with self._uow_factory() as uow:
            await uow.payments.register_webhook_attempt(payment.id, result, now=self._clock.now())
            await uow.commit()

        if not result.delivered:
            raise WebhookDeliveryError(payment.id, attempts=attempt, reason=result.error)
        return result

    async def _load(self, payment_id: UUID) -> Payment:
        async with self._uow_factory() as uow:
            payment = await uow.payments.get(payment_id)
        if payment is None:
            raise PaymentNotFoundError(payment_id)
        return payment

    @staticmethod
    def _build_payload(payment: Payment, event: PaymentCreatedEvent) -> WebhookPayload:
        return WebhookPayload(
            event_id=event.event_id,
            event=EventType.PAYMENT_CREATED.value,
            payment_id=payment.id,
            status=payment.status.value,
            amount=payment.money.as_string(),
            currency=payment.money.currency.value,
            occurred_at=payment.processed_at or payment.updated_at,
        )


__all__ = ["ALREADY_DELIVERED", "DeliverWebhookUseCase"]
