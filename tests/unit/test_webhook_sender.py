"""Delivery and signing of webhooks."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import httpx

from payments.application.dto.webhook_payload import WebhookPayload
from payments.domain.value_objects import WebhookTarget
from payments.infrastructure.webhook.http_sender import (
    HttpWebhookSender,
    payload_to_body,
)
from payments.infrastructure.webhook.signer import (
    ATTEMPT_HEADER,
    REQUEST_ID_HEADER,
    SIGNATURE_HEADER,
    sign_payload,
    verify_signature,
)

SECRET = "super-secret"
TARGET = WebhookTarget("https://merchant.test/hooks/payments")
PAYLOAD = WebhookPayload(
    event_id=UUID(int=9),
    event="payment.created",
    payment_id=UUID(int=1),
    status="succeeded",
    amount="100.50",
    currency="RUB",
    occurred_at=datetime(2026, 1, 1, 12, 0, tzinfo=UTC),
)

Handler = Callable[[httpx.Request], httpx.Response]


def _sender(handler: Handler, *, secret: str = SECRET) -> HttpWebhookSender:
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return HttpWebhookSender(client, secret=secret, timeout_seconds=1.0)


def _capturing_handler(captured: dict[str, Any]) -> Handler:
    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = request.content
        captured["headers"] = dict(request.headers)
        return httpx.Response(200)

    return handler


async def test_a_2xx_answer_is_a_delivered_webhook() -> None:
    sender = _sender(lambda request: httpx.Response(204))

    result = await sender.send(TARGET, PAYLOAD, attempt=1)

    assert result.delivered is True
    assert result.status_code == 204
    assert result.error is None


async def test_an_error_status_is_reported_without_raising() -> None:
    sender = _sender(lambda request: httpx.Response(500))

    result = await sender.send(TARGET, PAYLOAD, attempt=2)

    assert result.delivered is False
    assert result.status_code == 500
    assert result.error == "HTTP 500"


async def test_a_transport_failure_is_reported_without_raising() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("timed out", request=request)

    sender = _sender(handler)

    result = await sender.send(TARGET, PAYLOAD, attempt=1)

    assert result.delivered is False
    assert result.status_code is None
    assert result.error is not None
    assert "ConnectTimeout" in result.error


async def test_the_request_body_is_signed_and_carries_the_attempt_headers() -> None:
    captured: dict[str, Any] = {}
    sender = _sender(_capturing_handler(captured))

    await sender.send(TARGET, PAYLOAD, attempt=3)

    body = captured["body"]
    headers = captured["headers"]
    signature = headers[SIGNATURE_HEADER.lower()]

    assert body == payload_to_body(PAYLOAD)
    assert signature == sign_payload(SECRET, body)
    assert verify_signature(SECRET, body, signature) is True
    assert headers[REQUEST_ID_HEADER.lower()] == str(PAYLOAD.event_id)
    assert headers[ATTEMPT_HEADER.lower()] == "3"


async def test_a_signature_of_another_secret_does_not_verify() -> None:
    captured: dict[str, Any] = {}
    sender = _sender(_capturing_handler(captured), secret="another-secret")

    await sender.send(TARGET, PAYLOAD, attempt=1)

    body = captured["body"]
    headers = captured["headers"]

    assert verify_signature(SECRET, body, headers[SIGNATURE_HEADER.lower()]) is False


def test_the_serialised_body_is_stable_and_json() -> None:
    import json

    document = json.loads(payload_to_body(PAYLOAD))

    assert document["amount"] == "100.50"
    assert document["event"] == "payment.created"
    assert document["occurred_at"] == "2026-01-01T12:00:00+00:00"
    assert payload_to_body(PAYLOAD) == payload_to_body(PAYLOAD)
