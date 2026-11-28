"""Chat HTTP contract without a database: configuration, validation, secret handling."""

from __future__ import annotations

import os

import pytest

from nexus_api.main import create_app
from tests.conftest import ClientFactory, SettingsFactory
from tests.fakes import HashEmbedder

SECRET = "sk-ant-unit-SECRET-do-not-leak-1234567890"


async def test_api_key_never_appears_in_logs_or_responses(
    make_settings: SettingsFactory, client_for: ClientFactory, capsys: pytest.CaptureFixture[str]
) -> None:
    app = create_app(make_settings(anthropic_api_key=SECRET), embedder=HashEmbedder())
    async with client_for(app) as client:
        status = await client.get("/api/v1/llm/status")
        openapi = await client.get("/openapi.json")

    output = capsys.readouterr()
    assert status.json()["configured"] is True
    assert SECRET not in status.text
    assert SECRET not in openapi.text
    assert SECRET not in output.out + output.err


async def test_status_reports_not_configured_without_a_key(
    make_settings: SettingsFactory, client_for: ClientFactory
) -> None:
    app = create_app(make_settings(), embedder=HashEmbedder())
    async with client_for(app) as client:
        response = await client.get("/api/v1/llm/status")

    # The database is unreachable in unit tests: the status degrades instead of failing.
    assert response.status_code == 200
    body = response.json()
    assert body["configured"] is False
    assert body["spent_today_usd"] is None
    assert body["daily_budget_usd"] == 1.0


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"content": ""},
        {"content": "   "},
        {"content": "x" * 4001},
        {"content": "q", "document_ids": ["no"]},
    ],
)
async def test_question_validation(
    make_settings: SettingsFactory, client_for: ClientFactory, body: dict[str, object]
) -> None:
    app = create_app(make_settings(), embedder=HashEmbedder())
    async with client_for(app) as client:
        response = await client.post(
            "/api/v1/conversations/00000000-0000-0000-0000-000000000000/messages", json=body
        )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


async def test_asking_without_a_key_is_a_503_before_any_database_access(
    make_settings: SettingsFactory, client_for: ClientFactory
) -> None:
    app = create_app(make_settings(), embedder=HashEmbedder())
    async with client_for(app) as client:
        response = await client.post(
            "/api/v1/conversations/00000000-0000-0000-0000-000000000000/messages",
            json={"content": "Question?"},
        )

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "llm_not_configured"


def test_routine_tests_never_see_the_api_key(make_settings: SettingsFactory) -> None:
    """Regression: `make test` loads .env; with a real key there, apps built in
    routine tests would have used the real Claude client and spent money."""
    assert "ANTHROPIC_API_KEY" not in os.environ
    assert make_settings().llm_configured is False
