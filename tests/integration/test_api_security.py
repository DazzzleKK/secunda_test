"""Authentication of the business endpoints."""

from __future__ import annotations

from uuid import uuid4

import pytest

from payments.infrastructure.db.models import PaymentModel
from tests.integration.helpers import api_headers, count_rows, payment_body

pytestmark = pytest.mark.integration

PAYMENTS_URL = "/api/v1/payments"


async def test_post_without_api_key_returns_401(api_client) -> None:
    response = await api_client.post(
        PAYMENTS_URL,
        json=payment_body(),
        headers={"Idempotency-Key": "key-auth"},
    )

    assert response.status_code == 401
    assert response.json()["code"] == "unauthorized"


async def test_post_with_a_wrong_api_key_returns_401(api_client) -> None:
    response = await api_client.post(
        PAYMENTS_URL,
        json=payment_body(),
        headers=api_headers("key-auth", api_key="not-the-key"),
    )

    assert response.status_code == 401
    assert response.json()["code"] == "unauthorized"


async def test_get_without_api_key_returns_401(api_client) -> None:
    response = await api_client.get(f"{PAYMENTS_URL}/{uuid4()}")

    assert response.status_code == 401


async def test_a_rejected_request_creates_nothing(api_client, session) -> None:
    await api_client.post(
        PAYMENTS_URL,
        json=payment_body(),
        headers={"Idempotency-Key": "key-auth"},
    )

    assert await count_rows(session, PaymentModel) == 0
