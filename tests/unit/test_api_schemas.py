"""The request schema is strict about the shape of money."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from payments.presentation.api.schemas import PaymentCreateRequest


def _body(**overrides: object) -> dict[str, object]:
    body: dict[str, object] = {
        "amount": "100.50",
        "currency": "RUB",
        "description": "test",
        "metadata": {},
        "webhook_url": "https://example.test/hook",
    }
    body.update(overrides)
    return body


def test_a_decimal_string_is_accepted() -> None:
    request = PaymentCreateRequest(**_body())

    assert request.amount == "100.50"


@pytest.mark.parametrize("amount", [100.5, 100, True, ["100.50"], {"value": "100.50"}])
def test_a_json_number_is_rejected(amount: object) -> None:
    with pytest.raises(ValidationError):
        PaymentCreateRequest(**_body(amount=amount))


@pytest.mark.parametrize("amount", ["", "сто", "1,50", "10.50 ₽"])
def test_a_non_decimal_string_is_rejected(amount: str) -> None:
    with pytest.raises(ValidationError):
        PaymentCreateRequest(**_body(amount=amount))


def test_the_schema_of_amount_is_a_string() -> None:
    schema = PaymentCreateRequest.model_json_schema()

    assert schema["properties"]["amount"]["type"] == "string"
