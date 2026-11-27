"""Database connectivity, migrations and session lifecycle against real PostgreSQL."""

from __future__ import annotations

import asyncio

import asyncpg
import pytest
from alembic import command
from alembic.script import ScriptDirectory
from fastapi import APIRouter, FastAPI
from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlalchemy.pool import QueuePool

from nexus_api.api.deps import DbSessionDep
from nexus_api.core.config import Settings
from nexus_api.db.engine import create_engine
from nexus_api.db.health import check_database
from nexus_api.main import create_app
from tests.conftest import ClientFactory, wait_until_accepting
from tests.fakes import HashEmbedder
from tests.integration.conftest import alembic_config

pytestmark = pytest.mark.integration


async def _scalar(settings: Settings, query: str, *args: object) -> object:
    connection = await asyncpg.connect(
        host=settings.postgres_host,
        port=settings.postgres_port,
        user=settings.postgres_user,
        password=settings.postgres_password.get_secret_value(),
        database=settings.postgres_db,
        server_settings={"application_name": "nexus-test-observer"},
    )
    try:
        return await connection.fetchval(query, *args)
    finally:
        await connection.close()


async def test_database_check_reports_up(engine: AsyncEngine) -> None:
    health = await check_database(engine, timeout_seconds=2.0)

    assert health.is_up
    assert health.failure is None
    assert health.latency_ms is not None


async def test_readiness_returns_200_when_database_is_available(
    migrated_settings: Settings, client_for: ClientFactory
) -> None:
    async with client_for(create_app(migrated_settings, embedder=HashEmbedder())) as client:
        response = await client.get("/api/v1/health/ready")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ready"
    assert body["checks"]["database"]["status"] == "up"
    assert body["checks"]["database"]["error"] is None


async def test_migrations_are_at_head(engine: AsyncEngine, migrated_settings: Settings) -> None:
    head = ScriptDirectory.from_config(alembic_config(migrated_settings)).get_current_head()
    async with engine.connect() as connection:
        result = await connection.execute(text("SELECT version_num FROM alembic_version"))
        current = result.scalar()

    assert head == "0002"
    assert current == head


async def test_pgvector_extension_is_installed_and_functional(engine: AsyncEngine) -> None:
    async with engine.connect() as connection:
        version = (
            await connection.execute(
                text("SELECT extversion FROM pg_extension WHERE extname = 'vector'")
            )
        ).scalar()
        distance = (
            await connection.execute(text("SELECT '[1,2,3]'::vector <-> '[1,2,4]'::vector"))
        ).scalar()

    assert version == "0.8.7"
    assert distance == pytest.approx(1.0)


def test_initial_migration_is_reversible(migrated_settings: Settings) -> None:
    # Sync test: Alembic's async env.py drives its own event loop via asyncio.run().
    config = alembic_config(migrated_settings)
    has_vector = "SELECT count(*) FROM pg_extension WHERE extname = 'vector'"

    command.downgrade(config, "base")
    assert asyncio.run(_scalar(migrated_settings, has_vector)) == 0

    command.upgrade(config, "head")
    assert asyncio.run(_scalar(migrated_settings, has_vector)) == 1


@pytest.fixture
def app_with_session_routes(migrated_settings: Settings) -> FastAPI:
    """App with routes that exercise the real per-request session dependency."""
    router = APIRouter(prefix="/_test")

    @router.post("/commit")
    async def commit(session: DbSessionDep) -> dict[str, bool]:
        await session.execute(text("CREATE TABLE m0_committed (id int)"))
        await session.commit()
        return {"ok": True}

    @router.post("/no-commit")
    async def no_commit(session: DbSessionDep) -> dict[str, bool]:
        await session.execute(text("CREATE TABLE m0_uncommitted (id int)"))
        return {"ok": True}

    @router.post("/fail")
    async def fail(session: DbSessionDep) -> None:
        await session.execute(text("CREATE TABLE m0_failed (id int)"))
        raise RuntimeError("simulated failure mid-transaction")

    app = create_app(migrated_settings, embedder=HashEmbedder())
    app.include_router(router)
    return app


async def test_request_session_commits_only_explicitly_and_rolls_back_on_error(
    app_with_session_routes: FastAPI, client_for: ClientFactory, migrated_settings: Settings
) -> None:
    async with client_for(app_with_session_routes) as client:
        assert (await client.post("/_test/commit")).status_code == 200
        assert (await client.post("/_test/no-commit")).status_code == 200
        assert (await client.post("/_test/fail")).status_code == 500

    exists = "SELECT to_regclass('public.{}') IS NOT NULL"
    try:
        assert await _scalar(migrated_settings, exists.format("m0_committed")) is True
        assert await _scalar(migrated_settings, exists.format("m0_uncommitted")) is False
        assert await _scalar(migrated_settings, exists.format("m0_failed")) is False
    finally:
        await _scalar(migrated_settings, "DROP TABLE IF EXISTS m0_committed")


