"""Liveness and readiness probes."""

from __future__ import annotations

from collections.abc import AsyncIterator

import httpx
import pytest
import pytest_asyncio

from payments.infrastructure.config import Settings
from payments.presentation.api.app import create_app
from tests.integration.helpers import TEST_API_KEY

pytestmark = pytest.mark.integration

UNREACHABLE_DATABASE_URL = "postgresql+asyncpg://payments:payments@127.0.0.1:1/payments"


async def test_health_reports_ok(api_client) -> None:
    response = await api_client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


async def test_ready_reports_the_database(api_client) -> None:
    response = await api_client.get("/ready")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": "ok"}


@pytest_asyncio.fixture
async def broken_client() -> AsyncIterator[httpx.AsyncClient]:
    """Client of an application whose database is unreachable."""
    app = create_app(
        Settings(
            _env_file=None,
            api_key=TEST_API_KEY,
            database_url=UNREACHABLE_DATABASE_URL,
        )
    )
    transport = httpx.ASGITransport(app=app)
    try:
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://testserver",
        ) as client:
            yield client
    finally:
        await app.state.dishka_container.close()


async def test_ready_returns_503_when_the_database_is_unreachable(broken_client) -> None:
    response = await broken_client.get("/ready")

    assert response.status_code == 503
    assert response.json() == {"status": "unavailable", "database": "down"}
