"""Fixtures shared by unit and integration tests."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import Any

import httpx
import pytest
from asgi_lifespan import LifespanManager
from fastapi import FastAPI
from pydantic import SecretStr

from nexus_api.core.config import Settings
from nexus_api.embeddings.base import Embedder

SettingsFactory = Callable[..., Settings]
ClientFactory = Callable[[FastAPI], AbstractAsyncContextManager[httpx.AsyncClient]]


@pytest.fixture
def make_settings() -> SettingsFactory:
    """Build Settings from explicit values only.

    Init kwargs take precedence over environment variables, so unit tests are not
    influenced by a developer's shell or .env file.
    """

    def factory(**overrides: Any) -> Settings:
        values: dict[str, Any] = {
            "app_env": "test",
            "log_format": "json",
            "postgres_password": "unit-test-password",
            # Port 1 on loopback: nothing listens there, connections are refused fast.
            "postgres_host": "127.0.0.1",
            "postgres_port": 1,
            "db_health_timeout_seconds": 1.0,
            # A cache directory that does not exist: an app built without an
            # injected embedder fails to load the model fast and deterministically
            # instead of loading ~470 MB from the developer's real cache.
            "embedding_cache_dir": "/nonexistent/nexus-test-model-cache",
        }
        values.update(overrides)
        return Settings(**values)

    return factory


async def wait_until(predicate: Callable[[], bool], *, limit_seconds: float = 10.0) -> None:
    """Poll a condition set by background startup work (model load, recovery).

    Polling (rather than an Event) keeps production code free of test-only hooks.
    """
    async with asyncio.timeout(limit_seconds):
        while not predicate():  # noqa: ASYNC110 - deliberate polling, see docstring
            await asyncio.sleep(0.01)


async def wait_until_accepting(app: FastAPI) -> None:
    """Wait for startup recovery to finish (requires a reachable database)."""
    await wait_until(lambda: bool(app.state.ingestion_runner.accepting))


@asynccontextmanager
async def _client_for(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    # LifespanManager runs startup/shutdown exactly like uvicorn would.
    async with LifespanManager(app) as manager:
        # The model loads in the background; let it settle (ready or failed) so
        # readiness assertions are deterministic.
        await wait_until(lambda: app.state.embedder_provider.state != "loading")
        transport = httpx.ASGITransport(app=manager.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            yield client


@pytest.fixture
def client_for() -> ClientFactory:
    """Usage: ``async with client_for(app) as client: ...``"""
    return _client_for


@pytest.fixture(scope="session")
def e5_embedder() -> Embedder:
    """The real pinned model, loaded once per session (marker ``model`` only).

    Fails, never skips, when the model is missing: selecting ``-m model`` is an
    explicit request to verify the real model.
    """
    from nexus_api.embeddings.e5 import load_e5_embedder

    settings = Settings(postgres_password=SecretStr("unused-by-model-tests"))
    try:
        return load_e5_embedder(settings)
    except OSError as exc:
        pytest.fail(
            f"Embedding model not available locally ({exc}). Run `make models` first.",
            pytrace=False,
        )
