"""Payment gateway port."""

from __future__ import annotations

from typing import Protocol
from uuid import UUID

from payments.application.dto.charge_result import ChargeResult
from payments.domain.entities import Payment


class PaymentGatewayProtocol(Protocol):
    """The only door to the outside payment world."""

    async def charge(self, payment: Payment, *, idempotency_key: UUID) -> ChargeResult:
        """Charge the payment.

        The idempotency key is our own payment id, so a retried call returns the
        same decision instead of charging the customer twice. A technical failure
        (timeout, 5xx, unreachable gateway) is raised as a domain error and is
        retryable; a declined charge is a successful call with `approved=False`.
        """
        ...


__all__ = ["PaymentGatewayProtocol"]
