"""Domain enums of the payments context."""

from __future__ import annotations

from enum import StrEnum


class PaymentStatus(StrEnum):
    """Lifecycle states of a payment."""

    PENDING = "pending"
    SUCCEEDED = "succeeded"
    FAILED = "failed"

    @property
    def is_final(self) -> bool:
        """Return True when the status is terminal."""
        return self is not PaymentStatus.PENDING


class Currency(StrEnum):
    """Currencies supported by the service."""

    RUB = "RUB"
    USD = "USD"
    EUR = "EUR"


class EventType(StrEnum):
    """Types of events written to the outbox."""

    PAYMENT_CREATED = "payment.created"
