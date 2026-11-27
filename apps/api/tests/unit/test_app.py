"""Application startup, liveness and readiness without a database."""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator

import pytest

from nexus_api.main import create_app
from tests.conftest import ClientFactory, SettingsFactory


async def test_starts_and_stops_without_database(
    make_settings: SettingsFactory, client_for: ClientFactory
) -> None:
    app = create_app(make_settings())

    async with client_for(app) as client:
        assert app.state.engine is not None
        response = await client.get("/api/v1/health/live")

    assert response.status_code == 200


async def test_liveness_is_independent_of_database(
    make_settings: SettingsFactory, client_for: ClientFactory
) -> None:
    async with client_for(create_app(make_settings())) as client:
        response = await client.get("/api/v1/health/live")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


async def test_readiness_returns_503_when_database_refuses_connections(
    make_settings: SettingsFactory, client_for: ClientFactory
) -> None:
    async with client_for(create_app(make_settings())) as client:
        response = await client.get("/api/v1/health/ready")

    assert response.status_code == 503
    assert response.json() == {
        "status": "not_ready",
        "checks": {
            "database": {"status": "down", "latency_ms": None, "error": "unavailable"},
            # make_settings points the model cache at a missing directory.
            "embedding_model": {"status": "down", "latency_ms": None, "error": "unavailable"},
        },
    }
    assert "x-request-id" in response.headers


@pytest.fixture
async def unresponsive_server() -> AsyncIterator[int]:
    """A TCP server that accepts connections but never speaks the Postgres protocol."""
    connections: list[asyncio.StreamWriter] = []

    async def hold_open(_reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        connections.append(writer)

    server = await asyncio.start_server(hold_open, "127.0.0.1", 0)
    port: int = server.sockets[0].getsockname()[1]
    try:
        yield port
    finally:
        for writer in connections:
            writer.close()
        server.close()
        await server.wait_closed()


async def test_readiness_check_is_bounded_by_timeout(
    make_settings: SettingsFactory, client_for: ClientFactory, unresponsive_server: int
) -> None:
    settings = make_settings(postgres_port=unresponsive_server, db_health_timeout_seconds=0.3)

    async with client_for(create_app(settings)) as client:
        start = time.perf_counter()
        response = await client.get("/api/v1/health/ready")
        elapsed = time.perf_counter() - start

    assert response.status_code == 503
    assert response.json()["checks"]["database"]["error"] == "timeout"
    assert elapsed < 1.5


async def test_docs_are_disabled_in_production(
    make_settings: SettingsFactory, client_for: ClientFactory
) -> None:
    async with client_for(create_app(make_settings(app_env="production"))) as client:
        assert (await client.get("/docs")).status_code == 404
        assert (await client.get("/openapi.json")).status_code == 404


async def test_cors_allows_configured_origin_only(
    make_settings: SettingsFactory, client_for: ClientFactory
) -> None:
    app = create_app(make_settings(cors_origins="http://localhost:3000"))
    async with client_for(app) as client:
        allowed = await client.get(
            "/api/v1/health/live", headers={"Origin": "http://localhost:3000"}
        )
        denied = await client.get("/api/v1/health/live", headers={"Origin": "https://evil.test"})

    assert allowed.headers["access-control-allow-origin"] == "http://localhost:3000"
    assert "x-request-id" in allowed.headers["access-control-expose-headers"].lower()
    assert "access-control-allow-origin" not in denied.headers
