"""SQLAlchemy implementations of the persistence ports.

Every vendor exception is translated here, so nothing above this layer sees
`IntegrityError` or `SQLAlchemyError`.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any, NoReturn, cast
from uuid import UUID

from sqlalchemy import Table, func, select, update
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from payments.application.dto.webhook_delivery_result import WebhookDeliveryResult
from payments.application.ports.outbox_repository import OutboxRepositoryProtocol
from payments.application.ports.payment_repository import PaymentRepositoryProtocol
from payments.domain.entities import OutboxEvent, Payment
from payments.domain.enums import PaymentStatus
from payments.domain.exceptions import InvalidAmountError
from payments.domain.value_objects import PaymentOutcome
from payments.infrastructure.db.mappers import (
    outbox_to_entity,
    outbox_to_row,
    payment_to_entity,
    payment_to_row,
)
from payments.infrastructure.db.models import OutboxModel, PaymentModel
from shared.exceptions import InfraError

DEFAULT_MAX_PUBLISH_ATTEMPTS = 5
"""After this many failed publications an event is parked instead of retried.

Parked rows stay in the table with their `last_error`, so the outbox lag metric
keeps growing and the incident is visible instead of looping forever.
"""

MAX_ERROR_LENGTH = 500
"""Upper bound of the error text stored in the outbox."""


def _constraint_name(exc: IntegrityError) -> str | None:
    """Extract the violated constraint name from a wrapped driver error."""
    original = getattr(exc, "orig", None)
    candidates = (original, getattr(original, "diag", None), getattr(original, "__cause__", None))
    for candidate in candidates:
        name = getattr(candidate, "constraint_name", None)
        if isinstance(name, str):
            return name
    return None


def _raise_translated_integrity_error(exc: IntegrityError) -> NoReturn:
    """Turn a constraint violation into a domain or infrastructure error."""
    constraint = _constraint_name(exc)
    if constraint is not None and "amount_positive" in constraint:
        raise InvalidAmountError(
            amount=constraint,
            message="Amount violates the money invariant",
        ) from exc
    raise InfraError(f"Database integrity error ({constraint or 'unknown constraint'})") from exc


@asynccontextmanager
async def _translated(operation: str) -> AsyncIterator[None]:
    """Translate vendor database errors raised inside the block."""
    try:
        yield
    except IntegrityError as exc:
        _raise_translated_integrity_error(exc)
    except SQLAlchemyError as exc:
        raise InfraError(f"Database error during {operation}") from exc


class SqlAlchemyPaymentRepository(PaymentRepositoryProtocol):
    """Payment storage backed by PostgreSQL."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add_if_absent(self, payment: Payment) -> bool:
        """Insert the row unless the idempotency key is already taken.

        The statement is built against `PaymentModel.__table__` on purpose: an
        ORM-enabled insert resolves the `metadata` key as `Base.metadata`.
        """
        statement = (
            postgres_insert(cast(Table, PaymentModel.__table__))
            .values(**payment_to_row(payment))
            .on_conflict_do_nothing(index_elements=[PaymentModel.__table__.c.idempotency_key])
            .returning(PaymentModel.__table__.c.id)
        )
        async with _translated("payments.add_if_absent"):
            result = await self._session.execute(statement)
        return result.scalar_one_or_none() is not None

    async def get(self, payment_id: UUID) -> Payment | None:
        """Return the payment with this id, or None."""
        statement = select(PaymentModel).where(PaymentModel.id == payment_id)
        async with _translated("payments.get"):
            result = await self._session.execute(statement)
        model = result.scalar_one_or_none()
        return None if model is None else payment_to_entity(model)

    async def get_by_idempotency_key(self, key: str) -> Payment | None:
        """Return the payment stored under this idempotency key, or None."""
        statement = select(PaymentModel).where(PaymentModel.idempotency_key == key)
        async with _translated("payments.get_by_idempotency_key"):
            result = await self._session.execute(statement)
        model = result.scalar_one_or_none()
        return None if model is None else payment_to_entity(model)

    async def apply_outcome(self, payment_id: UUID, outcome: PaymentOutcome) -> bool:
        """Apply a terminal outcome with a guarded update."""
        statement = (
            update(PaymentModel)
            .where(
                PaymentModel.id == payment_id,
                PaymentModel.status == PaymentStatus.PENDING.value,
            )
            .values(
                status=outcome.status.value,
                failure_reason=outcome.reason,
                processed_at=func.now(),
                updated_at=func.now(),
            )
            .returning(PaymentModel.id)
        )
        async with _translated("payments.apply_outcome"):
            result = await self._session.execute(statement)
        return result.scalar_one_or_none() is not None

    async def register_webhook_attempt(
        self,
        payment_id: UUID,
        result: WebhookDeliveryResult,
        *,
        now: datetime,
    ) -> None:
        """Record a webhook attempt; the delivery timestamp is set only once."""
        values: dict[str, Any] = {
            "webhook_attempts": PaymentModel.webhook_attempts + 1,
            "webhook_last_error": result.error,
            "updated_at": now,
        }
        if result.delivered:
            values["webhook_delivered_at"] = func.coalesce(PaymentModel.webhook_delivered_at, now)
        statement = update(PaymentModel).where(PaymentModel.id == payment_id).values(**values)
        async with _translated("payments.register_webhook_attempt"):
            await self._session.execute(statement)


