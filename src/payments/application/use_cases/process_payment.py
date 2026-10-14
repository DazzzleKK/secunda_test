"""Process payment use case."""

from __future__ import annotations

from datetime import timedelta
from uuid import UUID

from payments.application.dto.charge_result import ChargeResult
from payments.application.dto.payment_created_event import PaymentCreatedEvent
from payments.application.dto.process_payment_result import ProcessPaymentResult
from payments.application.ports.clock import ClockProtocol
from payments.application.ports.payment_gateway import PaymentGatewayProtocol
from payments.application.ports.unit_of_work import UnitOfWorkFactoryProtocol
from payments.domain.entities import Payment
from payments.domain.enums import PaymentStatus
from payments.domain.exceptions import (
    PaymentGatewayUnavailableError,
    PaymentNotFoundError,
    PaymentProcessingError,
)
from payments.domain.value_objects import PaymentOutcome

DECLINED_REASON = "gateway declined the payment"
CHARGE_LEASE = timedelta(minutes=5)
"""How long a claim on a payment is trusted.

Longer than any realistic gateway call, short enough that a payment whose charging
started and whose outcome was never stored becomes chargeable again instead of
staying pending forever.
"""


class ProcessPaymentUseCase:
    """Turn a `payment.created` message into a terminal payment status.

    The gateway call happens between two short transactions: the emulated charge
    takes seconds, and holding a transaction (let alone a row lock) for that long
    would pin a database connection for nothing.

    Before charging, the payment is claimed with one conditional `UPDATE`. This is
    what keeps two copies of the same event from both calling the gateway: the
    guarded `apply_outcome` alone protects the status, not the money.
    """

    def __init__(
        self,
        uow_factory: UnitOfWorkFactoryProtocol,
        gateway: PaymentGatewayProtocol,
        clock: ClockProtocol,
    ) -> None:
        self._uow_factory = uow_factory
        self._gateway = gateway
        self._clock = clock

    async def execute(self, event: PaymentCreatedEvent) -> ProcessPaymentResult:
        """Charge the payment and store the outcome.

        Raises:
            PaymentNotFoundError: the message references an unknown payment.
            PaymentProcessingError: the gateway failed technically (retryable).
        """
        payment = await self._load(event.payment_id)
        if payment.is_final:
            return ProcessPaymentResult(
                payment_id=payment.id,
                status=payment.status,
                applied=False,
            )

        if not await self._claim(payment.id):
            # Another worker is charging this payment right now, or finished just
            # before this copy arrived. Doing nothing is the correct answer.
            return ProcessPaymentResult(
                payment_id=payment.id,
                status=payment.status,
                applied=False,
            )

        charge = await self._charge(payment)
        outcome = PaymentOutcome(
            status=PaymentStatus.SUCCEEDED if charge.approved else PaymentStatus.FAILED,
            reason=None if charge.approved else charge.reason or DECLINED_REASON,
        )

        async with self._uow_factory() as uow:
            applied = await uow.payments.apply_outcome(payment.id, outcome)
            stored = await uow.payments.get(payment.id)
            await uow.commit()

        # `stored` reflects the winner of a concurrent transition as well.
        status = stored.status if stored is not None else outcome.status
        return ProcessPaymentResult(payment_id=payment.id, status=status, applied=applied)

    async def _load(self, payment_id: UUID) -> Payment:
        async with self._uow_factory() as uow:
            payment = await uow.payments.get(payment_id)
        if payment is None:
            raise PaymentNotFoundError(payment_id)
        return payment

    async def _claim(self, payment_id: UUID) -> bool:
        """Take the payment for charging; False when someone else holds it."""
        async with self._uow_factory() as uow:
            claimed = await uow.payments.claim_for_charging(
                payment_id,
                now=self._clock.now(),
                lease=CHARGE_LEASE,
            )
            if claimed:
                await uow.commit()
        return claimed

    async def _charge(self, payment: Payment) -> ChargeResult:
        try:
            return await self._gateway.charge(payment, idempotency_key=payment.id)
        except PaymentGatewayUnavailableError as exc:
            raise PaymentProcessingError(
                "payment gateway is unavailable", payment_id=payment.id
            ) from exc


__all__ = ["CHARGE_LEASE", "DECLINED_REASON", "ProcessPaymentUseCase"]
