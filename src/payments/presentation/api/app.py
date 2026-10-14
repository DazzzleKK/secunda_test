"""FastAPI application factory.

Run it with the factory flag, so the process reads the settings at startup instead
of at import time:

    uvicorn --factory payments.presentation.api.app:create_app
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from dishka import make_async_container
from dishka.integrations.fastapi import setup_dishka
from fastapi import FastAPI

from payments.infrastructure.config import Settings, get_settings
from payments.presentation.api.errors import register_exception_handlers
from payments.presentation.api.providers import AppProvider
from payments.presentation.api.routers import health, payments

DESCRIPTION = """
Asynchronous payment processing service.

A payment is accepted with `202 Accepted`, stored together with its outbox event in
a single transaction and processed by a consumer through an emulated gateway. The
result is delivered to the client's `webhook_url` with retries and a dead letter
queue. Requests are idempotent by the mandatory `Idempotency-Key` header and
authenticated by the static `X-API-Key` header.
"""


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the HTTP application with its DI container and handlers."""
    resolved_settings = settings if settings is not None else get_settings()
    container = make_async_container(AppProvider(resolved_settings))

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        yield
        await container.close()

    app = FastAPI(
        title="Payments API",
        version="1.0.0",
        summary="Asynchronous payment processing service",
        description=DESCRIPTION,
        lifespan=lifespan,
    )

    register_exception_handlers(app)
    app.include_router(payments.router)
    app.include_router(health.router)
    setup_dishka(container, app)
    return app


__all__ = ["create_app"]
