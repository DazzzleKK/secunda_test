"""Invariants of the payments value objects."""

from __future__ import annotations

import json
from dataclasses import FrozenInstanceError
from decimal import Decimal

import pytest

from payments.domain.enums import Currency, PaymentStatus
from payments.domain.exceptions import (
    InvalidAmountError,
    InvalidCurrencyError,
    InvalidIdempotencyKeyError,
    InvalidWebhookTargetError,
    PaymentsDomainError,
)
from payments.domain.value_objects import (
    MAX_IDEMPOTENCY_KEY_LENGTH,
    IdempotencyKey,
    Money,
    PaymentOutcome,
    WebhookTarget,
)


def test_money_accepts_positive_amount_with_two_fraction_digits() -> None:
    money = Money(amount=Decimal("100.50"), currency=Currency.RUB)

    assert money.as_string() == "100.50"


def test_money_keeps_two_fraction_digits_for_whole_amounts() -> None:
    money = Money(amount=Decimal("10.00"), currency=Currency.USD)

    assert money.as_string() == "10.00"


@pytest.mark.parametrize("amount", [Decimal("0.00"), Decimal("-1.00")])
def test_money_rejects_non_positive_amount(amount: Decimal) -> None:
    with pytest.raises(InvalidAmountError):
        Money(amount=amount, currency=Currency.RUB)


@pytest.mark.parametrize(
    ("amount", "expected"),
    [
        (Decimal("10.5"), "10.50"),
        (Decimal("10"), "10.00"),
        (Decimal("10.500"), "10.50"),
    ],
)
def test_money_normalises_amounts_with_fewer_fraction_digits(
    amount: Decimal, expected: str
) -> None:
    money = Money(amount=amount, currency=Currency.RUB)

    assert money.as_string() == expected


@pytest.mark.parametrize("amount", [Decimal("10.555"), Decimal("0.001")])
def test_money_rejects_amount_with_too_many_fraction_digits(amount: Decimal) -> None:
    with pytest.raises(InvalidAmountError):
        Money(amount=amount, currency=Currency.RUB)


@pytest.mark.parametrize("amount", [Decimal("NaN"), Decimal("Infinity")])
def test_money_rejects_non_finite_amount(amount: Decimal) -> None:
    with pytest.raises(InvalidAmountError):
        Money(amount=amount, currency=Currency.RUB)


def test_money_rejects_non_decimal_amount() -> None:
    with pytest.raises(InvalidAmountError):
        Money(amount="100.50", currency=Currency.RUB)  # type: ignore[arg-type]


def test_money_rejects_unsupported_currency() -> None:
    with pytest.raises(PaymentsDomainError):
        Money(amount=Decimal("1.00"), currency="GBP")  # type: ignore[arg-type]


def test_money_is_immutable() -> None:
    money = Money(amount=Decimal("100.50"), currency=Currency.RUB)

    with pytest.raises(FrozenInstanceError):
        money.amount = Decimal("200.00")  # type: ignore[misc]


@pytest.mark.parametrize(
    "url",
    ["https://example.com/hooks", "http://example.com:8080/hooks?source=merchant"],
)
def test_webhook_target_accepts_absolute_http_urls(url: str) -> None:
    assert WebhookTarget(url).url == url


@pytest.mark.parametrize(
    "url",
    ["ftp://example.com/hooks", "example.com/hooks", "https://", "mailto:ops@example.com"],
)
def test_webhook_target_rejects_url_without_http_scheme_or_host(url: str) -> None:
    with pytest.raises(PaymentsDomainError):
        WebhookTarget(url)


@pytest.mark.parametrize("key", ["k", "k" * MAX_IDEMPOTENCY_KEY_LENGTH])
def test_idempotency_key_accepts_boundary_lengths(key: str) -> None:
    assert IdempotencyKey(key).value == key


@pytest.mark.parametrize("key", ["", "k" * (MAX_IDEMPOTENCY_KEY_LENGTH + 1)])
def test_idempotency_key_rejects_length_outside_the_range(key: str) -> None:
    with pytest.raises(PaymentsDomainError):
        IdempotencyKey(key)


def test_idempotency_key_rejects_non_string_value() -> None:
    with pytest.raises(PaymentsDomainError):
        IdempotencyKey(42)  # type: ignore[arg-type]


def test_payment_outcome_accepts_succeeded_without_reason() -> None:
    outcome = PaymentOutcome(status=PaymentStatus.SUCCEEDED)

    assert outcome.reason is None


def test_payment_outcome_accepts_failed_with_reason() -> None:
    outcome = PaymentOutcome(status=PaymentStatus.FAILED, reason="insufficient funds")

    assert outcome.reason == "insufficient funds"


def test_payment_outcome_rejects_pending_status() -> None:
    with pytest.raises(PaymentsDomainError):
        PaymentOutcome(status=PaymentStatus.PENDING)


def test_payment_outcome_requires_reason_for_failed_status() -> None:
    with pytest.raises(PaymentsDomainError):
        PaymentOutcome(status=PaymentStatus.FAILED)


def test_amount_with_more_than_eighteen_digits_is_rejected() -> None:
    with pytest.raises(InvalidAmountError):
        Money(amount=Decimal("12345678901234567.00"), currency=Currency.RUB)


def test_amount_at_the_precision_limit_is_accepted() -> None:
    money = Money(amount=Decimal("1234567890123456.00"), currency=Currency.RUB)

    assert money.as_string() == "1234567890123456.00"


def test_unsupported_currency_raises_a_dedicated_error() -> None:
    with pytest.raises(InvalidCurrencyError):
        Money(amount=Decimal("10.00"), currency="GBP")  # type: ignore[arg-type]


def test_invalid_webhook_target_does_not_echo_the_url() -> None:
    secret_url = "ftp://user:secret@internal.example.com/hook"

    with pytest.raises(InvalidWebhookTargetError) as exc_info:
        WebhookTarget(secret_url)

    assert exc_info.value.details == {"scheme": "ftp", "has_host": True}
    assert "secret" not in json.dumps(exc_info.value.details)
    assert secret_url not in str(exc_info.value)


def test_invalid_webhook_target_reports_a_missing_host() -> None:
    with pytest.raises(InvalidWebhookTargetError) as exc_info:
        WebhookTarget("https:///no-host")

    assert exc_info.value.details == {"scheme": "https", "has_host": False}


def test_idempotency_key_reports_only_its_length() -> None:
    with pytest.raises(InvalidIdempotencyKeyError) as exc_info:
        IdempotencyKey("x" * (MAX_IDEMPOTENCY_KEY_LENGTH + 1))

    assert exc_info.value.details == {"idempotency_key_length": MAX_IDEMPOTENCY_KEY_LENGTH + 1}
    assert "x" not in str(exc_info.value)
