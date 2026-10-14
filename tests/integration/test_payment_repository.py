"""Integration tests of the payment repository against a real PostgreSQL."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.exc import IntegrityError

from payments.application.dto.webhook_delivery_result import WebhookDeliveryResult
from payments.domain.enums import Currency, PaymentStatus
from payments.domain.exceptions import InvalidAmountError
from payments.domain.value_objects import PaymentOutcome
from payments.infrastructure.db.mappers import payment_to_row
from payments.infrastructure.db.models import PaymentModel
from payments.infrastructure.db.repositories import _raise_translated_integrity_error
from tests.integration.helpers import FIXED_NOW, build_payment, count_rows

pytestmark = pytest.mark.integration

CLAIM_LEASE = timedelta(minutes=5)


async def test_add_if_absent_stores_the_payment(uow_factory, session) -> None:
    payment = build_payment()

    async with uow_factory() as uow:
        inserted = await uow.payments.add_if_absent(payment)
        await uow.commit()

    assert inserted is True
    assert await count_rows(session, PaymentModel) == 1


async def test_add_if_absent_returns_false_for_a_duplicate_key(uow_factory, session) -> None:
    first = build_payment(idempotency_key="same-key")
    second = build_payment(idempotency_key="same-key")

    async with uow_factory() as uow:
        assert await uow.payments.add_if_absent(first) is True
        await uow.commit()

    async with uow_factory() as uow:
        assert await uow.payments.add_if_absent(second) is False
        await uow.commit()

    assert await count_rows(session, PaymentModel) == 1


async def test_get_round_trips_every_field(uow_factory) -> None:
    payment = build_payment(
        amount="7.00",
        currency=Currency.EUR,
        metadata={"order_id": "42", "nested": {"attempt": 1}},
    )

    async with uow_factory() as uow:
        await uow.payments.add_if_absent(payment)
        await uow.commit()

    async with uow_factory() as uow:
        stored = await uow.payments.get(payment.id)

    assert stored is not None
    assert stored.id == payment.id
    assert stored.money.as_string() == "7.00"
    assert stored.money.currency is Currency.EUR
    assert stored.status is PaymentStatus.PENDING
    assert stored.metadata == {"order_id": "42", "nested": {"attempt": 1}}
    assert stored.idempotency_key == payment.idempotency_key
    assert stored.request_fingerprint == payment.request_fingerprint
    assert stored.webhook_url == payment.webhook_url
    assert stored.created_at == FIXED_NOW
    assert stored.webhook_attempts == 0


async def test_get_returns_none_for_an_unknown_id(uow_factory) -> None:
    async with uow_factory() as uow:
        assert await uow.payments.get(build_payment().id) is None


async def test_get_by_idempotency_key_finds_the_stored_payment(uow_factory) -> None:
    payment = build_payment(idempotency_key="lookup-me")

    async with uow_factory() as uow:
        await uow.payments.add_if_absent(payment)
        await uow.commit()

    async with uow_factory() as uow:
        found = await uow.payments.get_by_idempotency_key("lookup-me")
        missing = await uow.payments.get_by_idempotency_key("never-used")

    assert found is not None
    assert found.id == payment.id
    assert missing is None


async def test_apply_outcome_moves_the_payment_to_succeeded(uow_factory) -> None:
    payment = build_payment()

    async with uow_factory() as uow:
        await uow.payments.add_if_absent(payment)
        await uow.commit()

    async with uow_factory() as uow:
        applied = await uow.payments.apply_outcome(
            payment.id, PaymentOutcome(status=PaymentStatus.SUCCEEDED)
        )
        await uow.commit()

    async with uow_factory() as uow:
        stored = await uow.payments.get(payment.id)

    assert applied is True
    assert stored is not None
    assert stored.status is PaymentStatus.SUCCEEDED
    assert stored.processed_at is not None
    assert stored.failure_reason is None


async def test_apply_outcome_is_ignored_when_the_payment_is_final(uow_factory) -> None:
    payment = build_payment()

    async with uow_factory() as uow:
        await uow.payments.add_if_absent(payment)
        await uow.commit()

    async with uow_factory() as uow:
        await uow.payments.apply_outcome(payment.id, PaymentOutcome(status=PaymentStatus.SUCCEEDED))
        await uow.commit()

    async with uow_factory() as uow:
        first = await uow.payments.get(payment.id)

    async with uow_factory() as uow:
        applied_again = await uow.payments.apply_outcome(
            payment.id,
            PaymentOutcome(status=PaymentStatus.FAILED, reason="gateway declined"),
        )
        await uow.commit()

    async with uow_factory() as uow:
        second = await uow.payments.get(payment.id)

    assert first is not None and second is not None
    assert applied_again is False
    assert second.status is PaymentStatus.SUCCEEDED
    assert second.failure_reason is None
    assert second.processed_at == first.processed_at


async def test_register_webhook_attempt_records_a_failure(uow_factory) -> None:
    payment = build_payment()
    result = WebhookDeliveryResult(delivered=False, status_code=500, error="HTTP 500")

    async with uow_factory() as uow:
        await uow.payments.add_if_absent(payment)
        await uow.commit()

    async with uow_factory() as uow:
        await uow.payments.register_webhook_attempt(payment.id, result, now=FIXED_NOW)
        await uow.commit()

    async with uow_factory() as uow:
        stored = await uow.payments.get(payment.id)

    assert stored is not None
    assert stored.webhook_attempts == 1
    assert stored.webhook_delivered_at is None
    assert stored.webhook_last_error == "HTTP 500"


async def test_register_webhook_attempt_keeps_the_first_delivery_time(uow_factory) -> None:
    payment = build_payment()
    delivered = WebhookDeliveryResult(delivered=True, status_code=200)
    later = FIXED_NOW + timedelta(minutes=5)

    async with uow_factory() as uow:
        await uow.payments.add_if_absent(payment)
        await uow.commit()

    async with uow_factory() as uow:
        await uow.payments.register_webhook_attempt(payment.id, delivered, now=FIXED_NOW)
        await uow.commit()

    async with uow_factory() as uow:
        await uow.payments.register_webhook_attempt(payment.id, delivered, now=later)
        await uow.commit()

    async with uow_factory() as uow:
        stored = await uow.payments.get(payment.id)

    assert stored is not None
    assert stored.webhook_attempts == 2
    assert stored.webhook_delivered_at == FIXED_NOW


async def test_claim_for_charging_is_exclusive(uow_factory) -> None:
    payment = build_payment()

    async with uow_factory() as uow:
        await uow.payments.add_if_absent(payment)
        await uow.commit()

    async with uow_factory() as uow:
        first = await uow.payments.claim_for_charging(payment.id, now=FIXED_NOW, lease=CLAIM_LEASE)
        await uow.commit()

    async with uow_factory() as uow:
        second = await uow.payments.claim_for_charging(
            payment.id, now=FIXED_NOW + timedelta(seconds=1), lease=CLAIM_LEASE
        )
        await uow.commit()

    assert first is True
    assert second is False


async def test_a_claim_expires_after_its_lease(uow_factory) -> None:
    payment = build_payment()

    async with uow_factory() as uow:
        await uow.payments.add_if_absent(payment)
        await uow.commit()

    async with uow_factory() as uow:
        await uow.payments.claim_for_charging(payment.id, now=FIXED_NOW, lease=CLAIM_LEASE)
        await uow.commit()

    async with uow_factory() as uow:
        reclaimed = await uow.payments.claim_for_charging(
            payment.id, now=FIXED_NOW + CLAIM_LEASE + timedelta(seconds=1), lease=CLAIM_LEASE
        )
        await uow.commit()

    assert reclaimed is True


async def test_a_final_payment_cannot_be_claimed(uow_factory) -> None:
    payment = build_payment()

    async with uow_factory() as uow:
        await uow.payments.add_if_absent(payment)
        await uow.payments.apply_outcome(payment.id, PaymentOutcome(status=PaymentStatus.SUCCEEDED))
        await uow.commit()

    async with uow_factory() as uow:
        claimed = await uow.payments.claim_for_charging(
            payment.id, now=FIXED_NOW, lease=CLAIM_LEASE
        )
        await uow.commit()

    assert claimed is False


async def test_amount_check_constraint_is_translated_to_a_domain_error(session) -> None:
    values = payment_to_row(build_payment()) | {"amount": Decimal("-1.00")}

    with pytest.raises(IntegrityError) as error:
        await session.execute(postgres_insert(PaymentModel.__table__).values(**values))

    with pytest.raises(InvalidAmountError):
        _raise_translated_integrity_error(error.value)
