"""Process payment use case on fake ports."""

from __future__ import annotations

from uuid import UUID

import pytest

from payments.application.dto.payment_created_event import PaymentCreatedEvent
from payments.application.use_cases.process_payment import DECLINED_REASON, ProcessPaymentUseCase
from payments.domain.entities import Payment
from payments.domain.enums import PaymentStatus
from payments.domain.exceptions import (
    PaymentGatewayUnavailableError,
    PaymentNotFoundError,
    PaymentProcessingError,
)
from tests.unit.fakes import (
    FIXED_NOW,
    FakeClock,
    FakePaymentGateway,
    FakeStore,
    FakeUnitOfWorkFactory,
    make_payment,
    seed_payment,
)

PAYMENT_ID = UUID(int=3)
EVENT_ID = UUID(int=90)


def an_event(payment_id: UUID = PAYMENT_ID, attempt: int = 0) -> PaymentCreatedEvent:
    """Build a `payment.created` message for the test payment."""
    return PaymentCreatedEvent(event_id=EVENT_ID, payment_id=payment_id, attempt=attempt)


def a_final_payment() -> Payment:
    """Build a payment that already reached SUCCEEDED."""
    payment = make_payment(payment_id=PAYMENT_ID)
    payment.mark_succeeded(FIXED_NOW)
    return payment


async def test_process_payment_marks_an_approved_payment_succeeded(
    store: FakeStore,
    clock: FakeClock,
    uow_factory: FakeUnitOfWorkFactory,
) -> None:
    seed_payment(store, make_payment(payment_id=PAYMENT_ID))
    gateway = FakePaymentGateway(approved=True)
    use_case = ProcessPaymentUseCase(uow_factory, gateway, clock)

    result = await use_case.execute(an_event())

    assert result.applied is True
    assert result.status is PaymentStatus.SUCCEEDED
    stored = store.payments[PAYMENT_ID]
    assert stored.status is PaymentStatus.SUCCEEDED
    assert stored.processed_at == clock.now()
    assert stored.failure_reason is None
    assert gateway.calls == [(PAYMENT_ID, PAYMENT_ID)]


async def test_process_payment_marks_a_declined_payment_failed_without_raising(
    store: FakeStore,
    clock: FakeClock,
    uow_factory: FakeUnitOfWorkFactory,
) -> None:
    seed_payment(store, make_payment(payment_id=PAYMENT_ID))
    gateway = FakePaymentGateway(approved=False, reason="insufficient funds")
    use_case = ProcessPaymentUseCase(uow_factory, gateway, clock)

    result = await use_case.execute(an_event())

    assert result.applied is True
    assert result.status is PaymentStatus.FAILED
    stored = store.payments[PAYMENT_ID]
    assert stored.status is PaymentStatus.FAILED
    assert stored.failure_reason == "insufficient funds"
    assert stored.processed_at == clock.now()


async def test_process_payment_falls_back_to_a_reason_when_the_gateway_gives_none(
    store: FakeStore,
    clock: FakeClock,
    uow_factory: FakeUnitOfWorkFactory,
) -> None:
    seed_payment(store, make_payment(payment_id=PAYMENT_ID))
    gateway = FakePaymentGateway(approved=False, reason=None)
    use_case = ProcessPaymentUseCase(uow_factory, gateway, clock)

    await use_case.execute(an_event())

    assert store.payments[PAYMENT_ID].failure_reason == DECLINED_REASON


async def test_process_payment_is_a_no_op_for_an_already_final_payment(
    store: FakeStore,
    clock: FakeClock,
    uow_factory: FakeUnitOfWorkFactory,
) -> None:
    seed_payment(store, a_final_payment())
    gateway = FakePaymentGateway()
    use_case = ProcessPaymentUseCase(uow_factory, gateway, clock)

    result = await use_case.execute(an_event())

    assert result.applied is False
    assert result.status is PaymentStatus.SUCCEEDED
    assert gateway.calls == []
    assert uow_factory.commit_count == 0


async def test_process_payment_keeps_the_payment_pending_when_the_gateway_is_unavailable(
    store: FakeStore,
    clock: FakeClock,
    uow_factory: FakeUnitOfWorkFactory,
) -> None:
    seed_payment(store, make_payment(payment_id=PAYMENT_ID))
    gateway = FakePaymentGateway(unavailable=True)
    use_case = ProcessPaymentUseCase(uow_factory, gateway, clock)

    with pytest.raises(PaymentProcessingError) as exc_info:
        await use_case.execute(an_event())

    assert isinstance(exc_info.value.__cause__, PaymentGatewayUnavailableError)
    stored = store.payments[PAYMENT_ID]
    assert stored.status is PaymentStatus.PENDING
    assert stored.processed_at is None
    assert stored.failure_reason is None
    assert uow_factory.commit_count == 0


async def test_process_payment_raises_not_found_for_an_unknown_payment(
    clock: FakeClock,
    uow_factory: FakeUnitOfWorkFactory,
) -> None:
    gateway = FakePaymentGateway()
    use_case = ProcessPaymentUseCase(uow_factory, gateway, clock)

    with pytest.raises(PaymentNotFoundError) as exc_info:
        await use_case.execute(an_event(payment_id=UUID(int=404)))

    assert exc_info.value.payment_id == UUID(int=404)
    assert gateway.calls == []
