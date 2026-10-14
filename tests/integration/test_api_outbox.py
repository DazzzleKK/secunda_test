"""The outbox guarantee seen from the API: accepted now, published later.

This is the scenario the README describes as scenario 7 — with the broker down the
API still answers 202 and the event waits in the outbox; once the broker is back the
embedded relay publishes it. Both halves run against a real PostgreSQL.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
import pytest
from sqlalchemy import func, select

from payments.infrastructure.config import Settings
from payments.infrastructure.db.models import OutboxModel
from payments.presentation.api.app import create_app
from tests.integration.helpers import (
    TEST_API_KEY,
    RabbitMq,
    api_headers,
    payment_body,
)

pytestmark = pytest.mark.integration

PAYMENTS_URL = "/api/v1/payments"
DEAD_BROKER_HOST = "127.0.0.1"
DEAD_BROKER_PORT = 1
PUBLISH_TIMEOUT_SECONDS = 30.0
POLL_INTERVAL_SECONDS = 0.2


@asynccontextmanager
async def running_api(settings: Settings) -> AsyncIterator[httpx.AsyncClient]:
    """Run the application with its real lifespan: broker, embedded relay, DI."""
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            yield client


async def wait_for_published(session, payment_id, *, timeout: float) -> bool:
    """Wait until the event of this payment is stamped as published."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        published = await session.scalar(
            select(func.count())
            .select_from(OutboxModel)
            .where(
                OutboxModel.aggregate_id == payment_id,
                OutboxModel.published_at.is_not(None),
            )
        )
        if published:
            return True
        await asyncio.sleep(POLL_INTERVAL_SECONDS)
    return False


@pytest.fixture
def api_settings(postgres_url: str, rabbitmq: RabbitMq):
    """Settings pointing the API at the test database and broker."""

    def build(*, broker: str = "live") -> Settings:
        host = DEAD_BROKER_HOST if broker == "dead" else rabbitmq.host
        port = DEAD_BROKER_PORT if broker == "dead" else rabbitmq.port
        return Settings(
            _env_file=None,
            api_key=TEST_API_KEY,
            database_url=postgres_url,
            rabbitmq_host=host,
            rabbitmq_port=port,
        )

    return build


async def test_the_api_accepts_a_payment_while_the_broker_is_down(api_settings, session) -> None:
    """202 and a waiting outbox row, without touching a broker at all.

    The application is built with a broker that cannot be reached; the request path
    never involves it, and that is exactly the guarantee: accepting a payment does
    not depend on the broker being alive.
    """
    app = create_app(api_settings(broker="dead"))
    transport = httpx.ASGITransport(app=app)
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            response = await client.post(
                PAYMENTS_URL,
                json=payment_body(),
                headers=api_headers("outbox-broker-down"),
            )
    finally:
        await app.state.dishka_container.close()

    assert response.status_code == 202
    payment_id = response.json()["payment_id"]

    pending = await session.scalar(
        select(func.count()).select_from(OutboxModel).where(OutboxModel.aggregate_id == payment_id)
    )
    published = await session.scalar(
        select(func.count())
        .select_from(OutboxModel)
        .where(
            OutboxModel.aggregate_id == payment_id,
            OutboxModel.published_at.is_not(None),
        )
    )

    assert pending == 1
    assert published == 0


async def test_the_embedded_relay_publishes_the_event(api_settings, session) -> None:
    async with running_api(api_settings()) as client:
        response = await client.post(
            PAYMENTS_URL,
            json=payment_body(),
            headers=api_headers("outbox-broker-up"),
        )

        assert response.status_code == 202
        payment_id = response.json()["payment_id"]

        assert await wait_for_published(session, payment_id, timeout=PUBLISH_TIMEOUT_SECONDS)
