"""Grounded answers end to end: real uvicorn, real PostgreSQL, scripted generator.

The app runs under a real uvicorn server so that streaming and client disconnects
behave as in production (httpx's in-process ASGI transport buffers responses).
"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from decimal import Decimal
from typing import Any

import asyncpg
import httpx
import pytest
import uvicorn
from fastapi import FastAPI

from nexus_api.api.v1.conversations import answer_response
from nexus_api.core.config import Settings
from nexus_api.llm.generator import (
    CitationRef,
    Completed,
    GenerationError,
    GenerationEvent,
    GenerationRequest,
    TextDelta,
)
from nexus_api.llm.pricing import TokenUsage
from nexus_api.main import create_app
from tests.conftest import wait_until, wait_until_accepting
from tests.fakes import HashEmbedder, ScriptedGenerator, cited_answer, make_pdf

pytestmark = pytest.mark.integration

Event = tuple[str, dict[str, Any]]
Serve = Callable[..., AbstractAsyncContextManager[tuple[FastAPI, httpx.AsyncClient]]]


async def _fetch(settings: Settings, query: str, *args: object) -> Any:
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


@pytest.fixture(autouse=True)
async def _clean(migrated_settings: Settings) -> None:
    await _fetch(migrated_settings, "TRUNCATE documents, conversations, llm_usage CASCADE")


@pytest.fixture
def serve(migrated_settings: Settings) -> Serve:
    """``async with serve(generator, **settings) as (app, client)`` on a real server."""

    @asynccontextmanager
    async def factory(
        generator: ScriptedGenerator | None, **overrides: Any
    ) -> AsyncIterator[tuple[FastAPI, httpx.AsyncClient]]:
        settings = migrated_settings.model_copy(update=overrides)
        app = create_app(settings, embedder=HashEmbedder(), answer_generator=generator)
        server = uvicorn.Server(
            uvicorn.Config(app, host="127.0.0.1", port=0, log_config=None, lifespan="on")
        )
        task = asyncio.create_task(server.serve())
        try:
            await wait_until(lambda: server.started)
            await wait_until_accepting(app)
            port = server.servers[0].sockets[0].getsockname()[1]
            async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}", timeout=20) as client:
                yield app, client
        finally:
            server.should_exit = True
            await task

    return factory


async def _ingest(client: httpx.AsyncClient, *pages: str) -> str:
    data = make_pdf([[line] for line in pages] + [[f"marker{uuid.uuid4().hex[:8]}"]])
    response = await client.post(
        "/api/v1/documents", files={"file": ("handbook.pdf", data, "application/pdf")}
    )
    document_id: str = response.json()["id"]
    async with asyncio.timeout(15):
        # Polling another component's state over HTTP; there is no event to await.
        while (await client.get(f"/api/v1/documents/{document_id}")).json()[  # noqa: ASYNC110
            "status"
        ] != "ready":
            await asyncio.sleep(0.02)
    return document_id


async def _conversation(client: httpx.AsyncClient) -> str:
    response = await client.post("/api/v1/conversations", json={})
    assert response.status_code == 201
    conversation_id: str = response.json()["id"]
    return conversation_id


def _parse(lines: list[str]) -> list[Event]:
    events: list[Event] = []
    name: str = "message"
    data: list[str] = []
    for line in lines:
        if line == "":
            if data:
                events.append((name, json.loads("\n".join(data))))
            name, data = "message", []
        elif line.startswith(":"):
            continue
        else:
            key, _, value = line.partition(":")
            value = value.removeprefix(" ")
            if key == "event":
                name = value
            elif key == "data":
                data.append(value)
    return events


async def _ask(
    client: httpx.AsyncClient, conversation_id: str, question: str, **body: Any
) -> tuple[int, list[Event], dict[str, Any] | None]:
    async with client.stream(
        "POST",
        f"/api/v1/conversations/{conversation_id}/messages",
        json={"content": question, **body},
    ) as response:
        if response.headers.get("content-type", "").startswith("application/json"):
            return response.status_code, [], json.loads(await response.aread())
        lines = [line async for line in response.aiter_lines()]
    return response.status_code, _parse([*lines, ""]), None


async def _messages(client: httpx.AsyncClient, conversation_id: str) -> list[dict[str, Any]]:
    detail = (await client.get(f"/api/v1/conversations/{conversation_id}")).json()
    messages: list[dict[str, Any]] = detail["messages"]
    return messages


# --- happy path, citations, persistence ---------------------------------------------------


async def test_grounded_answer_streams_with_validated_page_citations(serve: Serve) -> None:
    generator = ScriptedGenerator(cited_answer)
    async with serve(generator) as (_, client):
        document_id = await _ingest(client, "Glaciers carve valleys.", "Volcanoes erupt ash.")
        conversation_id = await _conversation(client)
        status, events, _ = await _ask(client, conversation_id, "What do glaciers do?")
        messages = await _messages(client, conversation_id)
        conversation = (await client.get(f"/api/v1/conversations/{conversation_id}")).json()

    assert status == 200
    names = [name for name, _ in events]
    assert names[0] == "meta"
    assert names[-1] == "done"
    assert "citation" in names
    meta = events[0][1]
    assert meta["sources"][0]["document_id"] == document_id

    citation = next(data for name, data in events if name == "citation")
    request = generator.requests[0]
    cited_passage = request.sources[0].passages[0]
    assert citation["number"] == 1
    assert citation["document_id"] == document_id
    assert citation["filename"] == "handbook.pdf"
    assert citation["page_start"] == citation["page_end"]
    assert citation["text"] == cited_passage, "shown text is exactly the passage that was sent"

    text = "".join(data["text"] for name, data in events if name == "delta")
    assert text == "Here is the answer. It is stated in the document.[1](#cite-1)"
    done = events[-1][1]
    assert done["status"] == "complete"
    assert done["grounded"] is True
    assert done["usage"] == {
        "input_tokens": 1000,
        "cache_creation_tokens": 0,
        "cache_read_tokens": 700,
        "output_tokens": 120,
    }
    assert done["cost_usd"] == pytest.approx((1000 * 2 + 700 * 0.10 + 120 * 10) / 1e6)

    user, assistant = messages
    assert (user["role"], user["content"]) == ("user", "What do glaciers do?")
    assert assistant["content"] == text
    assert assistant["citations"][0]["page_start"] == citation["page_start"]
    assert assistant["status"] == "complete"
    assert conversation["title"] == "What do glaciers do?"


async def test_no_retrieved_passages_means_no_api_call(serve: Serve) -> None:
    generator = ScriptedGenerator(cited_answer)
    async with serve(generator) as (_, client):
        conversation_id = await _conversation(client)  # no documents at all
        status, events, _ = await _ask(client, conversation_id, "Anything?")
        messages = await _messages(client, conversation_id)

    assert status == 200
    assert generator.requests == []
    assert events[-1][1]["status"] == "no_context"
    assert events[-1][1]["cost_usd"] == 0.0
    assert messages[1]["status"] == "no_context"
    assert "couldn't find" in messages[1]["content"]


async def test_invalid_citations_from_the_model_are_never_shown(serve: Serve) -> None:
    def script(_: GenerationRequest) -> list[GenerationEvent | Exception]:
        return [
            CitationRef(block=0, search_result_index=9, start_block_index=0, end_block_index=1),
            CitationRef(block=0, search_result_index=0, start_block_index=0, end_block_index=99),
            TextDelta(text="Unsupported claim.", block=0),
            Completed("end_turn", TokenUsage(output=5), "claude-sonnet-5-5"),
        ]

    async with serve(ScriptedGenerator(script)) as (_, client):
        await _ingest(client, "Some content.")
        conversation_id = await _conversation(client)
        _, events, _ = await _ask(client, conversation_id, "Question?")

    assert [name for name, _ in events if name == "citation"] == []
    assert events[-1][1]["grounded"] is False
    assert "".join(d["text"] for n, d in events if n == "delta") == "Unsupported claim."


async def test_follow_up_sends_completed_history_without_markers(serve: Serve) -> None:
    generator = ScriptedGenerator(cited_answer)
    async with serve(generator) as (_, client):
        await _ingest(client, "Data retention is three years.")
        conversation_id = await _conversation(client)
        await _ask(client, conversation_id, "How long is data kept?")
        await _ask(client, conversation_id, "And after that?")

    second = generator.requests[1]
    assert [(t.question, t.answer) for t in second.history] == [
        ("How long is data kept?", "Here is the answer. It is stated in the document.")
    ]
    assert second.question == "And after that?"


# --- outcomes: refusal, truncation, failure, cancellation ----------------------------------


@pytest.mark.parametrize(
    ("stop_reason", "category", "expected"),
    [("refusal", "cyber", "refused"), ("max_tokens", None, "truncated")],
)
async def test_stop_reasons_are_stored_transparently(
    serve: Serve, stop_reason: str, category: str | None, expected: str
) -> None:
    def script(_: GenerationRequest) -> list[GenerationEvent | Exception]:
        return [
            TextDelta(text="Partial", block=0),
            Completed(
                stop_reason, TokenUsage(input_uncached=10, output=5), "claude-sonnet-5-5", category
            ),
        ]

    async with serve(ScriptedGenerator(script)) as (_, client):
        await _ingest(client, "Content.")
        conversation_id = await _conversation(client)
        _, events, _ = await _ask(client, conversation_id, "Question?")
        messages = await _messages(client, conversation_id)

    assert events[-1][1]["status"] == expected
    assert events[-1][1]["stop_category"] == category
    assert messages[1]["status"] == expected
    assert messages[1]["content"] == "Partial"


async def test_failure_mid_stream_keeps_partial_text_and_reports_an_error_event(
    serve: Serve, migrated_settings: Settings
) -> None:
    def script(_: GenerationRequest) -> list[GenerationEvent | Exception]:
        return [
            TextDelta(text="Partial answer", block=0),
            GenerationError(
                "llm_unavailable", "The Anthropic API is temporarily unavailable.", retryable=True
            ),
        ]

    async with serve(ScriptedGenerator(script)) as (app, client):
        await _ingest(client, "Content.")
        conversation_id = await _conversation(client)
        status, events, _ = await _ask(client, conversation_id, "Question?")
        messages = await _messages(client, conversation_id)
        llm_status = (await client.get("/api/v1/llm/status")).json()
        await wait_until(lambda: app.state.chat_service.slots.active == 0)

    assert status == 200
    assert events[-1] == (
        "error",
        {
            "message_id": messages[1]["id"],
            "code": "llm_unavailable",
            "message": "The Anthropic API is temporarily unavailable.",
            "retryable": True,
            "retry_after": None,
        },
    )
    assert messages[1]["status"] == "failed"
    assert messages[1]["content"] == "Partial answer"
    assert messages[1]["cost_usd"] is None
    # Unknown usage: the reservation keeps counting against the budget.
    reserved = await _fetch(
        migrated_settings, "SELECT reserved_cost_usd FROM messages WHERE role = 'assistant'"
    )
    assert llm_status["spent_today_usd"] == pytest.approx(float(reserved))


async def test_client_disconnect_cancels_upstream_and_keeps_partial_text(serve: Serve) -> None:
    hold = asyncio.Event()

    def script(_: GenerationRequest) -> list[GenerationEvent | Exception]:
        return [TextDelta(text="First words", block=0), TextDelta(text=" never sent", block=0)]

    generator = ScriptedGenerator(script, hold=hold)
    async with serve(generator) as (app, client):
        await _ingest(client, "Content.")
        conversation_id = await _conversation(client)
        async with client.stream(
            "POST", f"/api/v1/conversations/{conversation_id}/messages", json={"content": "Q?"}
        ) as response:
            async for line in response.aiter_lines():
                if "First words" in line:
                    break  # leaving the block closes the connection mid-stream
        await wait_until(lambda: generator.closed_early == 1)
        await wait_until(lambda: app.state.chat_service.slots.active == 0)
        messages = await _messages(client, conversation_id)

    assert messages[1]["status"] == "cancelled"
    assert messages[1]["content"] == "First words"
    assert generator.closed_early == 1, "the upstream stream was closed (no further billing)"


async def test_closing_the_event_stream_records_cancellation_and_closes_upstream(
    serve: Serve,
) -> None:
    """The other shutdown path: the response closes the generator (aclose) instead of
    cancelling its task. The inner generators must still run their cleanup."""
    hold = asyncio.Event()
    generator = ScriptedGenerator(
        lambda _: [TextDelta(text="Only this", block=0), TextDelta(text=" not this", block=0)],
        hold=hold,
    )
    async with serve(generator) as (app, client):
        await _ingest(client, "Content.")
        conversation_id = await _conversation(client)
        chat = app.state.chat_service
        prepared = await chat.prepare(uuid.UUID(conversation_id), "Q?", None)
        events = chat.stream(prepared)
        names = [(await anext(events))["event"], (await anext(events))["event"]]
        await events.aclose()
        messages = await _messages(client, conversation_id)

    assert names == ["meta", "delta"]
    assert generator.closed_early == 1, "upstream generator closed"
    assert chat.slots.active == 0, "generation slot released"
    assert (messages[1]["status"], messages[1]["content"]) == ("cancelled", "Only this")


def _asgi_send_failing_at(fail_at: int) -> tuple[Any, list[dict[str, Any]]]:
    """An ASGI ``send`` that raises (client gone) on message number ``fail_at``."""
    sent: list[dict[str, Any]] = []

    async def send(message: dict[str, Any]) -> None:
        if len(sent) == fail_at:
            raise OSError("client went away")
        sent.append(message)

    return send, sent


async def _never_disconnect() -> dict[str, Any]:
    await asyncio.Event().wait()
    raise AssertionError("unreachable")


_SCOPE = {"type": "http", "method": "POST", "path": "/", "headers": []}


async def test_response_that_never_starts_frees_the_slot_and_the_conversation(
    serve: Serve,
) -> None:
    """Regression: the client is gone before the first byte, so the SSE generator is
    never started and runs no ``finally``. The response must still clean up."""
    generator = ScriptedGenerator(cited_answer)
    async with serve(generator) as (app, client):
        await _ingest(client, "Content.")
        conversation_id = await _conversation(client)
        chat = app.state.chat_service
        prepared = await chat.prepare(uuid.UUID(conversation_id), "Q?", None)
        assert chat.slots.active == 1
        send, _ = _asgi_send_failing_at(0)  # http.response.start fails
        with pytest.raises(OSError, match="client went away"):
            await answer_response(chat, prepared)(_SCOPE, _never_disconnect, send)
        messages = await _messages(client, conversation_id)
        status, _, _ = await _ask(client, conversation_id, "Again?")  # not 409 busy

    assert chat.slots.active == 0, "generation slot released"
    assert len(generator.requests) == 1, "Claude is only called for 'Again?'"
    assert (messages[1]["status"], messages[1]["cost_usd"]) == ("cancelled", 0.0)
    assert status == 200


async def test_send_failure_mid_stream_closes_the_answer_through_the_route(
    serve: Serve,
) -> None:
    """Regression: sse-starlette closes the route's iterator (aclose) when a send
    fails; the close must reach the chat stream and the upstream generator."""
    hold = asyncio.Event()
    generator = ScriptedGenerator(
        lambda _: [TextDelta(text="Only this", block=0), TextDelta(text=" not this", block=0)],
        hold=hold,
    )
    async with serve(generator) as (app, client):
        await _ingest(client, "Content.")
        conversation_id = await _conversation(client)
        chat = app.state.chat_service
        prepared = await chat.prepare(uuid.UUID(conversation_id), "Q?", None)
        send, sent = _asgi_send_failing_at(2)  # start and meta sent, first delta fails
        with pytest.raises(OSError, match="client went away"):
            await answer_response(chat, prepared)(_SCOPE, _never_disconnect, send)
        messages = await _messages(client, conversation_id)

    assert [m["type"] for m in sent] == ["http.response.start", "http.response.body"]
    assert chat.slots.active == 0, "generation slot released"
    assert generator.closed_early == 1, "upstream generator closed"
    assert (messages[1]["status"], messages[1]["content"]) == ("cancelled", "Only this")


async def test_no_database_connection_is_held_while_generating(
    serve: Serve, migrated_settings: Settings
) -> None:
    hold = asyncio.Event()
    generator = ScriptedGenerator(cited_answer, hold=hold)
    async with serve(generator) as (app, client):
        await _ingest(client, "Content.")
        conversation_id = await _conversation(client)
        ask = asyncio.create_task(_ask(client, conversation_id, "Q?"))
        await asyncio.wait_for(generator.started.wait(), 10)
        checked_out = app.state.engine.pool.checkedout()
        in_transaction = await _fetch(
            migrated_settings,
            "SELECT count(*) FROM pg_stat_activity WHERE application_name = 'nexus-api'"
            " AND state LIKE 'idle in transaction%'",
        )
        hold.set()
        await ask

    assert checked_out == 0
    assert in_transaction == 0


# --- admission: configuration, concurrency, budget -------------------------------------------


async def test_without_an_api_key_chat_is_disabled_but_search_works(serve: Serve) -> None:
    async with serve(None) as (_, client):
        await _ingest(client, "Content.")
        conversation_id = await _conversation(client)
        status, _, error = await _ask(client, conversation_id, "Q?")
        llm_status = (await client.get("/api/v1/llm/status")).json()
        search = await client.post("/api/v1/search", json={"query": "content"})

    assert status == 503
    assert error is not None
    assert error["error"]["code"] == "llm_not_configured"
    assert llm_status["configured"] is False
    assert search.status_code == 200


async def test_concurrency_limit_rejects_with_429(serve: Serve) -> None:
    hold = asyncio.Event()
    generator = ScriptedGenerator(cited_answer, hold=hold)
    async with serve(generator, llm_max_concurrent_generations=1) as (_, client):
        await _ingest(client, "Content.")
        first_conversation = await _conversation(client)
        second_conversation = await _conversation(client)
        first = asyncio.create_task(_ask(client, first_conversation, "Q1?"))
        await asyncio.wait_for(generator.started.wait(), 10)
        status, _, error = await _ask(client, second_conversation, "Q2?")
        hold.set()
        await first

    assert status == 429
    assert error is not None
    assert error["error"]["code"] == "llm_busy"


async def test_same_conversation_cannot_generate_twice_at_once(serve: Serve) -> None:
    hold = asyncio.Event()
    generator = ScriptedGenerator(cited_answer, hold=hold)
    async with serve(generator) as (_, client):
        await _ingest(client, "Content.")
        conversation_id = await _conversation(client)
        first = asyncio.create_task(_ask(client, conversation_id, "Q1?"))
        await asyncio.wait_for(generator.started.wait(), 10)
        status, _, error = await _ask(client, conversation_id, "Q2?")
        delete = await client.delete(f"/api/v1/conversations/{conversation_id}")
        hold.set()
        await first

    assert status == 409
    assert error is not None
    assert error["error"]["code"] == "conversation_busy"
    assert delete.status_code == 409


async def test_exhausted_budget_rejects_before_calling_the_api(serve: Serve) -> None:
    generator = ScriptedGenerator(cited_answer)
    async with serve(generator, llm_daily_budget_usd=0.0001) as (_, client):
        await _ingest(client, "Content.")
        conversation_id = await _conversation(client)
        status, _, error = await _ask(client, conversation_id, "Q?")
        messages = await _messages(client, conversation_id)

    assert status == 429
    assert error is not None
    assert error["error"]["code"] == "llm_budget_exceeded"
    assert generator.requests == []
    assert messages == [], "nothing is stored for a rejected question"


async def test_concurrent_admissions_cannot_overspend_the_budget(
    migrated_settings: Settings,
) -> None:
    """Six simultaneous admissions; the budget fits exactly two reservations.

    Without the advisory lock, every request would read the same "spent so far"
    before any of them inserted its reservation, and all six would be admitted.
    """
    from nexus_api.db.engine import create_engine, create_sessionmaker
    from nexus_api.rag import repository

    engine = create_engine(migrated_settings)
    sessionmaker = create_sessionmaker(engine)
    try:
        conversation_ids = [uuid.uuid4() for _ in range(6)]
        for conversation_id in conversation_ids:
            await _fetch(
                migrated_settings,
                "INSERT INTO conversations (id, title) VALUES ($1, 'race')",
                conversation_id,
            )

        async def admit(conversation_id: uuid.UUID) -> str:
            try:
                await repository.admit_generation(
                    sessionmaker,
                    conversation_id=conversation_id,
                    question="Q?",
                    model="claude-sonnet-5-5",
                    reservation_usd=Decimal("0.40"),
                    budget_usd=Decimal("1.00"),
                    retrieved=[],
                )
            except repository.BudgetExceededError:
                return "rejected"
            return "admitted"

        outcomes = await asyncio.gather(*(admit(c) for c in conversation_ids))
        reserved = await _fetch(
            migrated_settings,
            "SELECT sum(reserved_cost_usd) FROM llm_usage",
        )
    finally:
        await engine.dispose()

    assert sorted(outcomes) == ["admitted", "admitted"] + ["rejected"] * 4
    assert Decimal(reserved) == Decimal("0.80")


async def test_deleting_conversations_does_not_reopen_the_budget(serve: Serve) -> None:
    """Regression: spend was summed over messages, so deleting a conversation deleted
    its spend. The ledger keeps it."""
    generator = ScriptedGenerator(cited_answer)
    async with serve(generator) as (_, client):
        await _ingest(client, "Content.")
        conversation_id = await _conversation(client)
        await _ask(client, conversation_id, "Q?")
        before = (await client.get("/api/v1/llm/status")).json()["spent_today_usd"]
        deleted = await client.delete(f"/api/v1/conversations/{conversation_id}")
        after = (await client.get("/api/v1/llm/status")).json()["spent_today_usd"]

    assert deleted.status_code == 204
    assert before > 0
    assert after == pytest.approx(before)


async def test_question_without_passages_waits_for_a_streaming_answer(serve: Serve) -> None:
    """Regression: the no-context path skipped the busy check, so a turn could
    interleave with an answer still streaming in the same conversation."""
    hold = asyncio.Event()
    generator = ScriptedGenerator(cited_answer, hold=hold)
    async with serve(generator) as (_, client):
        await _ingest(client, "Content.")
        conversation_id = await _conversation(client)
        first = asyncio.create_task(_ask(client, conversation_id, "Q?"))
        await asyncio.wait_for(generator.started.wait(), 10)
        status, _, error = await _ask(
            client, conversation_id, "Elsewhere?", document_ids=[str(uuid.uuid4())]
        )
        hold.set()
        await first

    assert status == 409
    assert error is not None
    assert error["error"]["code"] == "conversation_busy"


# --- recovery -------------------------------------------------------------------------------------


async def test_startup_marks_interrupted_generations_and_keeps_their_reservation(
    serve: Serve, migrated_settings: Settings
) -> None:
    conversation_id, message_id = uuid.uuid4(), uuid.uuid4()
    await _fetch(
        migrated_settings,
        "WITH c AS (INSERT INTO conversations (id, title) VALUES ($1, 'Crashed') RETURNING id)"
        " INSERT INTO messages (id, conversation_id, role, content, status, citations, retrieved,"
        " reserved_cost_usd) SELECT $2, id, 'assistant', '', 'streaming', '[]', '[]', 0.05 FROM c",
        conversation_id,
        message_id,
    )
    await _fetch(
        migrated_settings,
        "INSERT INTO llm_usage (id, model, reserved_cost_usd)"
        " VALUES ($1, 'claude-sonnet-5-5', 0.05)",
        message_id,
    )
    generator = ScriptedGenerator(cited_answer)
    async with serve(generator) as (_, client):
        messages = await _messages(client, str(conversation_id))
        llm_status = (await client.get("/api/v1/llm/status")).json()
        await _ingest(client, "Content.")
        status, events, _ = await _ask(client, str(conversation_id), "Ask again?")

    assert messages[0]["status"] == "interrupted"
    assert messages[0]["error_code"] == "interrupted"
    assert llm_status["spent_today_usd"] == pytest.approx(0.05), "the reservation still counts"
    assert status == 200, "an interrupted conversation can continue"
    assert events[-1][1]["status"] == "complete"


async def test_conversation_lifecycle(serve: Serve) -> None:
    async with serve(ScriptedGenerator(cited_answer)) as (_, client):
        created = (await client.post("/api/v1/conversations", json={"title": "My topic"})).json()
        listed = (await client.get("/api/v1/conversations")).json()
        missing = await client.get(f"/api/v1/conversations/{uuid.uuid4()}")
        deleted = await client.delete(f"/api/v1/conversations/{created['id']}")
        gone = await client.get(f"/api/v1/conversations/{created['id']}")
        ask_missing = await client.post(
            f"/api/v1/conversations/{uuid.uuid4()}/messages", json={"content": "Q?"}
        )

    assert created["title"] == "My topic"
    assert [c["id"] for c in listed] == [created["id"]]
    assert missing.status_code == 404
    assert deleted.status_code == 204
    assert gone.json()["error"]["code"] == "conversation_not_found"
    assert ask_missing.status_code == 404
