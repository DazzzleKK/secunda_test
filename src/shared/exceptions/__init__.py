"""Error hierarchy of the shared kernel."""

from shared.exceptions.base import AppError
from shared.exceptions.domain import DomainError

__all__ = ["AppError", "DomainError"]
