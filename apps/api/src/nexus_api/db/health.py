"""Database connectivity check with a bounded timeout.

Pure database logic: no HTTP concepts. The API layer maps the result to a
status code.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Literal

import asyncpg
import structlog
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine

logger = structlog.stdlib.get_logger(__name__)

FailureReason = Literal["timeout", "unavailable"]

# Failures that mean "the database cannot serve us right now". asyncpg errors are
# listed explicitly because errors raised while *establishing* a connection
# (wrong password, unknown database, too many connections) reach callers
# unwrapped by SQLAlchemy.
_DATABASE_FAILURES: tuple[type[Exception], ...] = (
    SQLAlchemyError,
    OSError,
    asyncpg.PostgresError,
    asyncpg.InterfaceError,
)


@dataclass(frozen=True, slots=True)
class DatabaseHealth:
    is_up: bool
    latency_ms: float | None = None
    failure: FailureReason | None = None


async def check_database(engine: AsyncEngine, timeout_seconds: float) -> DatabaseHealth:
    """Run ``SELECT 1`` and report whether the database answered in time."""
    start = time.perf_counter()
    try:
        async with asyncio.timeout(timeout_seconds), engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
    except TimeoutError:
        logger.warning("database_check_timeout", timeout_seconds=timeout_seconds)
        return DatabaseHealth(is_up=False, failure="timeout")
    except _DATABASE_FAILURES as exc:
        logger.warning("database_check_failed", error_type=type(exc).__name__, error=str(exc))
        return DatabaseHealth(is_up=False, failure="unavailable")
    latency_ms = round((time.perf_counter() - start) * 1000, 2)
    return DatabaseHealth(is_up=True, latency_ms=latency_ms)
