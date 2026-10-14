"""Domain exceptions of the payments context.

Every error carries a stable `code` and an explicit whitelist of context fields in
`details`; the HTTP layer maps `code` to a status code and passes `details` through.
"""

from __future__ import annotations

from typing import Any, ClassVar
from uuid import UUID

from shared.exceptions import DomainError


class PaymentsDomainError(DomainError):
    """Base class of every payments domain error."""

    code: ClassVar[str] = "payments_error"
    default_message: ClassVar[str] = "Payments domain rule violated"


class PaymentNotFoundError(PaymentsDomainError):
    """The requested payment does not exist."""

    code = "payment_not_found"
    default_message = "Payment not found"

    def __init__(self, payment_id: UUID, message: str | None = None) -> None:
        super().__init__(message, details={"payment_id": str(payment_id)})
        self.payment_id = payment_id


class IdempotencyConflictError(PaymentsDomainError):
    """The idempotency key was reused with a different request payload."""

    code = "idempotency_conflict"
    default_message = "Idempotency key was reused with a different request payload"

    def __init__(self, idempotency_key: str, message: str | None = None) -> None:
        super().__init__(message, details={"idempotency_key": idempotency_key})
        self.idempotency_key = idempotency_key


class InvalidAmountError(PaymentsDomainError):
    """The amount violates the money invariant."""

    code = "invalid_amount"
    default_message = "Amount must be a finite positive decimal with exactly two fraction digits"

    def __init__(self, amount: object, message: str | None = None) -> None:
        super().__init__(message, details={"amount": str(amount)})
        self.amount = amount


class PaymentGatewayUnavailableError(PaymentsDomainError):
    """The payment gateway could not be reached (technical failure, retryable)."""

    code = "payment_gateway_unavailable"
    default_message = "Payment gateway is unavailable"

    def __init__(self, payment_id: UUID, reason: str | None = None) -> None:
        details: dict[str, Any] = {"payment_id": str(payment_id)}
        if reason is not None:
            details["reason"] = reason
        super().__init__(reason, details=details)
        self.payment_id = payment_id


class WebhookDeliveryError(PaymentsDomainError):
    """A webhook delivery attempt failed (drives the retry/DLQ ladder)."""

    code = "webhook_delivery_failed"
    default_message = "Webhook delivery failed"

    def __init__(self, payment_id: UUID, *, attempts: int, reason: str | None = None) -> None:
        details: dict[str, Any] = {"payment_id": str(payment_id), "attempts": attempts}
        if reason is not None:
            details["reason"] = reason
        super().__init__(reason, details=details)
        self.payment_id = payment_id
        self.attempts = attempts


class PaymentProcessingError(PaymentsDomainError):
    """Technical failure while processing a payment (retryable)."""

    code = "payment_processing_failed"
    default_message = "Payment processing failed"

    def __init__(self, message: str | None = None, *, payment_id: UUID | None = None) -> None:
        details: dict[str, Any] = {}
        if payment_id is not None:
            details["payment_id"] = str(payment_id)
        super().__init__(message, details=details)
        self.payment_id = payment_id


class PaymentNotFinalizedError(PaymentsDomainError):
    """The webhook was requested for a payment that has no final status yet."""

    code = "payment_not_finalized"
    default_message = "Payment has no final status yet"

    def __init__(self, payment_id: UUID, message: str | None = None) -> None:
        super().__init__(message, details={"payment_id": str(payment_id)})
        self.payment_id = payment_id


__all__ = [
    "IdempotencyConflictError",
    "InvalidAmountError",
    "PaymentGatewayUnavailableError",
    "PaymentNotFinalizedError",
    "PaymentNotFoundError",
    "PaymentProcessingError",
    "PaymentsDomainError",
    "WebhookDeliveryError",
]
