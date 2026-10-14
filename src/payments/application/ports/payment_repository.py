"""Persistence port for the payment aggregate."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Protocol
from uuid import UUID

from payments.application.dto.webhook_delivery_result import WebhookDeliveryResult
from payments.domain.entities import Payment
from payments.domain.value_objects import PaymentOutcome


class PaymentRepositoryProtocol(Protocol):
    """Payment storage.

    Implementations live in the infrastructure layer; they map their own rows to
    the domain entity and never leak ORM objects through this port.
    """

    async def add_if_absent(self, payment: Payment) -> bool:
        """Insert the payment unless its idempotency key already exists.

        Returns False when the key is taken (`INSERT ... ON CONFLICT DO NOTHING`),
        so concurrent duplicates collapse into a single row instead of an error.
        """
        ...

    async def get(self, payment_id: UUID) -> Payment | None:
        """Return the payment with this id, or None."""
        ...

    async def get_by_idempotency_key(self, key: str) -> Payment | None:
        """Return the payment stored under this idempotency key, or None."""
        ...

    async def apply_outcome(self, payment_id: UUID, outcome: PaymentOutcome) -> bool:
        """Apply a terminal outcome with a guarded update.

        Returns False when the payment is already final, which keeps a duplicate
        delivery from touching the stored status.
        """
        ...

    async def claim_for_charging(
        self,
        payment_id: UUID,
        *,
        now: datetime,
        lease: timedelta,
    ) -> bool:
        """Take the payment for charging, atomically.

        Returns False when another worker already holds the claim or the payment is
        final, so two copies of the same event cannot both call the gateway. The
        claim is a lease: after `lease` a payment whose charging started but whose
        outcome was never stored becomes claimable again, otherwise a crash between
        the charge and the status update would leave it pending forever.
        """
        ...

    async def register_webhook_attempt(
        self,
        payment_id: UUID,
        result: WebhookDeliveryResult,
        *,
        now: datetime,
    ) -> None:
        """Record a webhook delivery attempt and its outcome."""
        ...


__all__ = ["PaymentRepositoryProtocol"]
