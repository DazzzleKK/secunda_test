"""Request and response bodies of the HTTP API.

Schemas live in the presentation layer only: they validate the *shape* of the
request and never replace domain rules (the domain still owns money, URL and
metadata invariants).
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from payments.application.dto.payment_dto import PaymentDto
from payments.domain.enums import Currency, PaymentStatus

MAX_DESCRIPTION_LENGTH = 1024
MAX_AMOUNT_LENGTH = 64


class PaymentCreateRequest(BaseModel):
    """Body of `POST /api/v1/payments`.

    Money travels as a **string**: a JSON number would go through a float before
    reaching the domain and could change the value (e.g. 100000000000000.01 becomes
    100000000000000.02). The domain value object still owns the real invariants —
    positive, exactly two fraction digits, at most 18 digits.
    """

    model_config = ConfigDict(extra="forbid")

    amount: str = Field(min_length=1, max_length=MAX_AMOUNT_LENGTH, examples=["100.50"])
    currency: Currency
    description: str = Field(default="", max_length=MAX_DESCRIPTION_LENGTH)
    metadata: dict[str, Any] = Field(default_factory=dict)
    webhook_url: str = Field(min_length=1)

    @field_validator("amount", mode="after")
    @classmethod
    def _must_be_a_decimal_string(cls, value: str) -> str:
        """Reject anything that is not a decimal string (numbers included)."""
        try:
            Decimal(value)
        except InvalidOperation as exc:
            raise ValueError('amount must be a decimal string, for example "100.50"') from exc
        return value


class PaymentCreateResponse(BaseModel):
    """Body of a `202 Accepted` answer."""

    payment_id: UUID
    status: PaymentStatus
    created_at: datetime

    @classmethod
    def from_dto(cls, payment: PaymentDto) -> PaymentCreateResponse:
        """Build the response from a payment snapshot."""
        return cls(
            payment_id=payment.payment_id,
            status=payment.status,
            created_at=payment.created_at,
        )


class PaymentResponse(BaseModel):
    """Full payment representation."""

    payment_id: UUID
    amount: str
    currency: Currency
    description: str
    metadata: dict[str, Any]
    status: PaymentStatus
    webhook_url: str
    created_at: datetime
    updated_at: datetime
    processed_at: datetime | None
    failure_reason: str | None
    webhook_attempts: int
    webhook_delivered_at: datetime | None
    webhook_last_error: str | None

    @classmethod
    def from_dto(cls, payment: PaymentDto) -> PaymentResponse:
        """Build the response from a payment snapshot."""
        return cls(
            payment_id=payment.payment_id,
            amount=payment.amount,
            currency=payment.currency,
            description=payment.description,
            metadata=dict(payment.metadata),
            status=payment.status,
            webhook_url=payment.webhook_url,
            created_at=payment.created_at,
            updated_at=payment.updated_at,
            processed_at=payment.processed_at,
            failure_reason=payment.failure_reason,
            webhook_attempts=payment.webhook_attempts,
            webhook_delivered_at=payment.webhook_delivered_at,
            webhook_last_error=payment.webhook_last_error,
        )


class HealthResponse(BaseModel):
    """Liveness answer."""

    status: str


class ReadinessResponse(BaseModel):
    """Readiness answer; `database` tells which dependency is not ready."""

    status: str
    database: str


class ErrorEnvelope(BaseModel):
    """Single error format of the service (see the brief, section 7)."""

    code: str
    message: str
    details: dict[str, Any] | None = None


__all__ = [
    "ErrorEnvelope",
    "HealthResponse",
    "PaymentCreateRequest",
    "PaymentCreateResponse",
    "PaymentResponse",
    "ReadinessResponse",
]
