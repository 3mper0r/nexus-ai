"""Fixtures shared by unit and integration tests."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import Any

import httpx
import pytest
from asgi_lifespan import LifespanManager
from fastapi import FastAPI

from nexus_api.core.config import Settings

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
        }
        values.update(overrides)
        return Settings(**values)

    return factory


@asynccontextmanager
async def _client_for(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    # LifespanManager runs startup/shutdown exactly like uvicorn would.
    async with LifespanManager(app) as manager:
        transport = httpx.ASGITransport(app=manager.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            yield client


@pytest.fixture
def client_for() -> ClientFactory:
    """Usage: ``async with client_for(app) as client: ...``"""
    return _client_for
