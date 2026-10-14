"""Base class for errors caused by a violated domain rule."""

from __future__ import annotations

from typing import ClassVar

from shared.exceptions.base import AppError


class DomainError(AppError):
    """Base class of every business rule violation."""

    code: ClassVar[str] = "domain_error"
    default_message: ClassVar[str] = "Domain rule violated"