class SqlAlchemyOutboxRepository(OutboxRepositoryProtocol):
    """Outbox storage backed by PostgreSQL."""

    def __init__(
        self,
        session: AsyncSession,
        *,
        max_publish_attempts: int = DEFAULT_MAX_PUBLISH_ATTEMPTS,
    ) -> None:
        self._session = session
        self._max_publish_attempts = max_publish_attempts

    async def add(self, event: OutboxEvent) -> None:
        """Append an event to the outbox inside the current transaction."""
        statement = postgres_insert(cast(Table, OutboxModel.__table__)).values(
            **outbox_to_row(event)
        )
        async with _translated("outbox.add"):
            await self._session.execute(statement)

    async def fetch_unpublished(self, limit: int) -> Sequence[OutboxEvent]:
        """Lock and return the oldest unpublished events that are still retryable."""
        statement = (
            select(OutboxModel)
            .where(
                OutboxModel.published_at.is_(None),
                OutboxModel.attempts < self._max_publish_attempts,
            )
            .order_by(OutboxModel.created_at, OutboxModel.id)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        async with _translated("outbox.fetch_unpublished"):
            result = await self._session.execute(statement)
        return [outbox_to_entity(model) for model in result.scalars().all()]

    async def mark_published(self, event_id: UUID, *, now: datetime) -> None:
        """Stamp `published_at` after the broker confirmed the message."""
        statement = (
            update(OutboxModel)
            .where(OutboxModel.id == event_id)
            .values(
                published_at=now,
                attempts=OutboxModel.attempts + 1,
                last_error=None,
            )
        )
        async with _translated("outbox.mark_published"):
            await self._session.execute(statement)

    async def register_failure(
        self,
        event_id: UUID,
        error: str,
        *,
        permanent: bool = True,
    ) -> None:
        """Record a publication failure without losing the row.

        The text is truncated (broker errors can embed the whole message body) and
        only a permanent failure moves the event towards parking.
        """
        values: dict[str, Any] = {"last_error": error[:MAX_ERROR_LENGTH]}
        if permanent:
            values["attempts"] = OutboxModel.attempts + 1
        statement = update(OutboxModel).where(OutboxModel.id == event_id).values(**values)
        async with _translated("outbox.register_failure"):
            await self._session.execute(statement)

    async def oldest_unpublished_age(self) -> timedelta | None:
        """Return the age of the oldest unpublished event, or None.

        Parked events (too many failed publications) are included on purpose: a
        growing metric is the signal that a human has to look at the outbox.
        """
        statement = select(func.min(OutboxModel.created_at)).where(
            OutboxModel.published_at.is_(None)
        )
        async with _translated("outbox.oldest_unpublished_age"):
            result = await self._session.execute(statement)
        oldest = result.scalar_one_or_none()
        if oldest is None:
            return None
        return datetime.now(UTC) - oldest


__all__ = ["SqlAlchemyOutboxRepository", "SqlAlchemyPaymentRepository"]
