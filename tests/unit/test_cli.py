"""The operator CLI speaks to the management API correctly."""

from __future__ import annotations

import json
from typing import Any

import httpx

from payments.cli import peek_messages, publish_back
from payments.infrastructure.messaging.topology import (
    ATTEMPT_HEADER,
    EXCHANGE_NAME,
    WORK_ROUTING_KEY,
)

MANAGEMENT_URL = "http://management.test"


def _client(handler) -> httpx.Client:  # type: ignore[no-untyped-def]
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_peek_keeps_the_messages_in_the_queue() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(200, json=[{"payload": "{}", "properties": {"headers": {}}}])

    messages = peek_messages(
        _client(handler), MANAGEMENT_URL, "payments.dlq", limit=5, remove=False
    )

    assert captured["ackmode"] == "ack_requeue_true"
    assert captured["count"] == 5
    assert len(messages) == 1


def test_replay_removes_the_messages_from_the_queue() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(200, json=[])

    peek_messages(_client(handler), MANAGEMENT_URL, "payments.dlq", limit=1, remove=True)

    assert captured["ackmode"] == "ack_requeue_false"


def test_replay_publishes_with_a_fresh_attempt_counter() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured.update(json.loads(request.content))
        return httpx.Response(200, json={"routed": True})

    routed = publish_back(_client(handler), MANAGEMENT_URL, body='{"payment_id": "x"}')

    assert routed is True
    assert captured["routing_key"] == WORK_ROUTING_KEY
    assert captured["properties"]["headers"][ATTEMPT_HEADER] == 0
    assert captured["properties"]["delivery_mode"] == 2
    assert captured["payload_encoding"] == "string"
    assert "/api/exchanges/" in captured["url"]
    assert EXCHANGE_NAME in captured["url"]


def test_an_unrouted_message_is_reported() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"routed": False})

    assert publish_back(_client(handler), MANAGEMENT_URL, body="{}") is False
