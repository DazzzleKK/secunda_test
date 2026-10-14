"""Builders and small helpers shared by the integration tests."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import httpx
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from payments.domain.entities import OutboxEvent, Payment
from payments.domain.enums import Currency
from payments.domain.value_objects import IdempotencyKey, Money, WebhookTarget

FIXED_NOW = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)


def build_payment(
    *,
    idempotency_key: str = "idem-1",
    amount: str = "100.50",
    currency: Currency = Currency.RUB,
    description: str = "integration test payment",
    metadata: dict[str, Any] | None = None,
    webhook_url: str = "https://example.test/hooks/payments",
    payment_id: UUID | None = None,
    now: datetime = FIXED_NOW,
) -> Payment:
    """Build a pending payment through the domain factory."""
    return Payment.create(
        payment_id=payment_id or uuid4(),
        money=Money(amount=Decimal(amount), currency=currency),
        description=description,
        metadata=metadata if metadata is not None else {"order_id": "42"},
        idempotency_key=IdempotencyKey(idempotency_key),
        webhook_target=WebhookTarget(webhook_url),
        now=now,
    )


def build_outbox_event(payment: Payment, *, now: datetime = FIXED_NOW) -> OutboxEvent:
    """Build the `payment.created` event of a payment."""
    return OutboxEvent.payment_created(payment, now=now)


TEST_API_KEY = "test-api-key"
RABBITMQ_USER = "guest"
RABBITMQ_PASSWORD = "guest"
WEBHOOK_SECRET = "integration-signing-secret"
RABBITMQ_READY_TIMEOUT_SECONDS = 90.0
POLL_INTERVAL_SECONDS = 0.2


@dataclass(frozen=True, slots=True)
class RabbitMq:
    """Endpoints of the broker started for the integration tests."""

    amqp_url: str
    management_url: str


def wait_for_rabbitmq(
    management_url: str, *, timeout: float = RABBITMQ_READY_TIMEOUT_SECONDS
) -> None:
    """Block until the management API of the broker answers."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            response = httpx.get(
                f"{management_url}/api/overview",
                auth=(RABBITMQ_USER, RABBITMQ_PASSWORD),
                timeout=2.0,
                trust_env=False,
            )
        except httpx.HTTPError:
            time.sleep(0.5)
            continue
        if response.status_code == 200:
            return
        time.sleep(0.5)
    raise TimeoutError("RabbitMQ management API did not become ready")


def queue_document(management_url: str, queue: str) -> dict[str, Any]:
    """Return the management API description of a queue."""
    response = httpx.get(
        f"{management_url}/api/queues/%2F/{queue}",
        auth=(RABBITMQ_USER, RABBITMQ_PASSWORD),
        timeout=5.0,
        trust_env=False,
    )
    response.raise_for_status()
    return dict(response.json())


def queue_messages(management_url: str, queue: str) -> int:
    """Return how many messages a queue currently holds."""
    return int(queue_document(management_url, queue).get("messages", 0))


def queue_bindings(management_url: str, queue: str) -> set[tuple[str, str]]:
    """Return the `(exchange, routing key)` pairs a queue is bound with."""
    response = httpx.get(
        f"{management_url}/api/queues/%2F/{queue}/bindings",
        auth=(RABBITMQ_USER, RABBITMQ_PASSWORD),
        timeout=5.0,
        trust_env=False,
    )
    response.raise_for_status()
    return {
        (str(binding.get("source", "")), str(binding.get("routing_key", "")))
        for binding in response.json()
    }


def exchange_document(management_url: str, exchange: str) -> dict[str, Any]:
    """Return the management API description of an exchange."""
    response = httpx.get(
        f"{management_url}/api/exchanges/%2F/{exchange}",
        auth=(RABBITMQ_USER, RABBITMQ_PASSWORD),
        timeout=5.0,
        trust_env=False,
    )
    response.raise_for_status()
    return dict(response.json())


def fetch_queue_messages(
    management_url: str,
    queue: str,
    *,
    count: int = 10,
) -> list[dict[str, Any]]:
    """Read messages from a queue without removing them (for assertions)."""
    response = httpx.post(
        f"{management_url}/api/queues/%2F/{queue}/get",
        auth=(RABBITMQ_USER, RABBITMQ_PASSWORD),
        json={"count": count, "ackmode": "ack_requeue_true", "encoding": "auto"},
        timeout=5.0,
        trust_env=False,
    )
    response.raise_for_status()
    return list(response.json())


async def wait_for_queue_messages(
    management_url: str,
    queue: str,
    *,
    minimum: int = 1,
    timeout: float = 30.0,
) -> int:
    """Poll a queue until it holds at least `minimum` messages."""
    deadline = time.monotonic() + timeout
    depth = 0
    while time.monotonic() < deadline:
        depth = queue_messages(management_url, queue)
        if depth >= minimum:
            return depth
        await asyncio.sleep(POLL_INTERVAL_SECONDS)
    return depth


def api_headers(
    idempotency_key: str | None = "idem-1",
    *,
    api_key: str = TEST_API_KEY,
) -> dict[str, str]:
    """Headers of an authenticated request; the idempotency key is optional."""
    headers = {"X-API-Key": api_key}
    if idempotency_key is not None:
        headers["Idempotency-Key"] = idempotency_key
    return headers


def payment_body(
    *,
    amount: str = "100.50",
    currency: str = "RUB",
    description: str = "integration test payment",
    metadata: dict[str, Any] | None = None,
    webhook_url: str = "https://example.test/hooks/payments",
) -> dict[str, Any]:
    """A valid body of `POST /api/v1/payments`."""
    return {
        "amount": amount,
        "currency": currency,
        "description": description,
        "metadata": {"order_id": "42"} if metadata is None else metadata,
        "webhook_url": webhook_url,
    }


async def count_rows(session: AsyncSession, model: type[Any]) -> int:
    """Count the rows of a table through a fresh session."""
    result = await session.execute(select(func.count()).select_from(model))
    return int(result.scalar_one())


async def truncate_all(engine: AsyncEngine) -> None:
    """Empty both tables, releasing the rows locked by a previous test."""
    from sqlalchemy import text

    async with engine.begin() as connection:
        await connection.execute(text("TRUNCATE TABLE payments, outbox"))


__all__ = [
    "FIXED_NOW",
    "RABBITMQ_USER",
    "TEST_API_KEY",
    "WEBHOOK_SECRET",
    "RabbitMq",
    "api_headers",
    "build_outbox_event",
    "build_payment",
    "count_rows",
    "exchange_document",
    "fetch_queue_messages",
    "payment_body",
    "queue_bindings",
    "queue_document",
    "queue_messages",
    "truncate_all",
    "wait_for_queue_messages",
    "wait_for_rabbitmq",
]
