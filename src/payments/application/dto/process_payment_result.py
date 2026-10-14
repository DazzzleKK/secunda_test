"""Output of the process payment use case."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from payments.domain.enums import PaymentStatus


@dataclass(frozen=True, slots=True)
class ProcessPaymentResult:
    """What happened to the payment during processing.

    `applied` is False when the payment was already final, i.e. the message was a
    duplicate and the status was not touched.
    """

    payment_id: UUID
    status: PaymentStatus
    applied: bool


__all__ = ["ProcessPaymentResult"]