async def test_shutdown_disposes_engine_connections(
    migrated_settings: Settings, client_for: ClientFactory
) -> None:
    open_app_connections = """
        SELECT count(*) FROM pg_stat_activity
        WHERE application_name = 'nexus-api' AND datname = $1
    """
    db_name = migrated_settings.postgres_db

    app = create_app(migrated_settings, embedder=HashEmbedder())
    async with client_for(app) as client:
        await wait_until_accepting(app)  # startup recovery uses the pool too
        assert (await client.get("/api/v1/health/ready")).status_code == 200
        assert await _scalar(migrated_settings, open_app_connections, db_name) == 1  # pooled, idle

    # Backend processes exit asynchronously after the client disconnects.
    for _ in range(40):
        if await _scalar(migrated_settings, open_app_connections, db_name) == 0:
            break
        await asyncio.sleep(0.05)
    assert await _scalar(migrated_settings, open_app_connections, db_name) == 0


# --- Readiness failure modes (regression: connect-time asyncpg errors used to
# escape check_database and turn readiness into a 500) ---------------------------


@pytest.mark.parametrize(
    "overrides",
    [
        {"postgres_password": SecretStr("definitely-wrong-password")},
        {"postgres_db": "nexus_does_not_exist"},
    ],
    ids=["wrong-password", "missing-database"],
)
async def test_readiness_returns_503_for_rejected_connections(
    migrated_settings: Settings, client_for: ClientFactory, overrides: dict[str, object]
) -> None:
    settings = migrated_settings.model_copy(update=overrides)

    async with client_for(create_app(settings, embedder=HashEmbedder())) as client:
        response = await client.get("/api/v1/health/ready")

    assert response.status_code == 503
    assert response.json()["checks"]["database"] == {
        "status": "down",
        "latency_ms": None,
        "error": "unavailable",
    }
    assert "password" not in response.text


# --- Connection release guarantees ------------------------------------------------


def _checked_out(engine: AsyncEngine) -> int:
    pool = engine.pool
    assert isinstance(pool, QueuePool)
    return pool.checkedout()


async def test_readiness_timeout_covers_pool_acquisition(migrated_settings: Settings) -> None:
    engine = create_engine(
        migrated_settings.model_copy(update={"db_pool_size": 1, "db_max_overflow": 0})
    )
    try:
        async with engine.connect():  # exhaust the pool
            health = await check_database(engine, timeout_seconds=0.3)
            assert health.failure == "timeout"
        assert _checked_out(engine) == 0
        assert (await check_database(engine, timeout_seconds=2.0)).is_up
    finally:
        await engine.dispose()


async def test_repeated_failed_checks_do_not_exhaust_pool(migrated_settings: Settings) -> None:
    engine = create_engine(
        migrated_settings.model_copy(
            update={
                "db_pool_size": 1,
                "db_max_overflow": 0,
                "postgres_password": SecretStr("wrong-password"),
            }
        )
    )
    try:
        for _ in range(5):
            assert (await check_database(engine, timeout_seconds=1.0)).failure == "unavailable"
            assert _checked_out(engine) == 0
    finally:
        await engine.dispose()


async def test_cancelled_request_releases_connection_and_server_query(
    migrated_settings: Settings, client_for: ClientFactory
) -> None:
    router = APIRouter(prefix="/_test")

    @router.get("/slow")
    async def slow(session: DbSessionDep) -> None:
        await session.execute(text("SELECT pg_sleep(30)"))

    app = create_app(migrated_settings, embedder=HashEmbedder())
    app.include_router(router)
    active_sleeps = (
        "SELECT count(*) FROM pg_stat_activity "
        "WHERE state = 'active' AND query LIKE '%pg_sleep(30)%' AND pid <> pg_backend_pid()"
    )

    async with client_for(app) as client:
        await wait_until_accepting(app)
        task = asyncio.create_task(client.get("/_test/slow"))
        for _ in range(40):
            if _checked_out(app.state.engine) == 1:
                break
            await asyncio.sleep(0.05)
        assert _checked_out(app.state.engine) == 1

        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        assert _checked_out(app.state.engine) == 0
        for _ in range(40):
            if await _scalar(migrated_settings, active_sleeps) == 0:
                break
            await asyncio.sleep(0.05)
        assert await _scalar(migrated_settings, active_sleeps) == 0


async def test_commit_releases_connection_before_request_ends(
    migrated_settings: Settings, client_for: ClientFactory
) -> None:
    """Long work after commit (e.g. an LLM call) must not hold a pooled connection."""
    router = APIRouter(prefix="/_test")
    app = create_app(migrated_settings, embedder=HashEmbedder())

    @router.get("/commit-then-work")
    async def commit_then_work(session: DbSessionDep) -> dict[str, int]:
        await session.execute(text("SELECT 1"))
        held_in_transaction = _checked_out(app.state.engine)
        await session.commit()
        return {"before": held_in_transaction, "after": _checked_out(app.state.engine)}

    app.include_router(router)
    async with client_for(app) as client:
        await wait_until_accepting(app)
        response = await client.get("/_test/commit-then-work")

    assert response.json() == {"before": 1, "after": 0}
