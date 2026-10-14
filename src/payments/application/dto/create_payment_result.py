"""Output of the create payment use case."""

from __future__ import annotations

from dataclasses import dataclass

from payments.application.dto.payment_dto import PaymentDto


@dataclass(frozen=True, slots=True)
class CreatePaymentResult:
    """The created (or replayed) payment."""

    payment: PaymentDto
    replayed: bool


__all__ = ["CreatePaymentResult"]
