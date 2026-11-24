"""Async engine and session lifecycle.

* One ``AsyncEngine`` (and its connection pool) per process, created in the
  application lifespan and disposed on shutdown.
* One ``AsyncSession`` per request, provided by the ``get_db_session``
  dependency. Sessions never commit implicitly: the code performing a unit of
  work calls ``await session.commit()``. Any uncommitted transaction is rolled
  back when the session closes, including when the request raises.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from nexus_api.core.config import Settings


def create_engine(settings: Settings) -> AsyncEngine:
    """Create the engine. No connection is opened until first use."""
    return create_async_engine(
        settings.database_url,
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
        pool_pre_ping=True,
        pool_recycle=1800,
        connect_args={
            "timeout": settings.db_connect_timeout_seconds,
            "server_settings": {"application_name": "nexus-api"},
        },
    )


def create_sessionmaker(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    # expire_on_commit=False: ORM objects stay readable after commit without
    # triggering implicit (and, under asyncio, illegal) lazy refreshes.
    return async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
