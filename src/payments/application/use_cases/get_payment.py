"""Get payment use case."""

from __future__ import annotations

from uuid import UUID

from payments.application.dto.payment_dto import PaymentDto
from payments.application.ports.unit_of_work import UnitOfWorkFactoryProtocol
from payments.domain.exceptions import PaymentNotFoundError


class GetPaymentUseCase:
    """Read one payment by id."""

    def __init__(self, uow_factory: UnitOfWorkFactoryProtocol) -> None:
        self._uow_factory = uow_factory

    async def execute(self, payment_id: UUID) -> PaymentDto:
        """Return the payment snapshot.

        Raises:
            PaymentNotFoundError: no payment with this id exists.
        """
        async with self._uow_factory() as uow:
            payment = await uow.payments.get(payment_id)
        if payment is None:
            raise PaymentNotFoundError(payment_id)
        return PaymentDto.from_entity(payment)


__all__ = ["GetPaymentUseCase"]
