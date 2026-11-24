"""Integration test infrastructure: a real PostgreSQL, never mocked.

Policy: when integration tests are selected and PostgreSQL is unreachable, the
session FAILS loudly. It never silently skips. To exclude these tests locally,
deselect them explicitly: ``pytest -m "not integration"`` (``make test-unit``).

Isolation: tests run against a dedicated ``<POSTGRES_DB>_test`` database that is
dropped, recreated and migrated with Alembic at session start, then dropped at
the end. Development data in ``<POSTGRES_DB>`` is never touched.

Safeguards around the destructive DROP/CREATE DATABASE statements are enforced
by ``resolve_test_database_name`` (see tests/unit/test_integration_safety.py).
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import asyncpg
import pytest
from alembic import command
from alembic.config import Config
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncEngine

from nexus_api.core.config import Settings
from nexus_api.db.engine import create_engine

API_ROOT = Path(__file__).resolve().parents[2]
_SAFE_IDENTIFIER = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")
TEST_DATABASE_SUFFIX = "_test"


class UnsafeTestDatabaseError(Exception):
    """Raised when the integration suite must not create or drop a database."""


def resolve_test_database_name(settings: Settings) -> str:
    """Return the only database name the suite may DROP/CREATE, or refuse.

    * never against a production configuration;
    * always a derived ``<db>_test`` name, distinct from the configured database;
    * always a plain lowercase identifier (it is interpolated into DDL).
    """
    if settings.is_production:
        raise UnsafeTestDatabaseError("refusing to run integration tests with APP_ENV=production")
    name = f"{settings.postgres_db}{TEST_DATABASE_SUFFIX}"
    if not _SAFE_IDENTIFIER.fullmatch(name):
        raise UnsafeTestDatabaseError(f"unsafe test database name: {name!r}")
    if name == settings.postgres_db or not name.endswith(TEST_DATABASE_SUFFIX):
        raise UnsafeTestDatabaseError(f"test database {name!r} would not be isolated")
    return name


def alembic_config(settings: Settings) -> Config:
    config = Config(str(API_ROOT / "alembic.ini"))
    config.attributes["settings"] = settings
    config.attributes["configure_logger"] = False
    return config


async def _admin_execute(settings: Settings, *statements: str) -> None:
    connection = await asyncpg.connect(
        host=settings.postgres_host,
        port=settings.postgres_port,
        user=settings.postgres_user,
        password=settings.postgres_password.get_secret_value(),
        database=settings.postgres_db,
        timeout=settings.db_connect_timeout_seconds,
    )
    try:
        for statement in statements:
            await connection.execute(statement)
    finally:
        await connection.close()


@pytest.fixture(scope="session")
def base_settings() -> Settings:
    try:
        return Settings()
    except ValidationError as exc:
        pytest.fail(
            "Integration tests need database settings from the environment "
            f"(run via `make test-integration`, which loads .env):\n{exc}",
            pytrace=False,
        )


@pytest.fixture(scope="session")
def test_settings(base_settings: Settings) -> Iterator[Settings]:
    try:
        test_db = resolve_test_database_name(base_settings)
    except UnsafeTestDatabaseError as exc:
        pytest.fail(str(exc), pytrace=False)

    try:
        asyncio.run(
            _admin_execute(
                base_settings,
                f'DROP DATABASE IF EXISTS "{test_db}" WITH (FORCE)',
                f'CREATE DATABASE "{test_db}"',
            )
        )
    except (OSError, TimeoutError, asyncpg.PostgresError) as exc:
        pytest.fail(
            f"PostgreSQL is not available at {base_settings.postgres_host}:"
            f"{base_settings.postgres_port} ({type(exc).__name__}: {exc}). "
            "Start it with `make db-up`, or exclude integration tests with `make test-unit`.",
            pytrace=False,
        )

    yield base_settings.model_copy(update={"postgres_db": test_db})

    asyncio.run(_admin_execute(base_settings, f'DROP DATABASE IF EXISTS "{test_db}" WITH (FORCE)'))


@pytest.fixture(scope="session")
def migrated_settings(test_settings: Settings) -> Settings:
    """Settings pointing at a test database upgraded to the latest migration."""
    command.upgrade(alembic_config(test_settings), "head")
    return test_settings


@pytest.fixture
async def engine(migrated_settings: Settings) -> AsyncIterator[AsyncEngine]:
    engine = create_engine(migrated_settings)
    try:
        yield engine
    finally:
        await engine.dispose()
