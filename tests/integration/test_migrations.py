"""Integration tests of the Alembic migration and the schema it produces."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import inspect
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import AsyncEngine

from payments.infrastructure.db.models import Base

pytestmark = pytest.mark.integration


def _table_names(connection: Connection) -> list[str]:
    return list(inspect(connection).get_table_names())


def _index_names(connection: Connection, table: str) -> set[str]:
    return {index["name"] for index in inspect(connection).get_indexes(table)}


def _unique_constraint_names(connection: Connection, table: str) -> set[str]:
    return {item["name"] for item in inspect(connection).get_unique_constraints(table)}


async def test_upgrade_creates_both_tables(engine: AsyncEngine) -> None:
    async with engine.connect() as connection:
        tables = await connection.run_sync(_table_names)

    assert "payments" in tables
    assert "outbox" in tables


async def test_schema_has_the_expected_indexes_and_constraints(engine: AsyncEngine) -> None:
    async with engine.connect() as connection:
        payment_indexes = await connection.run_sync(_index_names, "payments")
        outbox_indexes = await connection.run_sync(_index_names, "outbox")
        unique_constraints = await connection.run_sync(_unique_constraint_names, "payments")

    assert "ix_payments_status_created_at" in payment_indexes
    assert "ix_outbox_unpublished" in outbox_indexes
    assert "uq_payments_idempotency_key" in unique_constraints


async def test_models_and_migration_do_not_diverge(engine: AsyncEngine) -> None:
    def compare(connection: Connection) -> list[Any]:
        context = MigrationContext.configure(connection, opts={"compare_type": True})
        return list(compare_metadata(context, Base.metadata))

    async with engine.connect() as connection:
        differences = await connection.run_sync(compare)

    assert differences == [], f"migration and models diverge: {differences}"


async def test_downgrade_removes_the_tables_and_upgrade_brings_them_back(
    alembic_config: Config, engine: AsyncEngine
) -> None:
    await asyncio.to_thread(command.downgrade, alembic_config, "base")
    try:
        async with engine.connect() as connection:
            tables = await connection.run_sync(_table_names)
        assert "payments" not in tables
        assert "outbox" not in tables
    finally:
        await asyncio.to_thread(command.upgrade, alembic_config, "head")

    async with engine.connect() as connection:
        tables = await connection.run_sync(_table_names)
        outbox_indexes = await connection.run_sync(_index_names, "outbox")

    assert "payments" in tables
    assert "outbox" in tables
    assert "ix_outbox_unpublished" in outbox_indexes
