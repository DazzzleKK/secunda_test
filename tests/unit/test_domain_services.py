"""Stability of the request fingerprint."""

from __future__ import annotations

import re
from collections.abc import Mapping
from decimal import Decimal
from typing import Any

import pytest

from payments.domain.enums import Currency
from payments.domain.services import build_request_fingerprint
from payments.domain.value_objects import Money

MONEY = Money(amount=Decimal("100.50"), currency=Currency.RUB)
DESCRIPTION = "Payment for order 42"
METADATA: Mapping[str, Any] = {"order_id": 42, "customer": "acme"}
WEBHOOK_URL = "https://merchant.example.com/hooks/payments"

BASE_FINGERPRINT = build_request_fingerprint(MONEY, DESCRIPTION, METADATA, WEBHOOK_URL)


def test_fingerprint_is_a_sha256_hex_digest() -> None:
    assert re.fullmatch(r"[0-9a-f]{64}", BASE_FINGERPRINT)


def test_fingerprint_is_stable_for_equal_requests() -> None:
    again = build_request_fingerprint(MONEY, DESCRIPTION, dict(METADATA), WEBHOOK_URL)

    assert again == BASE_FINGERPRINT


def test_fingerprint_ignores_metadata_key_order() -> None:
    reordered = build_request_fingerprint(
        MONEY, DESCRIPTION, {"customer": "acme", "order_id": 42}, WEBHOOK_URL
    )

    assert reordered == BASE_FINGERPRINT


def test_fingerprint_ignores_nested_metadata_key_order() -> None:
    first = build_request_fingerprint(MONEY, DESCRIPTION, {"outer": {"b": 2, "a": 1}}, WEBHOOK_URL)
    second = build_request_fingerprint(MONEY, DESCRIPTION, {"outer": {"a": 1, "b": 2}}, WEBHOOK_URL)

    assert first == second


@pytest.mark.parametrize(
    ("money", "description", "metadata", "webhook_url"),
    [
        (
            Money(amount=Decimal("100.51"), currency=Currency.RUB),
            DESCRIPTION,
            METADATA,
            WEBHOOK_URL,
        ),
        (
            Money(amount=Decimal("100.50"), currency=Currency.USD),
            DESCRIPTION,
            METADATA,
            WEBHOOK_URL,
        ),
        (MONEY, "Another description", METADATA, WEBHOOK_URL),
        (MONEY, DESCRIPTION, {"order_id": 43, "customer": "acme"}, WEBHOOK_URL),
        (MONEY, DESCRIPTION, METADATA, "https://other.example.com/hooks"),
    ],
)
def test_fingerprint_changes_when_a_request_field_changes(
    money: Money,
    description: str,
    metadata: Mapping[str, Any],
    webhook_url: str,
) -> None:
    changed = build_request_fingerprint(money, description, metadata, webhook_url)

    assert changed != BASE_FINGERPRINT
