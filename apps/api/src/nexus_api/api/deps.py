"""FastAPI dependencies that expose lifespan-managed resources to handlers.

Resources live on ``app.state`` (set in the lifespan). These accessors give
handlers typed access and are the seam tests use via ``dependency_overrides``.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from nexus_api.core.config import Settings


def get_settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


def get_engine(request: Request) -> AsyncEngine:
    engine: AsyncEngine = request.app.state.engine
    return engine


async def get_db_session(request: Request) -> AsyncIterator[AsyncSession]:
    """One session per request; closed (and rolled back if uncommitted) on exit.

    The ``async with`` must live directly in this generator. Delegating to another
    async generator via ``async for ... yield`` would leave the inner generator
    suspended when the endpoint raises, leaking its connection until GC.
    """
    sessionmaker: async_sessionmaker[AsyncSession] = request.app.state.sessionmaker
    async with sessionmaker() as session:
        yield session


SettingsDep = Annotated[Settings, Depends(get_settings)]
EngineDep = Annotated[AsyncEngine, Depends(get_engine)]
DbSessionDep = Annotated[AsyncSession, Depends(get_db_session)]
