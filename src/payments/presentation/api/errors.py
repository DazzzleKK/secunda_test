"""Transport mapping of application errors.

Domain and infrastructure errors carry a stable `code`; this module is the only
place that knows which HTTP status a code gets and how the envelope looks. Error
messages are fixed for the client, `details` is an explicit whitelist filled by the
error itself.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from payments.presentation.api.schemas import ErrorEnvelope
from shared.exceptions import AppError

logger = logging.getLogger(__name__)

DOMAIN_CODE_TO_HTTP: dict[str, int] = {
    # transport
    "unauthorized": status.HTTP_401_UNAUTHORIZED,
    "missing_idempotency_key": status.HTTP_400_BAD_REQUEST,
    # business
    "payment_not_found": status.HTTP_404_NOT_FOUND,
    "idempotency_conflict": status.HTTP_409_CONFLICT,
    "payment_not_visible": status.HTTP_409_CONFLICT,
    "payment_not_finalized": status.HTTP_409_CONFLICT,
    # domain invariants
    "invalid_amount": status.HTTP_422_UNPROCESSABLE_CONTENT,
    "invalid_currency": status.HTTP_422_UNPROCESSABLE_CONTENT,
    "invalid_metadata": status.HTTP_422_UNPROCESSABLE_CONTENT,
    "invalid_webhook_url": status.HTTP_422_UNPROCESSABLE_CONTENT,
    "invalid_idempotency_key": status.HTTP_422_UNPROCESSABLE_CONTENT,
    # retryable technical failures
    "payment_gateway_unavailable": status.HTTP_503_SERVICE_UNAVAILABLE,
    "payment_processing_failed": status.HTTP_503_SERVICE_UNAVAILABLE,
    "webhook_delivery_failed": status.HTTP_503_SERVICE_UNAVAILABLE,
    "infra_error": status.HTTP_503_SERVICE_UNAVAILABLE,
}

DEFAULT_ERROR_STATUS = status.HTTP_500_INTERNAL_SERVER_ERROR
VALIDATION_ERROR_STATUS = status.HTTP_422_UNPROCESSABLE_CONTENT


def _envelope(code: str, message: str, details: dict[str, Any] | None) -> dict[str, Any]:
    """Render the error envelope as JSON-ready data."""
    return ErrorEnvelope(code=code, message=message, details=details or None).model_dump(
        mode="json"
    )


async def app_error_handler(_: Request, exc: AppError) -> JSONResponse:
    """Map any application error to its HTTP status and the envelope."""
    status_code = DOMAIN_CODE_TO_HTTP.get(exc.code, DEFAULT_ERROR_STATUS)
    if status_code >= status.HTTP_500_INTERNAL_SERVER_ERROR:
        logger.error("request failed with %s", exc.code, extra={"details": exc.details})
    return JSONResponse(
        status_code=status_code,
        content=_envelope(exc.code, exc.message, exc.details),
    )


async def validation_error_handler(_: Request, exc: RequestValidationError) -> JSONResponse:
    """Render Pydantic failures in the same envelope."""
    details = [
        {
            "location": [str(part) for part in error.get("loc", ())],
            "message": str(error.get("msg", "")),
            "type": str(error.get("type", "")),
        }
        for error in exc.errors()
    ]
    return JSONResponse(
        status_code=VALIDATION_ERROR_STATUS,
        content=_envelope("validation_error", "Request validation failed", {"errors": details}),
    )


async def unexpected_error_handler(_: Request, exc: Exception) -> JSONResponse:
    """Never leak internals: log the cause and answer with a fixed envelope."""
    logger.exception("unhandled error", exc_info=exc)
    return JSONResponse(
        status_code=DEFAULT_ERROR_STATUS,
        content=_envelope("internal_error", "Internal server error", None),
    )


ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    400: {"model": ErrorEnvelope, "description": "Missing Idempotency-Key header"},
    401: {"model": ErrorEnvelope, "description": "Invalid or missing API key"},
    404: {"model": ErrorEnvelope, "description": "Payment not found"},
    409: {
        "model": ErrorEnvelope,
        "description": "Idempotency conflict, payment not visible or not final yet",
    },
    422: {
        "model": ErrorEnvelope,
        "description": "Request validation or domain invariant failure",
    },
    503: {"model": ErrorEnvelope, "description": "Retryable technical failure"},
}
"""Error answers documented in the OpenAPI schema of every business endpoint."""


def register_exception_handlers(app: FastAPI) -> None:
    """Attach the handlers of the service to the application."""
    app.add_exception_handler(AppError, app_error_handler)  # type: ignore[arg-type]
    app.add_exception_handler(RequestValidationError, validation_error_handler)  # type: ignore[arg-type]
    app.add_exception_handler(Exception, unexpected_error_handler)


__all__ = ["DOMAIN_CODE_TO_HTTP", "ERROR_RESPONSES", "register_exception_handlers"]
