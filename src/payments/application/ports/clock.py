"""Clock port."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol


class ClockProtocol(Protocol):
    """Source of the current time (UTC)."""

    def now(self) -> datetime:
        """Return the current moment."""
        ...


__all__ = ["ClockProtocol"]
