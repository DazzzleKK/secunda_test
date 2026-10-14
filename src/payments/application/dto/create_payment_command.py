"""Input of the create payment use case."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from payments.domain.enums import Currency


@dataclass(frozen=True, slots=True)
class CreatePaymentCommand:
    """A validated-by-contract request to create a payment."""

    amount: Decimal
    currency: Currency
    description: str
    metadata: Mapping[str, Any]
    webhook_url: str
    idempotency_key: str


__all__ = ["CreatePaymentCommand"]
