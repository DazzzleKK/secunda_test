"""Builders and small helpers shared by the integration tests."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

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
    "build_outbox_event",
    "build_payment",
    "count_rows",
    "truncate_all",
]
