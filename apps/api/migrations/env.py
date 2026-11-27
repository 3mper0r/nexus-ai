"""Alembic environment (async).

The database URL comes from the application's validated ``Settings``, so
migrations and the running API can never disagree about which database they use.
"""

from __future__ import annotations

import asyncio

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine

from nexus_api.core.config import Settings
from nexus_api.core.logging import configure_logging
from nexus_api.db import models as _models  # noqa: F401 - registers tables on Base.metadata
from nexus_api.db.base import Base

config = context.config

# Programmatic callers (e.g. the integration test suite, which migrates a
# throwaway test database) may inject Settings; the CLI reads the environment.
_injected = config.attributes.get("settings")
settings: Settings = _injected if isinstance(_injected, Settings) else Settings()

# Programmatic callers can pass configure_logger=False to keep their own logging.
if config.attributes.get("configure_logger", True):
    configure_logging("INFO", settings.log_format)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Emit SQL to stdout instead of executing it (``alembic upgrade --sql``)."""
    context.configure(
        url=settings.database_url.render_as_string(hide_password=False),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    # NullPool: a one-shot CLI process should not keep pooled connections.
    connectable = create_async_engine(settings.database_url, poolclass=pool.NullPool)
    try:
        async with connectable.connect() as connection:
            await connection.run_sync(do_run_migrations)
    finally:
        await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
