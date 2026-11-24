"""Error envelope consistency and information-leak prevention."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import APIRouter, FastAPI

from nexus_api.core.errors import AppError
from nexus_api.main import create_app
from tests.conftest import ClientFactory, SettingsFactory


class WidgetNotFoundError(AppError):
    status_code = 404
    code = "widget_not_found"


def _build_router() -> APIRouter:
    router = APIRouter(prefix="/_test")

    @router.get("/boom")
    async def boom() -> None:
        raise RuntimeError("db password=hunter2 at /srv/internal/module.py")

    @router.get("/items")
    async def items(limit: int) -> dict[str, int]:
        return {"limit": limit}

    @router.get("/widget")
    async def widget() -> None:
        raise WidgetNotFoundError("Widget does not exist.")

    return router


@pytest.fixture
def app(make_settings: SettingsFactory) -> FastAPI:
    application = create_app(make_settings())
    application.include_router(_build_router())
    return application


def _assert_envelope(body: dict[str, Any], code: str, request_id: str) -> None:
    assert set(body) == {"error"}
    assert body["error"]["code"] == code
    assert body["error"]["request_id"] == request_id
    assert isinstance(body["error"]["message"], str)


async def test_unknown_route_returns_404_envelope(app: FastAPI, client_for: ClientFactory) -> None:
    async with client_for(app) as client:
        response = await client.get("/api/v1/does-not-exist")

    assert response.status_code == 404
    _assert_envelope(response.json(), "not_found", response.headers["x-request-id"])


async def test_wrong_method_returns_405_with_allow_header(
    app: FastAPI, client_for: ClientFactory
) -> None:
    async with client_for(app) as client:
        response = await client.post("/api/v1/health/live")

    assert response.status_code == 405
    assert response.headers["allow"] == "GET"
    _assert_envelope(response.json(), "method_not_allowed", response.headers["x-request-id"])


async def test_validation_error_returns_sanitized_details(
    app: FastAPI, client_for: ClientFactory
) -> None:
    async with client_for(app) as client:
        response = await client.get("/_test/items", params={"limit": "secret-input-value"})

    assert response.status_code == 422
    body = response.json()
    _assert_envelope(body, "validation_error", response.headers["x-request-id"])
    assert body["error"]["details"] == [
        {
            "loc": ["query", "limit"],
            "message": "Input should be a valid integer, unable to parse string as an integer",
            "type": "int_parsing",
        }
    ]
    assert "secret-input-value" not in response.text


async def test_domain_error_maps_to_declared_status(
    app: FastAPI, client_for: ClientFactory
) -> None:
    async with client_for(app) as client:
        response = await client.get("/_test/widget")

    assert response.status_code == 404
    _assert_envelope(response.json(), "widget_not_found", response.headers["x-request-id"])
    assert response.json()["error"]["message"] == "Widget does not exist."


async def test_unhandled_exception_returns_generic_500_without_internals(
    make_settings: SettingsFactory, client_for: ClientFactory, capsys: pytest.CaptureFixture[str]
) -> None:
    # Build the app inside the test so its log handler binds to capsys' stdout.
    app = create_app(make_settings())
    app.include_router(_build_router())
    async with client_for(app) as client:
        response = await client.get("/_test/boom")

    assert response.status_code == 500
    body = response.json()
    _assert_envelope(body, "internal_error", response.headers["x-request-id"])
    assert body["error"]["message"] == "An unexpected error occurred."
    for leaked in ("hunter2", "Traceback", "RuntimeError", "/srv/internal"):
        assert leaked not in response.text
    # ...but the details are preserved in the server logs for operators.
    logs = capsys.readouterr().out
    assert "hunter2" in logs
    assert '"locals"' not in logs  # tracebacks are logged without local variables
