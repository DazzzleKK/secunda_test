"""Transactional boundary on top of an SQLAlchemy session."""

from __future__ import annotations

from types import TracebackType
from typing import Self

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from payments.application.ports.outbox_repository import OutboxRepositoryProtocol
from payments.application.ports.payment_repository import PaymentRepositoryProtocol
from payments.application.ports.unit_of_work import (
    UnitOfWorkFactoryProtocol,
    UnitOfWorkProtocol,
)
from payments.infrastructure.db.repositories import (
    SqlAlchemyOutboxRepository,
    SqlAlchemyPaymentRepository,
)


class SqlAlchemyUnitOfWork(UnitOfWorkProtocol):
    """One database transaction exposing the repositories.

    Leaving the scope without `commit()` rolls the transaction back, so a use case
    that lost the idempotency race simply drops its work.
    """

    payments: PaymentRepositoryProtocol
    outbox: OutboxRepositoryProtocol

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory
        self._session: AsyncSession | None = None
        self._committed = False

    @property
    def session(self) -> AsyncSession:
        """The session bound to this transaction."""
        if self._session is None:
            raise RuntimeError("Unit of work is used outside of its context manager")
        return self._session

    async def __aenter__(self) -> Self:
        """Open a session and build the repositories on top of it."""
        session = self._session_factory()
        self._session = session
        self._committed = False
        self.payments = SqlAlchemyPaymentRepository(session)
        self.outbox = SqlAlchemyOutboxRepository(session)
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Roll back uncommitted work and always release the session."""
        session = self._session
        self._session = None
        if session is None:
            return
        try:
            if not self._committed:
                await session.rollback()
        finally:
            await session.close()

    async def commit(self) -> None:
        """Commit every change made inside the scope."""
        await self.session.commit()
        self._committed = True

    async def rollback(self) -> None:
        """Discard every change made inside the scope."""
        await self.session.rollback()
        self._committed = False


class SqlAlchemyUnitOfWorkFactory(UnitOfWorkFactoryProtocol):
    """Opens a fresh unit of work (a new session) on every call."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    def __call__(self) -> UnitOfWorkProtocol:
        """Return a new, not yet entered, unit of work."""
        return SqlAlchemyUnitOfWork(self._session_factory)


__all__ = ["SqlAlchemyUnitOfWork", "SqlAlchemyUnitOfWorkFactory"]
