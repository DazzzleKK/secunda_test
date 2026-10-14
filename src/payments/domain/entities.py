"""Domain entities of the payments context."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any, ClassVar, Self
from uuid import UUID, uuid4

from payments.domain.enums import EventType, PaymentStatus
from payments.domain.services import build_request_fingerprint, canonicalize_metadata
from payments.domain.value_objects import IdempotencyKey, Money, PaymentOutcome, WebhookTarget

FALLBACK_FAILURE_REASON = "gateway declined the payment without a reason"


@dataclass(slots=True)
class Payment:
    """The payment aggregate root.

    The entity owns the terminal transitions: they are guarded, so a repeated
    transition returns False instead of overwriting an already final status.
    """

    id: UUID
    money: Money
    description: str
    metadata: Mapping[str, Any]
    idempotency_key: str
    request_fingerprint: str
    webhook_url: str
    status: PaymentStatus
    created_at: datetime
    updated_at: datetime
    processed_at: datetime | None = None
    failure_reason: str | None = None
    webhook_attempts: int = 0
    webhook_delivered_at: datetime | None = None
    webhook_last_error: str | None = None

    @classmethod
    def create(
        cls,
        *,
        payment_id: UUID,
        money: Money,
        description: str,
        metadata: Mapping[str, Any],
        idempotency_key: IdempotencyKey,
        webhook_target: WebhookTarget,
        now: datetime,
    ) -> Self:
        """Create a pending payment, deriving its request fingerprint.

        The metadata is frozen once and the same frozen copy feeds both the stored
        payload and the fingerprint, so the two can never diverge.
        """
        frozen_metadata = canonicalize_metadata(metadata)
        return cls(
            id=payment_id,
            money=money,
            description=description,
            metadata=frozen_metadata,
            idempotency_key=idempotency_key.value,
            request_fingerprint=build_request_fingerprint(
                money, description, frozen_metadata, webhook_target.url
            ),
            webhook_url=webhook_target.url,
            status=PaymentStatus.PENDING,
            created_at=now,
            updated_at=now,
        )

    @property
    def is_final(self) -> bool:
        """Return True when the payment reached a terminal status."""
        return self.status.is_final

    @property
    def webhook_pending(self) -> bool:
        """Return True when the payment is final but its webhook is not delivered yet."""
        return self.is_final and self.webhook_delivered_at is None

    def mark_succeeded(self, now: datetime) -> bool:
        """Move a pending payment to SUCCEEDED; return False when already final."""
        if self.is_final:
            return False
        self.status = PaymentStatus.SUCCEEDED
        self.failure_reason = None
        self.processed_at = now
        self.updated_at = now
        return True

    def mark_failed(self, reason: str, now: datetime) -> bool:
        """Move a pending payment to FAILED; return False when already final."""
        if self.is_final:
            return False
        self.status = PaymentStatus.FAILED
        self.failure_reason = reason
        self.processed_at = now
        self.updated_at = now
        return True

    def apply_outcome(self, outcome: PaymentOutcome, *, now: datetime) -> bool:
        """Apply a gateway outcome through the guarded terminal transition.

        Returns False when the payment already has a final status, which makes the
        transition idempotent under at-least-once delivery.
        """
        if outcome.status is PaymentStatus.SUCCEEDED:
            return self.mark_succeeded(now)
        return self.mark_failed(outcome.reason or FALLBACK_FAILURE_REASON, now)


@dataclass(frozen=True, slots=True)
class OutboxEvent:
    """An event stored in the outbox and published later by the relay."""

    PAYMENT_CREATED_ROUTING_KEY: ClassVar[str] = "payments.new"

    id: UUID
    aggregate_id: UUID
    event_type: EventType
    routing_key: str
    payload: Mapping[str, Any]
    created_at: datetime
    attempts: int = 0
    last_error: str | None = None
    published_at: datetime | None = None

    @classmethod
    def payment_created(
        cls,
        payment: Payment,
        *,
        now: datetime,
        event_id: UUID | None = None,
    ) -> Self:
        """Build the `payment.created` event describing a freshly created payment."""
        identifier = event_id if event_id is not None else uuid4()
        return cls(
            id=identifier,
            aggregate_id=payment.id,
            event_type=EventType.PAYMENT_CREATED,
            routing_key=cls.PAYMENT_CREATED_ROUTING_KEY,
            payload={
                "event_id": str(identifier),
                "event": EventType.PAYMENT_CREATED.value,
                "payment_id": str(payment.id),
                "attempt": 0,
                "status": payment.status.value,
                "amount": payment.money.as_string(),
                "currency": payment.money.currency.value,
                "occurred_at": now.isoformat(),
            },
            created_at=now,
        )


__all__ = ["FALLBACK_FAILURE_REASON", "OutboxEvent", "Payment"]
