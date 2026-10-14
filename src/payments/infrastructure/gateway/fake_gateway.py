"""Emulated payment gateway: the only door to the outside payment world."""

from __future__ import annotations

import asyncio
import random
from typing import Final
from uuid import UUID

from payments.application.dto.charge_result import ChargeResult
from payments.application.ports.payment_gateway import PaymentGatewayProtocol
from payments.domain.entities import Payment
from payments.domain.exceptions import PaymentGatewayUnavailableError

DECLINE_REASON: Final[str] = "insufficient funds"
TIMEOUT_REASON: Final[str] = "gateway did not answer in time"
DEFAULT_SUCCESS_RATE: Final[float] = 0.9
DEFAULT_MIN_DELAY_MS: Final[int] = 2000
DEFAULT_MAX_DELAY_MS: Final[int] = 5000
DEFAULT_SEED: Final[int] = 42


class FakePaymentGateway(PaymentGatewayProtocol):
    """Emulates a PSP: 2-5 seconds, 90% of charges approved.

    Latency, success rate and technical failure rate are configurable and the
    random generator is seeded, so tests get a deterministic sequence of outcomes
    instead of real randomness. The decision of an idempotency key is remembered:
    a retried charge returns the same result instead of charging twice. A declined
    charge is returned as a business result; only a technical failure is raised,
    and only that one is retryable.
    """

    def __init__(
        self,
        *,
        success_rate: float = DEFAULT_SUCCESS_RATE,
        min_delay_ms: int = DEFAULT_MIN_DELAY_MS,
        max_delay_ms: int = DEFAULT_MAX_DELAY_MS,
        seed: int | None = DEFAULT_SEED,
        technical_failure_rate: float = 0.0,
    ) -> None:
        if not 0.0 <= success_rate <= 1.0:
            raise ValueError("success_rate must be between 0 and 1")
        if not 0.0 <= technical_failure_rate <= 1.0:
            raise ValueError("technical_failure_rate must be between 0 and 1")
        if min_delay_ms < 0 or max_delay_ms < min_delay_ms:
            raise ValueError("delay bounds must satisfy 0 <= min <= max")
        self._success_rate = success_rate
        self._technical_failure_rate = technical_failure_rate
        self._min_delay_seconds = min_delay_ms / 1000
        self._max_delay_seconds = max_delay_ms / 1000
        self._random = random.Random(seed)
        self._decisions: dict[UUID, ChargeResult] = {}

    async def charge(self, payment: Payment, *, idempotency_key: UUID) -> ChargeResult:
        """Answer with a terminal outcome, the same one for the same idempotency key.

        The real gateway keeps the decision of a key it has already seen, and so does
        this emulator: a redelivered event must not charge the customer twice or turn
        an approved charge into a decline. Only business outcomes are remembered — a
        technical failure stays retryable.
        """
        remembered = self._decisions.get(idempotency_key)
        if remembered is not None:
            return remembered

        await asyncio.sleep(self._random.uniform(self._min_delay_seconds, self._max_delay_seconds))
        if self._random.random() < self._technical_failure_rate:
            raise PaymentGatewayUnavailableError(idempotency_key, TIMEOUT_REASON)
        decision = (
            ChargeResult(approved=True)
            if self._random.random() < self._success_rate
            else ChargeResult(approved=False, reason=DECLINE_REASON)
        )
        self._decisions[idempotency_key] = decision
        return decision


__all__ = ["DECLINE_REASON", "TIMEOUT_REASON", "FakePaymentGateway"]
