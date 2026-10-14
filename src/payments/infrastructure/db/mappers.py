"""Explicit mapping between ORM rows and domain objects."""

from __future__ import annotations

from typing import Any

from payments.domain.entities import OutboxEvent, Payment
from payments.domain.enums import Currency, EventType, PaymentStatus
from payments.domain.value_objects import Money
from payments.infrastructure.db.models import OutboxModel, PaymentModel


def payment_to_entity(model: PaymentModel) -> Payment:
    """Build the domain entity from a stored row."""
    return Payment(
        id=model.id,
        money=Money(amount=model.amount, currency=Currency(model.currency)),
        description=model.description,
        metadata=dict(model.payment_metadata),
        idempotency_key=model.idempotency_key,
        request_fingerprint=model.request_fingerprint,
        webhook_url=model.webhook_url,
        status=PaymentStatus(model.status),
        created_at=model.created_at,
        updated_at=model.updated_at,
        processed_at=model.processed_at,
        failure_reason=model.failure_reason,
        webhook_attempts=model.webhook_attempts,
        webhook_delivered_at=model.webhook_delivered_at,
        webhook_last_error=model.webhook_last_error,
    )


def payment_to_row(payment: Payment) -> dict[str, Any]:
    """Build the column values of a new payment row."""
    return {
        "id": payment.id,
        "amount": payment.money.amount,
        "currency": payment.money.currency.value,
        "description": payment.description,
        "metadata": dict(payment.metadata),
        "status": payment.status.value,
        "idempotency_key": payment.idempotency_key,
        "request_fingerprint": payment.request_fingerprint,
        "webhook_url": payment.webhook_url,
        "failure_reason": payment.failure_reason,
        "webhook_attempts": payment.webhook_attempts,
        "webhook_delivered_at": payment.webhook_delivered_at,
        "webhook_last_error": payment.webhook_last_error,
        "created_at": payment.created_at,
        "updated_at": payment.updated_at,
        "processed_at": payment.processed_at,
    }


def outbox_to_entity(model: OutboxModel) -> OutboxEvent:
    """Build the outbox event from a stored row."""
    return OutboxEvent(
        id=model.id,
        aggregate_id=model.aggregate_id,
        event_type=EventType(model.event_type),
        routing_key=model.routing_key,
        payload=dict(model.payload),
        created_at=model.created_at,
        attempts=model.attempts,
        last_error=model.last_error,
        published_at=model.published_at,
    )


def outbox_to_row(event: OutboxEvent) -> dict[str, Any]:
    """Build the column values of a new outbox row."""
    return {
        "id": event.id,
        "aggregate_id": event.aggregate_id,
        "event_type": event.event_type.value,
        "routing_key": event.routing_key,
        "payload": dict(event.payload),
        "attempts": event.attempts,
        "last_error": event.last_error,
        "created_at": event.created_at,
        "published_at": event.published_at,
    }


__all__ = [
    "outbox_to_entity",
    "outbox_to_row",
    "payment_to_entity",
    "payment_to_row",
]
