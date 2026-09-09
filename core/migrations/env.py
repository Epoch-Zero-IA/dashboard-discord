"""Alembic environment, in async mode.

Async on purpose: the application URL carries the `+asyncpg` driver, and keeping a
second synchronous URL just for migrations is how the two eventually disagree about
which database they point at.
"""

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import Connection

from core.config import database_url
from core.db import create_engine, metadata

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = metadata


def _url() -> str:
    """Return the URL to migrate.

    `sqlalchemy.url` is absent from alembic.ini, so this falls through to the
    environment for every normal run. The test harness sets the option in-process to
    aim the same env.py at a throwaway database.

    Returns:
        The connection URL.
    """
    return config.get_main_option("sqlalchemy.url") or database_url()


def run_migrations_offline() -> None:
    """Emit the SQL of the pending migrations without connecting to anything."""
    context.configure(
        url=_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    """Run the migrations on an already-open synchronous connection.

    Args:
        connection: The connection handed over by `run_sync`.
    """
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """Open an async connection and run the migrations through it."""
    engine = create_engine(_url())
    try:
        async with engine.connect() as connection:
            await connection.run_sync(do_run_migrations)
    finally:
        # Disposed even on failure: `alembic upgrade head` runs in the API entrypoint
        # before Granian, and a leaked pool there would hold connections for the life
        # of the container.
        await engine.dispose()


def run_migrations_online() -> None:
    """Run the migrations against a live database."""
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
