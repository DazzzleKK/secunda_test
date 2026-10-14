"""System clock and identifier generator."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

from payments.application.ports.clock import ClockProtocol
from payments.application.ports.id_generator import IdGeneratorProtocol


class SystemClock(ClockProtocol):
    """Clock backed by the system time, always in UTC."""

    def now(self) -> datetime:
        """Return the current instant as a timezone-aware UTC datetime."""
        return datetime.now(UTC)


class UuidGenerator(IdGeneratorProtocol):
    """Identifier generator backed by uuid4."""

    def new_id(self) -> UUID:
        """Return a new random identifier."""
        return uuid4()


__all__ = ["SystemClock", "UuidGenerator"]
