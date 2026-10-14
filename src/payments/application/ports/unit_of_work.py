"""Transactional boundary port."""

from __future__ import annotations

from typing import Protocol, Self

from payments.application.ports.outbox_repository import OutboxRepositoryProtocol
from payments.application.ports.payment_repository import PaymentRepositoryProtocol


class UnitOfWorkProtocol(Protocol):
    """A transaction scope exposing the repositories.

    Leaving the scope without `commit()` rolls the transaction back.
    """

    payments: PaymentRepositoryProtocol
    outbox: OutboxRepositoryProtocol

    async def __aenter__(self) -> Self:
        """Open a transaction."""
        ...

    async def __aexit__(self, *exc_info: object) -> None:
        """Close the transaction, rolling back unless it was committed."""
        ...

    async def commit(self) -> None:
        """Commit every change made inside the scope."""
        ...

    async def rollback(self) -> None:
        """Discard every change made inside the scope."""
        ...


class UnitOfWorkFactoryProtocol(Protocol):
    """Opens a fresh unit of work (a new transaction) on every call."""

    def __call__(self) -> UnitOfWorkProtocol:
        """Return a new, not yet entered, unit of work."""
        ...


__all__ = ["UnitOfWorkFactoryProtocol", "UnitOfWorkProtocol"]
