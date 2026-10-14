"""Deliver webhook use case on fake ports."""

from __future__ import annotations

from uuid import UUID

import pytest

from payments.application.dto.payment_created_event import PaymentCreatedEvent
from payments.application.use_cases.deliver_webhook import DeliverWebhookUseCase
from payments.domain.entities import Payment
from payments.domain.exceptions import (
    PaymentNotFinalizedError,
    PaymentNotFoundError,
    WebhookDeliveryError,
)
from tests.unit.fakes import (
    FIXED_NOW,
    FakeClock,
    FakeStore,
    FakeUnitOfWorkFactory,
    FakeWebhookSender,
    make_payment,
    seed_payment,
)

PAYMENT_ID = UUID(int=4)
EVENT_ID = UUID(int=77)


def an_event(payment_id: UUID = PAYMENT_ID, attempt: int = 0) -> PaymentCreatedEvent:
    """Build a `payment.created` message for the test payment."""
    return PaymentCreatedEvent(event_id=EVENT_ID, payment_id=payment_id, attempt=attempt)


def a_succeeded_payment() -> Payment:
    """Build a payment that already reached SUCCEEDED."""
    payment = make_payment(payment_id=PAYMENT_ID, amount="55.00")
    payment.mark_succeeded(FIXED_NOW)
    return payment


async def test_deliver_webhook_sends_the_payload_and_records_the_attempt(
    store: FakeStore,
    clock: FakeClock,
    uow_factory: FakeUnitOfWorkFactory,
) -> None:
    payment = seed_payment(store, a_succeeded_payment())
    sender = FakeWebhookSender(delivered=True, status_code=200)
    use_case = DeliverWebhookUseCase(uow_factory, sender, clock)

    result = await use_case.execute(an_event())

    assert result.delivered is True
    assert len(sender.calls) == 1
    target, payload, attempt = sender.calls[0]
    assert target.url == payment.webhook_url
    assert payload.event_id == EVENT_ID
    assert payload.event == "payment.created"
    assert payload.payment_id == PAYMENT_ID
    assert payload.status == "succeeded"
    assert payload.amount == "55.00"
    assert payload.currency == "RUB"
    assert payload.occurred_at == FIXED_NOW
    assert attempt == 1

    stored = store.payments[PAYMENT_ID]
    assert stored.webhook_attempts == 1
    assert stored.webhook_delivered_at == clock.now()
    assert stored.webhook_last_error is None
    assert uow_factory.commit_count == 1


async def test_deliver_webhook_raises_for_a_payment_without_a_final_status(
    store: FakeStore,
    clock: FakeClock,
    uow_factory: FakeUnitOfWorkFactory,
) -> None:
    seed_payment(store, make_payment(payment_id=PAYMENT_ID))
    sender = FakeWebhookSender()
    use_case = DeliverWebhookUseCase(uow_factory, sender, clock)

    with pytest.raises(PaymentNotFinalizedError) as exc_info:
        await use_case.execute(an_event())

    assert exc_info.value.payment_id == PAYMENT_ID
    assert exc_info.value.code == "payment_not_finalized"
    assert sender.calls == []


async def test_deliver_webhook_records_a_failed_attempt_and_raises(
    store: FakeStore,
    clock: FakeClock,
    uow_factory: FakeUnitOfWorkFactory,
) -> None:
    seed_payment(store, a_succeeded_payment())
    sender = FakeWebhookSender(delivered=False, status_code=500, error="HTTP 500")
    use_case = DeliverWebhookUseCase(uow_factory, sender, clock)

    with pytest.raises(WebhookDeliveryError) as exc_info:
        await use_case.execute(an_event())

    assert exc_info.value.code == "webhook_delivery_failed"
    assert exc_info.value.attempts == 1
    stored = store.payments[PAYMENT_ID]
    assert stored.webhook_attempts == 1
    assert stored.webhook_last_error == "HTTP 500"
    assert stored.webhook_delivered_at is None


async def test_deliver_webhook_counts_the_attempt_of_the_message(
    store: FakeStore,
    clock: FakeClock,
    uow_factory: FakeUnitOfWorkFactory,
) -> None:
    seed_payment(store, a_succeeded_payment())
    sender = FakeWebhookSender(delivered=False, status_code=500, error="HTTP 500")
    use_case = DeliverWebhookUseCase(uow_factory, sender, clock)

    with pytest.raises(WebhookDeliveryError) as exc_info:
        await use_case.execute(an_event(attempt=2))

    assert sender.calls[0][2] == 3
    assert exc_info.value.attempts == 3


async def test_deliver_webhook_skips_a_payment_that_was_already_delivered(
    store: FakeStore,
    clock: FakeClock,
    uow_factory: FakeUnitOfWorkFactory,
) -> None:
    payment = a_succeeded_payment()
    payment.webhook_delivered_at = FIXED_NOW
    payment.webhook_attempts = 1
    seed_payment(store, payment)
    sender = FakeWebhookSender()
    use_case = DeliverWebhookUseCase(uow_factory, sender, clock)

    result = await use_case.execute(an_event())

    assert result.delivered is True
    assert sender.calls == []
    assert store.payments[PAYMENT_ID].webhook_attempts == 1
    assert uow_factory.commit_count == 0


async def test_deliver_webhook_raises_not_found_for_an_unknown_payment(
    clock: FakeClock,
    uow_factory: FakeUnitOfWorkFactory,
) -> None:
    sender = FakeWebhookSender()
    use_case = DeliverWebhookUseCase(uow_factory, sender, clock)

    with pytest.raises(PaymentNotFoundError):
        await use_case.execute(an_event(payment_id=UUID(int=404)))

    assert sender.calls == []
