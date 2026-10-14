"""Error hierarchy of the shared kernel."""

from shared.exceptions.base import AppError
from shared.exceptions.domain import DomainError
from shared.exceptions.infra import InfraError

__all__ = ["AppError", "DomainError", "InfraError"]
