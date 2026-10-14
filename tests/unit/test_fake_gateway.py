"""Behaviour of the emulated payment gateway."""

from __future__ import annotations

from uuid import UUID

import pytest

from payments.domain.exceptions import PaymentGatewayUnavailableError
from payments.infrastructure.gateway.fake_gateway import (
    DECLINE_REASON,
    FakePaymentGateway,
)
from tests.unit.fakes import make_payment

PAYMENT_ID = UUID(int=1)


def _gateway(**overrides: object) -> FakePaymentGateway:
    defaults: dict[str, object] = {
        "success_rate": 1.0,
        "min_delay_ms": 0,
        "max_delay_ms": 0,
        "seed": 7,
    }
    defaults.update(overrides)
    return FakePaymentGateway(**defaults)  # type: ignore[arg-type]


async def test_a_successful_charge_is_approved() -> None:
    gateway = _gateway(success_rate=1.0)

    result = await gateway.charge(make_payment(), idempotency_key=PAYMENT_ID)

    assert result.approved is True
    assert result.reason is None


async def test_a_declined_charge_is_a_business_result_not_an_error() -> None:
    gateway = _gateway(success_rate=0.0)

    result = await gateway.charge(make_payment(), idempotency_key=PAYMENT_ID)

    assert result.approved is False
    assert result.reason == DECLINE_REASON


async def test_a_technical_failure_is_raised_for_the_retry_policy() -> None:
    gateway = _gateway(technical_failure_rate=1.0)

    with pytest.raises(PaymentGatewayUnavailableError) as exc_info:
        await gateway.charge(make_payment(), idempotency_key=PAYMENT_ID)

    assert exc_info.value.payment_id == PAYMENT_ID


async def test_the_same_seed_produces_the_same_sequence() -> None:
    first = _gateway(success_rate=0.5)
    second = _gateway(success_rate=0.5)

    first_results = [
        await first.charge(make_payment(), idempotency_key=PAYMENT_ID) for _ in range(5)
    ]
    second_results = [
        await second.charge(make_payment(), idempotency_key=PAYMENT_ID) for _ in range(5)
    ]

    assert [result.approved for result in first_results] == [
        result.approved for result in second_results
    ]


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"success_rate": 1.5}, "success_rate"),
        ({"technical_failure_rate": -0.1}, "technical_failure_rate"),
        ({"min_delay_ms": 100, "max_delay_ms": 10}, "delay bounds"),
    ],
)
def test_invalid_configuration_is_rejected(overrides: dict[str, object], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        _gateway(**overrides)
