"""Message consumed from the `payments.new` queue."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID


@dataclass(frozen=True, slots=True)
class PaymentCreatedEvent:
    """A `payment.created` message with its delivery attempt counter."""

    event_id: UUID
    payment_id: UUID
    attempt: int = 0


__all__ = ["PaymentCreatedEvent"]
