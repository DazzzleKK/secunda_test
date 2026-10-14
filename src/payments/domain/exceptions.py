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


class InvalidCurrencyError(PaymentsDomainError):
    """The currency is not one of the supported ones."""

    code = "invalid_currency"
    default_message = "Currency must be one of RUB, USD, EUR"

    def __init__(self, currency: object, message: str | None = None) -> None:
        super().__init__(message, details={"currency": str(currency)})
        self.currency = currency


class InvalidMetadataError(PaymentsDomainError):
    """The metadata is not a JSON object or exceeds the size limit."""

    code = "invalid_metadata"
    default_message = "Metadata must be a JSON object within the size limit"

    def __init__(
        self,
        message: str | None = None,
        *,
        reason: str | None = None,
        size_bytes: int | None = None,
        limit_bytes: int | None = None,
    ) -> None:
        details: dict[str, Any] = {}
        if reason is not None:
            details["reason"] = reason
        if size_bytes is not None:
            details["size_bytes"] = size_bytes
        if limit_bytes is not None:
            details["limit_bytes"] = limit_bytes
        super().__init__(message or reason, details=details)
        self.reason = reason


class InvalidWebhookTargetError(PaymentsDomainError):
    """The webhook URL is not an absolute http(s) URL with a host.

    The URL itself is never echoed back: it may carry credentials in its userinfo
    part, and `details` reaches the HTTP client.
    """

    code = "invalid_webhook_url"
    default_message = "Webhook URL must be an absolute http(s) URL with a host"

    def __init__(
        self,
        *,
        scheme: str = "",
        has_host: bool = False,
        message: str | None = None,
    ) -> None:
        super().__init__(message, details={"scheme": scheme, "has_host": has_host})
        self.scheme = scheme
        self.has_host = has_host


class InvalidIdempotencyKeyError(PaymentsDomainError):
    """The idempotency key is empty, too long or not a string."""

    code = "invalid_idempotency_key"
    default_message = "Idempotency key length must be between 1 and 255"

    def __init__(self, *, length: int | None = None, message: str | None = None) -> None:
        details: dict[str, Any] = {} if length is None else {"idempotency_key_length": length}
        super().__init__(message, details=details)
        self.length = length


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


class PaymentNotVisibleError(PaymentsDomainError):
    """The winner of an idempotency race is committed but still unreadable.

    Reported as a retryable conflict: the request was not lost, its result is just
    not visible to the current transaction yet.
    """

    code = "payment_not_visible"
    default_message = "Payment is already stored but not readable yet, retry the request"

    def __init__(self, payment_id: UUID, message: str | None = None) -> None:
        super().__init__(message, details={"payment_id": str(payment_id)})
        self.payment_id = payment_id


__all__ = [
    "IdempotencyConflictError",
    "InvalidAmountError",
    "InvalidCurrencyError",
    "InvalidIdempotencyKeyError",
    "InvalidMetadataError",
    "InvalidWebhookTargetError",
    "PaymentGatewayUnavailableError",
    "PaymentNotFinalizedError",
    "PaymentNotFoundError",
    "PaymentNotVisibleError",
    "PaymentProcessingError",
    "PaymentsDomainError",
    "WebhookDeliveryError",
]
