"""Contract tests: ``ClaudeAnswerGenerator`` against Claude's streaming wire format.

The real adapter (ChatAnthropic + the Anthropic SDK) is exercised end to end, with
an ``httpx2`` mock transport serving Server-Sent Events shaped like the Messages
API's (message_start, content_block_*, citations_delta, message_delta with
cumulative usage, message_stop, error). No network, no API key.

The fixtures follow the documented event schema; after a live run (``-m llm``) they
can be checked against recorded responses.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Callable
from typing import Any

import httpx2
import pytest

from nexus_api.llm.claude import ClaudeAnswerGenerator
from nexus_api.llm.generator import (
    CitationRef,
    Completed,
    GenerationError,
    GenerationEvent,
    GenerationRequest,
    HistoryTurn,
    SourceDocument,
    TextDelta,
)
from nexus_api.llm.pricing import TokenUsage
from tests.conftest import SettingsFactory

REQUEST = GenerationRequest(
    system="SYSTEM PROMPT",
    history=[HistoryTurn("Earlier question?", "Earlier answer.")],
    sources=[
        SourceDocument(
            "nexus://documents/a", "handbook.pdf", ["Intro text.", "Data is kept 3 years."]
        ),
        SourceDocument("nexus://documents/b", "policy.pdf", ["Other text."]),
    ],
    question="How long is data kept?",
    max_output_tokens=2048,
)

CITATION = {
    "type": "search_result_location",
    "cited_text": "Data is kept 3 years.",
    "source": "nexus://documents/a",
    "title": "handbook.pdf",
    "search_result_index": 0,
    "start_block_index": 1,
    "end_block_index": 2,
}


def _sse(events: list[dict[str, Any]]) -> bytes:
    return b"".join(f"event: {e['type']}\ndata: {json.dumps(e)}\n\n".encode() for e in events)


def _message(
    stop_reason: str = "end_turn", stop_details: dict[str, Any] | None = None
) -> list[dict[str, Any]]:
    usage = {"input_tokens": 300, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 700}
    return [
        {
            "type": "message_start",
            "message": {
                "id": "msg_test",
                "type": "message",
                "role": "assistant",
                "model": "claude-sonnet-5-5",
                "content": [],
                "stop_reason": None,
                "stop_sequence": None,
                "usage": {**usage, "output_tokens": 1},
            },
        },
        {
            "type": "content_block_start",
            "index": 0,
            "content_block": {"type": "thinking", "thinking": "", "signature": ""},
        },
        {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "signature_delta", "signature": "sig"},
        },
        {"type": "content_block_stop", "index": 0},
        {
            "type": "content_block_start",
            "index": 1,
            "content_block": {"type": "text", "text": "", "citations": None},
        },
        {
            "type": "content_block_delta",
            "index": 1,
            "delta": {"type": "text_delta", "text": "Short answer. "},
        },
        {"type": "content_block_stop", "index": 1},
        {
            "type": "content_block_start",
            "index": 2,
            "content_block": {"type": "text", "text": "", "citations": []},
        },
        {
            "type": "content_block_delta",
            "index": 2,
            "delta": {"type": "citations_delta", "citation": CITATION},
        },
        {
            "type": "content_block_delta",
            "index": 2,
            "delta": {"type": "text_delta", "text": "Three years."},
        },
        {"type": "content_block_stop", "index": 2},
        {
            "type": "message_delta",
            "delta": {
                "stop_reason": stop_reason,
                "stop_sequence": None,
                "stop_details": stop_details,
            },
            "usage": {**usage, "output_tokens": 42},
        },
        {"type": "message_stop"},
    ]


Handler = Callable[[httpx2.Request], httpx2.Response]


def _generator(make_settings: SettingsFactory, handler: Handler) -> ClaudeAnswerGenerator:
    settings = make_settings(anthropic_api_key="sk-ant-unit-test", llm_max_retries=0)
    client = httpx2.AsyncClient(
        transport=httpx2.MockTransport(handler), base_url="https://api.anthropic.com"
    )
    return ClaudeAnswerGenerator(settings, http_client=client)


def _stream_handler(
    events: list[dict[str, Any]], captured: list[dict[str, Any]] | None = None
) -> Handler:
    def handler(request: httpx2.Request) -> httpx2.Response:
        if captured is not None:
            captured.append({"headers": dict(request.headers), "body": json.loads(request.content)})
        return httpx2.Response(
            200, headers={"content-type": "text/event-stream"}, content=_sse(events)
        )

    return handler


async def _collect(stream: AsyncIterator[GenerationEvent]) -> list[GenerationEvent]:
    return [event async for event in stream]


async def test_request_layout_supports_caching_and_citations(
    make_settings: SettingsFactory,
) -> None:
    captured: list[dict[str, Any]] = []
    await _collect(_generator(make_settings, _stream_handler(_message(), captured)).stream(REQUEST))
    body, headers = captured[0]["body"], captured[0]["headers"]

    assert body["model"] == "claude-sonnet-5-5"
    assert body["max_tokens"] == 2048
    assert body["stream"] is True
    assert body["output_config"] == {"effort": "low"}
    assert body["thinking"] == {"type": "adaptive"}
    assert not {"temperature", "top_p", "top_k"} & body.keys(), "rejected on API 2026-10-21"
    # Prefix order: system (breakpoint) -> history (breakpoint on last turn) -> current turn.
    assert body["system"] == [
        {"type": "text", "text": "SYSTEM PROMPT", "cache_control": {"type": "ephemeral"}}
    ]
    roles = [m["role"] for m in body["messages"]]
    assert roles == ["user", "assistant", "user"]
    assert body["messages"][1]["content"][-1]["cache_control"] == {"type": "ephemeral"}
    current = body["messages"][-1]["content"]
    assert [block["type"] for block in current] == ["search_result", "search_result", "text"]
    assert current[0] == {
        "type": "search_result",
        "source": "nexus://documents/a",
        "title": "handbook.pdf",
        "content": [
            {"type": "text", "text": "Intro text."},
            {"type": "text", "text": "Data is kept 3 years."},
        ],
        "citations": {"enabled": True},
    }
    assert "cache_control" not in json.dumps(current), "the unique tail is never cached"
    assert current[-1]["text"] == "How long is data kept?"
    # anthropic SDK 2.x sends the key as a bearer token (not x-api-key).
    assert headers["authorization"] == "Bearer sk-ant-unit-test"


async def test_stream_is_translated_into_text_citations_and_usage(
    make_settings: SettingsFactory,
) -> None:
    events = await _collect(_generator(make_settings, _stream_handler(_message())).stream(REQUEST))

    texts = [e for e in events if isinstance(e, TextDelta)]
    citations = [e for e in events if isinstance(e, CitationRef)]
    completed = events[-1]
    assert [(t.text, t.block) for t in texts] == [("Short answer. ", 1), ("Three years.", 2)]
    assert citations == [
        CitationRef(block=2, search_result_index=0, start_block_index=1, end_block_index=2)
    ]
    assert isinstance(completed, Completed)
    assert completed.stop_reason == "end_turn"
    assert completed.model == "claude-sonnet-5-5"
    assert completed.usage == TokenUsage(
        input_uncached=300, cache_creation=0, cache_read=700, output=42
    )


@pytest.mark.parametrize(
    ("stop_reason", "details", "category"),
    [
        ("max_tokens", None, None),
        ("refusal", {"type": "refusal", "category": "cyber", "explanation": None}, "cyber"),
    ],
)
async def test_stop_reasons_are_reported(
    make_settings: SettingsFactory,
    stop_reason: str,
    details: dict[str, Any] | None,
    category: str | None,
) -> None:
    events = await _collect(
        _generator(make_settings, _stream_handler(_message(stop_reason, details))).stream(REQUEST)
    )
    completed = events[-1]

    assert isinstance(completed, Completed)
    assert completed.stop_reason == stop_reason
    assert completed.stop_category == category


def _error_handler(status: int, error_type: str, retry_after: str | None = None) -> Handler:
    def handler(_: httpx2.Request) -> httpx2.Response:
        headers = {"retry-after": retry_after} if retry_after else {}
        return httpx2.Response(
            status,
            headers=headers,
            json={"type": "error", "error": {"type": error_type, "message": "upstream detail"}},
        )

    return handler


@pytest.mark.parametrize(
    ("status", "error_type", "code", "retryable", "retry_after"),
    [
        (401, "authentication_error", "llm_not_configured", False, None),
        (403, "permission_error", "llm_not_configured", False, None),
        (429, "rate_limit_error", "llm_rate_limited", True, 7),
        (529, "overloaded_error", "llm_unavailable", True, None),
        (500, "api_error", "llm_unavailable", True, None),
        (400, "invalid_request_error", "llm_request_failed", False, None),
    ],
)
async def test_http_errors_map_to_stable_codes(
    make_settings: SettingsFactory,
    status: int,
    error_type: str,
    code: str,
    retryable: bool,
    retry_after: int | None,
) -> None:
    generator = _generator(
        make_settings, _error_handler(status, error_type, "7" if status == 429 else None)
    )
    with pytest.raises(GenerationError) as excinfo:
        await _collect(generator.stream(REQUEST))

    assert (excinfo.value.code, excinfo.value.retryable, excinfo.value.retry_after) == (
        code,
        retryable,
        retry_after,
    )
    assert "upstream detail" not in excinfo.value.message, "API error bodies are never forwarded"


async def test_error_inside_a_started_stream_is_retryable(make_settings: SettingsFactory) -> None:
    events = [
        *_message()[:6],
        {"type": "error", "error": {"type": "overloaded_error", "message": "Overloaded"}},
    ]
    received: list[GenerationEvent] = []

    async def consume() -> None:
        async for event in _generator(make_settings, _stream_handler(events)).stream(REQUEST):
            received.append(event)

    with pytest.raises(GenerationError) as excinfo:
        await consume()

    assert excinfo.value.code == "llm_unavailable"
    assert excinfo.value.retryable
    assert [e.text for e in received if isinstance(e, TextDelta)] == ["Short answer. "], (
        "partial text arrives first"
    )


async def test_cancelling_the_consumer_closes_the_upstream_response(
    make_settings: SettingsFactory,
) -> None:
    state = {"sent": 0, "closed": False}

    class Trickle(httpx2.AsyncByteStream):
        async def __aiter__(self) -> AsyncIterator[bytes]:
            for event in _message()[:6]:
                yield _sse([event])
            while True:
                state["sent"] += 1
                delta = {
                    "type": "content_block_delta",
                    "index": 1,
                    "delta": {"type": "text_delta", "text": "x"},
                }
                yield _sse([delta])
                await asyncio.sleep(0.005)

        async def aclose(self) -> None:
            state["closed"] = True

    def handler(_: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, headers={"content-type": "text/event-stream"}, stream=Trickle())

    async def consume() -> None:
        async for _ in _generator(make_settings, handler).stream(REQUEST):
            pass

    task = asyncio.create_task(consume())
    await asyncio.sleep(0.1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    sent_at_cancel = state["sent"]
    await asyncio.sleep(0.1)

    assert state["closed"], "the HTTP response must be closed: no further tokens are billed"
    assert state["sent"] == sent_at_cancel


def test_requires_an_api_key(make_settings: SettingsFactory) -> None:
    with pytest.raises(ValueError, match="ANTHROPIC_API_KEY"):
        ClaudeAnswerGenerator(make_settings())
