"""FastAPI application factory.

Run it with the factory flag, so the process reads the settings at startup instead
of at import time:

    uvicorn --factory payments.presentation.api.app:create_app
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress

from dishka import make_async_container
from dishka.integrations.fastapi import setup_dishka
from fastapi import FastAPI

from payments.infrastructure.config import Settings, get_settings
from payments.infrastructure.messaging.broker import build_broker
from payments.infrastructure.messaging.outbox_relay import OutboxRelay
from payments.infrastructure.messaging.topology import (
    Topology,
    declare_topology_with_retry,
)
from payments.presentation.api.errors import register_exception_handlers
from payments.presentation.api.providers import AppProvider
from payments.presentation.api.routers import health, payments

logger = logging.getLogger(__name__)

API_TOPOLOGY_ATTEMPTS = 3
API_TOPOLOGY_PAUSE_SECONDS = 1.0
RELAY_SHUTDOWN_TIMEOUT_SECONDS = 5.0

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
    broker = build_broker(resolved_settings.rabbitmq_url)
    container = make_async_container(AppProvider(resolved_settings, broker))

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        stop = asyncio.Event()
        relay_task: asyncio.Task[None] | None = None
        topology = Topology(retry_delays_ms=resolved_settings.retry_delays_ms)
        try:
            # The publisher declares the topology itself: a message sent to an
            # exchange with no bound queue would be dropped, and the consumer may
            # not be running at all.
            await declare_topology_with_retry(
                resolved_settings.rabbitmq_url,
                topology,
                attempts=API_TOPOLOGY_ATTEMPTS,
                pause_seconds=API_TOPOLOGY_PAUSE_SECONDS,
            )
        except Exception:
            logger.exception("broker topology is not ready; the outbox keeps the events")
        try:
            await broker.start()
        except Exception:
            logger.exception("broker connection failed; the relay will keep retrying")
        try:
            relay = await container.get(OutboxRelay)
            relay_task = asyncio.create_task(relay.run(stop), name="outbox-relay")
            yield
        finally:
            stop.set()
            if relay_task is not None:
                # Let the loop finish the batch it is publishing before cancelling.
                try:
                    await asyncio.wait_for(relay_task, timeout=RELAY_SHUTDOWN_TIMEOUT_SECONDS)
                except TimeoutError:
                    relay_task.cancel()
                    with suppress(asyncio.CancelledError):
                        await relay_task
            await broker.stop()
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
