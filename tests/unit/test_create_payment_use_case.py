"""Create payment use case on fake ports."""

from __future__ import annotations

from uuid import UUID

import pytest

from payments.application.use_cases.create_payment import CreatePaymentUseCase
from payments.domain.enums import Currency, EventType, PaymentStatus
from payments.domain.exceptions import (
    IdempotencyConflictError,
    InvalidAmountError,
    PaymentProcessingError,
    PaymentsDomainError,
)
from payments.domain.services import build_request_fingerprint
from tests.unit.fakes import (
    DEFAULT_IDEMPOTENCY_KEY,
    FakeClock,
    FakeIdGenerator,
    FakeStore,
    FakeUnitOfWorkFactory,
    ReadBlindness,
    make_command,
    make_payment,
    seed_payment,
)


async def test_create_payment_stores_the_payment_and_its_event_in_one_commit(
    store: FakeStore,
    clock: FakeClock,
    ids: FakeIdGenerator,
    uow_factory: FakeUnitOfWorkFactory,
) -> None:
    use_case = CreatePaymentUseCase(uow_factory, clock, ids)

    result = await use_case.execute(make_command())

    assert result.replayed is False
    assert result.payment.status is PaymentStatus.PENDING
    assert result.payment.amount == "100.50"
    assert result.payment.currency is Currency.RUB
    assert result.payment.created_at == clock.now()

    assert len(store.payments) == 1
    stored = store.payments[result.payment.payment_id]
    assert stored.status is PaymentStatus.PENDING
    assert stored.idempotency_key == DEFAULT_IDEMPOTENCY_KEY
    assert stored.processed_at is None
    assert stored.webhook_attempts == 0
    assert stored.webhook_delivered_at is None
    assert stored.request_fingerprint == build_request_fingerprint(
        stored.money, stored.description, stored.metadata, stored.webhook_url
    )

    events = list(store.outbox.values())
    assert len(events) == 1
    assert events[0].event_type is EventType.PAYMENT_CREATED
    assert events[0].routing_key == "payments.new"
    assert events[0].aggregate_id == stored.id
    assert events[0].published_at is None

    assert uow_factory.commit_count == 1


async def test_create_payment_replays_the_stored_payment_for_the_same_payload(
    store: FakeStore,
    clock: FakeClock,
    ids: FakeIdGenerator,
    uow_factory: FakeUnitOfWorkFactory,
) -> None:
    stored = seed_payment(store, make_payment(payment_id=UUID(int=11)))
    use_case = CreatePaymentUseCase(uow_factory, clock, ids)

    result = await use_case.execute(make_command())

    assert result.replayed is True
    assert result.payment.payment_id == stored.id
    assert len(store.payments) == 1
    assert store.outbox == {}
    assert uow_factory.commit_count == 0


async def test_create_payment_raises_conflict_when_the_key_is_reused_with_another_payload(
    store: FakeStore,
    clock: FakeClock,
    ids: FakeIdGenerator,
    uow_factory: FakeUnitOfWorkFactory,
) -> None:
    seed_payment(store, make_payment(payment_id=UUID(int=11)))
    use_case = CreatePaymentUseCase(uow_factory, clock, ids)

    with pytest.raises(IdempotencyConflictError) as exc_info:
        await use_case.execute(make_command(amount="200.00"))

    assert exc_info.value.idempotency_key == DEFAULT_IDEMPOTENCY_KEY
    assert len(store.payments) == 1
    assert store.outbox == {}


async def test_create_payment_replays_when_the_insert_hits_the_unique_index(
    store: FakeStore,
    clock: FakeClock,
    ids: FakeIdGenerator,
) -> None:
    stored = seed_payment(store, make_payment(payment_id=UUID(int=11)))
    uow_factory = FakeUnitOfWorkFactory(store, clock, blindness=ReadBlindness(remaining=1))
    use_case = CreatePaymentUseCase(uow_factory, clock, ids)

    result = await use_case.execute(make_command())

    assert result.replayed is True
    assert result.payment.payment_id == stored.id
    assert len(store.payments) == 1
    assert store.outbox == {}
    assert uow_factory.created == 2
    assert uow_factory.commit_count == 0


async def test_create_payment_raises_conflict_when_the_insert_hits_the_unique_index(
    store: FakeStore,
    clock: FakeClock,
    ids: FakeIdGenerator,
) -> None:
    seed_payment(store, make_payment(payment_id=UUID(int=11)))
    uow_factory = FakeUnitOfWorkFactory(store, clock, blindness=ReadBlindness(remaining=1))
    use_case = CreatePaymentUseCase(uow_factory, clock, ids)

    with pytest.raises(IdempotencyConflictError):
        await use_case.execute(make_command(amount="200.00"))

    assert len(store.payments) == 1
    assert store.outbox == {}


async def test_create_payment_reports_a_technical_error_when_the_winner_is_not_readable(
    store: FakeStore,
    clock: FakeClock,
    ids: FakeIdGenerator,
) -> None:
    seed_payment(store, make_payment(payment_id=UUID(int=11)))
    uow_factory = FakeUnitOfWorkFactory(store, clock, blindness=ReadBlindness(remaining=2))
    use_case = CreatePaymentUseCase(uow_factory, clock, ids)

    with pytest.raises(PaymentProcessingError) as exc_info:
        await use_case.execute(make_command())

    assert len(store.payments) == 1
    assert store.outbox == {}
    assert exc_info.value.code == "payment_processing_failed"


@pytest.mark.parametrize("amount", ["0.00", "-5.00", "10.5"])
async def test_create_payment_rejects_an_invalid_amount(
    amount: str,
    store: FakeStore,
    clock: FakeClock,
    ids: FakeIdGenerator,
    uow_factory: FakeUnitOfWorkFactory,
) -> None:
    use_case = CreatePaymentUseCase(uow_factory, clock, ids)

    with pytest.raises(InvalidAmountError):
        await use_case.execute(make_command(amount=amount))

    assert store.payments == {}
    assert uow_factory.created == 0


async def test_create_payment_rejects_an_empty_idempotency_key(
    store: FakeStore,
    clock: FakeClock,
    ids: FakeIdGenerator,
    uow_factory: FakeUnitOfWorkFactory,
) -> None:
    use_case = CreatePaymentUseCase(uow_factory, clock, ids)

    with pytest.raises(PaymentsDomainError):
        await use_case.execute(make_command(idempotency_key=""))

    assert store.payments == {}


async def test_create_payment_rejects_a_webhook_url_that_is_not_http(
    store: FakeStore,
    clock: FakeClock,
    ids: FakeIdGenerator,
    uow_factory: FakeUnitOfWorkFactory,
) -> None:
    use_case = CreatePaymentUseCase(uow_factory, clock, ids)

    with pytest.raises(PaymentsDomainError):
        await use_case.execute(make_command(webhook_url="ftp://merchant.example.com/hooks"))

    assert store.payments == {}
