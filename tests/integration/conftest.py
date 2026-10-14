"""Fixtures for integration tests: a real PostgreSQL from testcontainers."""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker
from testcontainers.community.postgres import PostgresContainer

from payments.infrastructure.db.session import build_engine, build_session_factory
from payments.infrastructure.db.unit_of_work import SqlAlchemyUnitOfWorkFactory
from tests.integration.helpers import truncate_all

POSTGRES_IMAGE = "postgres:16-alpine"
POSTGRES_PORT = 5432
PROJECT_ROOT = Path(__file__).resolve().parents[2]
ALEMBIC_INI = PROJECT_ROOT / "alembic.ini"


def build_alembic_config(database_url: str) -> Config:
    """Build an Alembic config that points at the test database."""
    config = Config(str(ALEMBIC_INI))
    config.set_main_option("script_location", str(PROJECT_ROOT / "migrations"))
    config.set_main_option("sqlalchemy.url", database_url)
    return config


@pytest.fixture(scope="session")
def postgres_url() -> Iterator[str]:
    """Start PostgreSQL once per session and migrate it to head."""
    with PostgresContainer(
        POSTGRES_IMAGE,
        username="payments",
        password="payments",
        dbname="payments",
    ) as container:
        host = container.get_container_host_ip()
        port = container.get_exposed_port(POSTGRES_PORT)
        url = f"postgresql+asyncpg://payments:payments@{host}:{port}/payments"
        command.upgrade(build_alembic_config(url), "head")
        yield url


@pytest.fixture(scope="session")
def alembic_config(postgres_url: str) -> Config:
    """Alembic config bound to the test database."""
    return build_alembic_config(postgres_url)


@pytest_asyncio.fixture
async def engine(postgres_url: str) -> AsyncIterator[AsyncEngine]:
    """One engine per test; disposed afterwards."""
    engine = build_engine(postgres_url)
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest_asyncio.fixture
async def session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Session factory bound to the test engine."""
    return build_session_factory(engine)


@pytest_asyncio.fixture(autouse=True)
async def clean_database(engine: AsyncEngine) -> AsyncIterator[None]:
    """Every integration test starts and ends with empty tables."""
    await truncate_all(engine)
    try:
        yield
    finally:
        await truncate_all(engine)


@pytest_asyncio.fixture
async def session(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    """A directly usable session for assertions."""
    async with session_factory() as session:
        yield session


@pytest_asyncio.fixture
async def uow_factory(
    session_factory: async_sessionmaker[AsyncSession],
) -> SqlAlchemyUnitOfWorkFactory:
    """Factory of transactions, as the use cases receive it."""
    return SqlAlchemyUnitOfWorkFactory(session_factory)
