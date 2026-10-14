"""Infrastructure failures that have no domain meaning."""

from __future__ import annotations

from shared.exceptions.base import AppError


class InfraError(AppError):
    """An untranslated infrastructure failure (presentation maps it to 503)."""

    code = "infra_error"
    default_message = "Infrastructure failure"


__all__ = ["InfraError"]
