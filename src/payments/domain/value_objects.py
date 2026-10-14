"""Immutable value objects of the payments domain."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Final
from urllib.parse import urlparse

from payments.domain.enums import Currency, PaymentStatus
from payments.domain.exceptions import (
    InvalidAmountError,
    InvalidCurrencyError,
    InvalidIdempotencyKeyError,
    InvalidWebhookTargetError,
    PaymentsDomainError,
)

FRACTION_DIGITS: Final[int] = 2
MAX_AMOUNT_DIGITS: Final[int] = 18
MAX_IDEMPOTENCY_KEY_LENGTH: Final[int] = 255
ALLOWED_WEBHOOK_SCHEMES: Final[frozenset[str]] = frozenset({"http", "https"})


@dataclass(frozen=True, slots=True)
class Money:
    """A positive monetary amount with an explicit currency.

    The amount keeps exactly two fraction digits, so its string form is stable and
    safe to put into JSON (money never travels as a float).
    """

    amount: Decimal
    currency: Currency

    def __post_init__(self) -> None:
        if not isinstance(self.amount, Decimal):
            raise InvalidAmountError(self.amount)
        if not self.amount.is_finite() or self.amount <= 0:
            raise InvalidAmountError(self.amount)
        if self.amount.as_tuple().exponent != -FRACTION_DIGITS:
            raise InvalidAmountError(self.amount)
        if len(self.amount.as_tuple().digits) > MAX_AMOUNT_DIGITS:
            raise InvalidAmountError(self.amount)
        if not isinstance(self.currency, Currency):
            raise InvalidCurrencyError(self.currency)

    def as_string(self) -> str:
        """Return the amount as a plain string with two fraction digits."""
        return f"{self.amount:.2f}"


@dataclass(frozen=True, slots=True)
class WebhookTarget:
    """A validated webhook destination."""

    url: str

    def __post_init__(self) -> None:
        if not isinstance(self.url, str):
            raise InvalidWebhookTargetError(message="webhook_url must be a string")
        parsed = urlparse(self.url)
        if parsed.scheme not in ALLOWED_WEBHOOK_SCHEMES or not parsed.hostname:
            raise InvalidWebhookTargetError(
                scheme=parsed.scheme,
                has_host=bool(parsed.hostname),
            )


@dataclass(frozen=True, slots=True)
class IdempotencyKey:
    """A client supplied idempotency key."""

    value: str

    def __post_init__(self) -> None:
        if not isinstance(self.value, str):
            raise InvalidIdempotencyKeyError(message="idempotency_key must be a string")
        length = len(self.value)
        if not 1 <= length <= MAX_IDEMPOTENCY_KEY_LENGTH:
            raise InvalidIdempotencyKeyError(length=length)


@dataclass(frozen=True, slots=True)
class PaymentOutcome:
    """The terminal outcome reported by the payment gateway.

    Only terminal statuses are allowed here: a business decline is a final state,
    not an exception (it is never retried).
    """

    status: PaymentStatus
    reason: str | None = None

    def __post_init__(self) -> None:
        if not self.status.is_final:
            raise PaymentsDomainError(
                "PaymentOutcome status must be SUCCEEDED or FAILED",
                details={"status": self.status.value},
            )
        if self.status is PaymentStatus.FAILED and not self.reason:
            raise PaymentsDomainError("A failed payment outcome must carry a reason")


__all__ = [
    "ALLOWED_WEBHOOK_SCHEMES",
    "FRACTION_DIGITS",
    "MAX_AMOUNT_DIGITS",
    "MAX_IDEMPOTENCY_KEY_LENGTH",
    "IdempotencyKey",
    "Money",
    "PaymentOutcome",
    "WebhookTarget",
]
