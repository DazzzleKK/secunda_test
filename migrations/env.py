"""Alembic environment: async engine, metadata from the SQLAlchemy models.

The database URL is resolved in this order:

1. `config.attributes["db_url"]` — used by tests;
2. `sqlalchemy.url` from `alembic.ini` or the command line;
3. `Settings.sqlalchemy_url` — the normal path in Docker.
"""

from __future__ import annotations

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine

from payments.infrastructure.config import Settings
from payments.infrastructure.db.models import Base

config = context.config

if config.config_file_name is not None and config.get_section("loggers") is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def resolve_database_url() -> str:
    """Return the async database URL for the current run."""
    override = config.attributes.get("db_url") or config.get_main_option("sqlalchemy.url")
    if override:
        return str(override)
    return Settings().sqlalchemy_url


def run_migrations_offline() -> None:
    """Emit SQL without connecting to the database."""
    context.configure(
        url=resolve_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    """Run migrations on a sync-style connection provided by run_sync."""
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """Open an async connection and run the migrations through run_sync."""
    engine = create_async_engine(resolve_database_url(), poolclass=pool.NullPool)
    try:
        async with engine.connect() as connection:
            await connection.run_sync(do_run_migrations)
    finally:
        await engine.dispose()


def run_migrations_online() -> None:
    """Entry point for online migrations."""
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
