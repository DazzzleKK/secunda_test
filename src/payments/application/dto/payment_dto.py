"""Snapshot of a payment, free of ORM and API concerns."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from payments.domain.entities import Payment
from payments.domain.enums import Currency, PaymentStatus


@dataclass(frozen=True, slots=True)
class PaymentDto:
    """Read model of a payment; money travels as a string."""

    payment_id: UUID
    amount: str
    currency: Currency
    description: str
    metadata: Mapping[str, Any]
    status: PaymentStatus
    idempotency_key: str
    webhook_url: str
    created_at: datetime
    updated_at: datetime
    processed_at: datetime | None
    failure_reason: str | None
    webhook_attempts: int
    webhook_delivered_at: datetime | None
    webhook_last_error: str | None

    @classmethod
    def from_entity(cls, payment: Payment) -> PaymentDto:
        """Build a DTO from the domain entity."""
        return cls(
            payment_id=payment.id,
            amount=payment.money.as_string(),
            currency=payment.money.currency,
            description=payment.description,
            metadata=dict(payment.metadata),
            status=payment.status,
            idempotency_key=payment.idempotency_key,
            webhook_url=payment.webhook_url,
            created_at=payment.created_at,
            updated_at=payment.updated_at,
            processed_at=payment.processed_at,
            failure_reason=payment.failure_reason,
            webhook_attempts=payment.webhook_attempts,
            webhook_delivered_at=payment.webhook_delivered_at,
            webhook_last_error=payment.webhook_last_error,
        )


__all__ = ["PaymentDto"]
