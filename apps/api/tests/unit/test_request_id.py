"""Request correlation: header propagation, generation and structured logs."""

from __future__ import annotations

import asyncio
import json

import pytest
import structlog
from fastapi import APIRouter

from nexus_api.core.request_id import resolve_request_id
from nexus_api.main import create_app
from tests.conftest import ClientFactory, SettingsFactory


@pytest.mark.parametrize(
    "incoming",
    [None, "", "short", "has spaces in it", "x" * 129, "id-with-é-unicode", "a;b<script>"],
)
def test_invalid_or_missing_ids_are_replaced(incoming: str | None) -> None:
    generated = resolve_request_id(incoming)

    assert generated != incoming
    assert len(generated) == 32
    int(generated, 16)  # UUID4 hex


@pytest.mark.parametrize("incoming", ["abcd1234", "req_2026-11-24.trace-01", "x" * 128])
def test_valid_ids_are_preserved(incoming: str) -> None:
    assert resolve_request_id(incoming) == incoming


async def test_generates_unique_id_per_request(
    make_settings: SettingsFactory, client_for: ClientFactory
) -> None:
    async with client_for(create_app(make_settings())) as client:
        first = await client.get("/api/v1/health/live")
        second = await client.get("/api/v1/health/live")

    assert len(first.headers["x-request-id"]) == 32
    assert first.headers["x-request-id"] != second.headers["x-request-id"]


async def test_propagates_valid_incoming_id_to_response_and_logs(
    make_settings: SettingsFactory, client_for: ClientFactory, capsys: pytest.CaptureFixture[str]
) -> None:
    request_id = "client-trace-0001"
    async with client_for(create_app(make_settings())) as client:
        capsys.readouterr()  # discard startup logs
        response = await client.get("/api/v1/health/live", headers={"X-Request-ID": request_id})

    assert response.headers["x-request-id"] == request_id
    records = [json.loads(line) for line in capsys.readouterr().out.splitlines() if line]
    completed = [r for r in records if r["event"] == "request_completed"]
    assert completed == [
        {
            "event": "request_completed",
            "request_id": request_id,
            "method": "GET",
            "path": "/api/v1/health/live",
            "status_code": 200,
            "duration_ms": completed[0]["duration_ms"],
            "level": "info",
            "logger": "nexus_api.core.request_id",
            "timestamp": completed[0]["timestamp"],
        }
    ]


async def test_request_id_is_not_leaked_between_requests(
    make_settings: SettingsFactory, client_for: ClientFactory, capsys: pytest.CaptureFixture[str]
) -> None:
    async with client_for(create_app(make_settings())) as client:
        await client.get("/api/v1/health/live", headers={"X-Request-ID": "first-request-id"})
        capsys.readouterr()
    # Shutdown logs are emitted outside any request and must carry no request_id.
    shutdown_logs = [json.loads(line) for line in capsys.readouterr().out.splitlines() if line]

    assert shutdown_logs
    assert all("request_id" not in record for record in shutdown_logs)


async def test_concurrent_requests_keep_their_own_request_id(
    make_settings: SettingsFactory, client_for: ClientFactory, capsys: pytest.CaptureFixture[str]
) -> None:
    """Context variables must not bleed between interleaved requests."""
    probe_logger = structlog.stdlib.get_logger("probe")
    router = APIRouter()

    @router.get("/_test/work/{n}")
    async def work(n: int) -> dict[str, int]:
        await asyncio.sleep((n % 7) / 1000)  # staggered: forces interleaving
        probe_logger.info("inside_handler", n=n)
        await asyncio.sleep(((30 - n) % 5) / 1000)
        return {"n": n}

    app = create_app(make_settings())
    app.include_router(router)
    ids = [f"concurrent-request-{i:03d}" for i in range(30)]

    async with client_for(app) as client:
        responses = await asyncio.gather(
            *(client.get(f"/_test/work/{i}", headers={"X-Request-ID": ids[i]}) for i in range(30))
        )

    assert [r.headers["x-request-id"] for r in responses] == ids
    records = [json.loads(line) for line in capsys.readouterr().out.splitlines() if line]
    inside = [r for r in records if r["event"] == "inside_handler"]
    assert len(inside) == 30
    assert all(r["request_id"] == ids[r["n"]] for r in inside)
