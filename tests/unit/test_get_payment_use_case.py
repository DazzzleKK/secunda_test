"""Get payment use case on fake ports."""

from __future__ import annotations

from uuid import UUID

import pytest

from payments.application.use_cases.get_payment import GetPaymentUseCase
from payments.domain.enums import PaymentStatus
from payments.domain.exceptions import PaymentNotFoundError
from tests.unit.fakes import FakeStore, FakeUnitOfWorkFactory, make_payment, seed_payment


async def test_get_payment_returns_the_stored_snapshot(
    store: FakeStore,
    uow_factory: FakeUnitOfWorkFactory,
) -> None:
    payment = seed_payment(store, make_payment(payment_id=UUID(int=5), amount="12.34"))
    use_case = GetPaymentUseCase(uow_factory)

    dto = await use_case.execute(payment.id)

    assert dto.payment_id == payment.id
    assert dto.amount == "12.34"
    assert dto.currency is payment.money.currency
    assert dto.status is PaymentStatus.PENDING
    assert dto.webhook_url == payment.webhook_url
    assert dto.metadata == dict(payment.metadata)


async def test_get_payment_raises_not_found_for_an_unknown_id(
    uow_factory: FakeUnitOfWorkFactory,
) -> None:
    use_case = GetPaymentUseCase(uow_factory)
    missing_id = UUID(int=404)

    with pytest.raises(PaymentNotFoundError) as exc_info:
        await use_case.execute(missing_id)

    assert exc_info.value.payment_id == missing_id
    assert exc_info.value.code == "payment_not_found"
    assert exc_info.value.details == {"payment_id": str(missing_id)}
