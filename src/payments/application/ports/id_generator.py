"""Identifier generator port."""

from __future__ import annotations

from typing import Protocol
from uuid import UUID


class IdGeneratorProtocol(Protocol):
    """Source of new identifiers."""

    def new_id(self) -> UUID:
        """Return a new unique identifier."""
        ...


__all__ = ["IdGeneratorProtocol"]
