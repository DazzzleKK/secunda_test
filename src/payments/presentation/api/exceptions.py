"""Transport-level errors raised by the HTTP layer itself.

They follow the same hierarchy as domain errors (`AppError`), so one handler maps
every failure to the `ErrorEnvelope`; the codes here describe the request, not the
payment.
"""

from __future__ import annotations

from shared.exceptions import AppError


class UnauthorizedError(AppError):
    """The `X-API-Key` header is missing or does not match the configured key."""

    code = "unauthorized"
    default_message = "Invalid or missing API key"


class MissingIdempotencyKeyError(AppError):
    """The mandatory `Idempotency-Key` header is absent."""

    code = "missing_idempotency_key"
    default_message = "Idempotency-Key header is required"


__all__ = ["MissingIdempotencyKeyError", "UnauthorizedError"]
