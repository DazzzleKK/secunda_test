"""HTTP contract of the payment endpoints."""

from __future__ import annotations

from uuid import uuid4

import pytest

from payments.infrastructure.db.models import OutboxModel, PaymentModel
from tests.integration.helpers import (
    api_headers,
    count_rows,
    payment_body,
)

pytestmark = pytest.mark.integration

PAYMENTS_URL = "/api/v1/payments"


async def test_create_payment_answers_202_and_writes_the_outbox_event(api_client, session) -> None:
    response = await api_client.post(
        PAYMENTS_URL, json=payment_body(), headers=api_headers("key-1")
    )

    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "pending"
    assert body["created_at"]
    assert response.headers["Location"] == f"{PAYMENTS_URL}/{body['payment_id']}"

    assert await count_rows(session, PaymentModel) == 1
    assert await count_rows(session, OutboxModel) == 1


async def test_repeating_the_request_returns_the_stored_payment(api_client, session) -> None:
    first = await api_client.post(PAYMENTS_URL, json=payment_body(), headers=api_headers("key-2"))
    second = await api_client.post(PAYMENTS_URL, json=payment_body(), headers=api_headers("key-2"))

    assert first.status_code == 202
    assert second.status_code == 202
    assert second.json()["payment_id"] == first.json()["payment_id"]
    assert second.headers["Idempotency-Replayed"] == "true"
    assert await count_rows(session, PaymentModel) == 1
    assert await count_rows(session, OutboxModel) == 1


async def test_the_same_key_with_another_body_conflicts(api_client) -> None:
    await api_client.post(PAYMENTS_URL, json=payment_body(), headers=api_headers("key-3"))

    response = await api_client.post(
        PAYMENTS_URL,
        json=payment_body(amount="200.00"),
        headers=api_headers("key-3"),
    )

    assert response.status_code == 409
    assert response.json()["code"] == "idempotency_conflict"


async def test_missing_idempotency_key_is_rejected(api_client) -> None:
    response = await api_client.post(PAYMENTS_URL, json=payment_body(), headers=api_headers(None))

    assert response.status_code == 400
    assert response.json()["code"] == "missing_idempotency_key"


async def test_get_returns_the_stored_payment(api_client) -> None:
    created = await api_client.post(PAYMENTS_URL, json=payment_body(), headers=api_headers("key-4"))
    payment_id = created.json()["payment_id"]

    response = await api_client.get(f"{PAYMENTS_URL}/{payment_id}", headers=api_headers())

    assert response.status_code == 200
    body = response.json()
    assert body["payment_id"] == payment_id
    assert body["amount"] == "100.50"
    assert body["currency"] == "RUB"
    assert body["status"] == "pending"
    assert body["metadata"] == {"order_id": "42"}
    assert body["webhook_attempts"] == 0
    assert body["processed_at"] is None
    assert body["webhook_delivered_at"] is None


async def test_get_of_an_unknown_payment_returns_404(api_client) -> None:
    response = await api_client.get(f"{PAYMENTS_URL}/{uuid4()}", headers=api_headers())

    assert response.status_code == 404
    assert response.json()["code"] == "payment_not_found"


async def test_get_with_a_malformed_id_returns_422(api_client) -> None:
    response = await api_client.get(f"{PAYMENTS_URL}/not-a-uuid", headers=api_headers())

    assert response.status_code == 422
    assert response.json()["code"] == "validation_error"


@pytest.mark.parametrize("amount", ["0.00", "-5.00", "10.555"])
async def test_invalid_amounts_are_rejected(api_client, amount: str) -> None:
    """Money invariants belong to the domain, so the code is `invalid_amount`."""
    response = await api_client.post(
        PAYMENTS_URL,
        json=payment_body(amount=amount),
        headers=api_headers("key-amount"),
    )

    assert response.status_code == 422
    assert response.json()["code"] == "invalid_amount"


async def test_a_numeric_amount_is_rejected(api_client) -> None:
    """Money must travel as a string: a JSON number goes through a float."""
    body = payment_body() | {"amount": 100.5}

    response = await api_client.post(PAYMENTS_URL, json=body, headers=api_headers("key-number"))

    assert response.status_code == 422
    assert response.json()["code"] == "validation_error"


async def test_a_large_amount_keeps_its_exact_value(api_client) -> None:
    created = await api_client.post(
        PAYMENTS_URL,
        json=payment_body(amount="100000000000000.01"),
        headers=api_headers("key-exact"),
    )

    assert created.status_code == 202
    payment_id = created.json()["payment_id"]

    stored = await api_client.get(f"{PAYMENTS_URL}/{payment_id}", headers=api_headers())

    assert stored.json()["amount"] == "100000000000000.01"


async def test_amount_with_one_fraction_digit_is_normalised(api_client) -> None:
    created = await api_client.post(
        PAYMENTS_URL, json=payment_body(amount="10.5"), headers=api_headers("key-scale")
    )

    assert created.status_code == 202
    payment_id = created.json()["payment_id"]

    stored = await api_client.get(f"{PAYMENTS_URL}/{payment_id}", headers=api_headers())

    assert stored.json()["amount"] == "10.50"


@pytest.mark.parametrize("currency", ["GBP", "rub"])
async def test_unsupported_currency_is_rejected(api_client, currency: str) -> None:
    response = await api_client.post(
        PAYMENTS_URL,
        json=payment_body(currency=currency),
        headers=api_headers("key-currency"),
    )

    assert response.status_code == 422


async def test_unsupported_webhook_scheme_is_rejected(api_client) -> None:
    response = await api_client.post(
        PAYMENTS_URL,
        json=payment_body(webhook_url="ftp://example.test/hook"),
        headers=api_headers("key-url"),
    )

    assert response.status_code == 422
    assert response.json()["code"] == "invalid_webhook_url"


async def test_unknown_body_fields_are_rejected(api_client) -> None:
    body = payment_body() | {"ammount": "100.50"}

    response = await api_client.post(PAYMENTS_URL, json=body, headers=api_headers("key-extra"))

    assert response.status_code == 422
    assert response.json()["code"] == "validation_error"


async def test_openapi_documents_headers_and_error_responses(api_client) -> None:
    document = (await api_client.get("/openapi.json")).json()
    operation = document["paths"][PAYMENTS_URL]["post"]
    schemas = document["components"]["schemas"]

    parameter_names = {parameter["name"] for parameter in operation["parameters"]}

    assert {"X-API-Key", "Idempotency-Key"} <= parameter_names
    assert {"400", "401", "409", "422", "503"} <= set(operation["responses"])
    assert schemas["PaymentCreateRequest"]["properties"]["amount"]["type"] == "string"
