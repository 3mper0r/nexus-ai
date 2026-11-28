"""Claude answer generator via LangChain's ``ChatAnthropic`` (ADR 0016).

Capability spike (docs/milestones/m2-plan.md): ``search_result`` blocks pass through
unchanged, streamed ``citations_delta`` events are merged into text blocks, usage
(including cache reads/writes) arrives with ``message_delta``, ``stop_reason`` and
refusal ``stop_details`` are exposed, and Anthropic's typed exceptions propagate.

Request layout (ADR 0018), in prompt-prefix order:
    system (cache breakpoint) -> history Q&A text (breakpoint on the last turn)
    -> current user turn: search_result blocks + question (never cached: unique)
"""

from __future__ import annotations

from collections.abc import AsyncGenerator, AsyncIterator, Sequence
from contextlib import aclosing
from typing import Any, cast

import anthropic
import httpx2
import structlog
from langchain_anthropic import ChatAnthropic
from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage, HumanMessage
from langchain_core.messages import SystemMessage as LcSystemMessage

from nexus_api.core.config import Settings
from nexus_api.llm.generator import (
    CitationRef,
    Completed,
    GenerationError,
    GenerationEvent,
    GenerationRequest,
    SourceDocument,
    TextDelta,
)
from nexus_api.llm.pricing import TokenUsage

logger = structlog.stdlib.get_logger(__name__)

_CACHE = {"type": "ephemeral"}


def search_result_block(document: SourceDocument) -> dict[str, Any]:
    return {
        "type": "search_result",
        "source": document.source,
        "title": document.title,
        "content": [{"type": "text", "text": passage} for passage in document.passages],
        "citations": {"enabled": True},
    }


def build_messages(request: GenerationRequest) -> list[BaseMessage]:
    messages: list[BaseMessage] = [
        LcSystemMessage(content=[{"type": "text", "text": request.system, "cache_control": _CACHE}])
    ]
    for position, turn in enumerate(request.history):
        last = position == len(request.history) - 1
        messages.append(HumanMessage(content=turn.question))
        answer_block: dict[str, Any] = {"type": "text", "text": turn.answer}
        if last:
            # Read point for the next turn: everything up to here is a stable prefix.
            answer_block["cache_control"] = _CACHE
        messages.append(AIMessage(content=[answer_block]))
    current: list[str | dict[str, Any]] = [search_result_block(d) for d in request.sources]
    current.append({"type": "text", "text": request.question})
    messages.append(HumanMessage(content=current))
    return messages


class ClaudeAnswerGenerator:
    def __init__(
        self, settings: Settings, *, http_client: httpx2.AsyncClient | None = None
    ) -> None:
        if not settings.llm_configured or settings.anthropic_api_key is None:
            raise ValueError("ANTHROPIC_API_KEY is not configured")
        self._model_name = settings.llm_model
        # Own the connection pool: ChatAnthropic would otherwise create one per model
        # that is only closed by a best-effort __del__, leaking the TLS connection to
        # api.anthropic.com at shutdown. An injected client stays the caller's.
        self._owns_client = http_client is None
        self._http_client = (
            anthropic.DefaultAsyncHttpxClient() if http_client is None else http_client
        )
        self._model = ChatAnthropic(
            model=settings.llm_model,
            anthropic_api_key=settings.anthropic_api_key,
            max_tokens=settings.llm_max_output_tokens,
            reasoning_effort=settings.llm_effort,
            # Explicit: ChatAnthropic would otherwise add display="summarized" when
            # effort is set. We never show reasoning, so it is not streamed.
            thinking={"type": "adaptive"},
            max_retries=settings.llm_max_retries,
            default_request_timeout=settings.llm_timeout_seconds,
            http_async_client=self._http_client,
        )

    async def aclose(self) -> None:
        """Close the HTTP connection pool (application shutdown). Idempotent."""
        if self._owns_client and not self._http_client.is_closed:
            await self._http_client.aclose()

    async def stream(self, request: GenerationRequest) -> AsyncIterator[GenerationEvent]:
        messages = build_messages(request)
        stop_reason: str | None = None
        stop_category: str | None = None
        model = self._model_name
        usage = TokenUsage()
        stream = cast(
            "AsyncGenerator[AIMessageChunk, None]",
            self._model.astream(messages, max_tokens=request.max_output_tokens),
        )
        try:
            # aclosing: when our consumer is cancelled (client disconnect), the
            # LangChain stream and with it the HTTP response are closed at once, so
            # no further tokens are generated or billed.
            async with aclosing(stream):
                async for chunk in stream:
                    for event in _content_events(chunk):
                        yield event
                    metadata = chunk.response_metadata
                    if metadata.get("model_name"):
                        model = str(metadata["model_name"])
                    if metadata.get("stop_reason"):
                        stop_reason = str(metadata["stop_reason"])
                        details = metadata.get("stop_details")
                        if isinstance(details, dict) and details.get("category"):
                            stop_category = str(details["category"])
                    if chunk.usage_metadata:
                        usage = _token_usage(chunk.usage_metadata)
        except anthropic.APIError as exc:
            raise _map_error(exc) from exc
        yield Completed(
            stop_reason=stop_reason, usage=usage, model=model, stop_category=stop_category
        )


def _content_events(chunk: AIMessageChunk) -> list[GenerationEvent]:
    events: list[GenerationEvent] = []
    if not isinstance(chunk.content, list):
        return events
    for block in chunk.content:
        if not isinstance(block, dict) or block.get("type") != "text":
            continue  # thinking blocks and anything else are not part of the answer
        index = int(block.get("index", 0))
        for citation in block.get("citations") or []:
            if isinstance(citation, dict) and citation.get("type") == "search_result_location":
                try:
                    events.append(
                        CitationRef(
                            block=index,
                            search_result_index=int(citation["search_result_index"]),
                            start_block_index=int(citation["start_block_index"]),
                            end_block_index=int(citation["end_block_index"]),
                        )
                    )
                except (KeyError, TypeError, ValueError):
                    logger.warning("llm_malformed_citation_dropped")
        text = block.get("text")
        if isinstance(text, str) and text:
            events.append(TextDelta(text=text, block=index))
    return events


def _token_usage(usage: Any) -> TokenUsage:
    """LangChain reports ``input_tokens`` *including* cache reads and writes."""
    details = usage.get("input_token_details") or {}
    cache_read = int(details.get("cache_read") or 0)
    cache_creation = int(details.get("cache_creation") or 0) + int(
        details.get("ephemeral_5m_input_tokens") or 0
    )
    total_input = int(usage.get("input_tokens") or 0)
    return TokenUsage(
        input_uncached=max(total_input - cache_read - cache_creation, 0),
        cache_creation=cache_creation,
        cache_read=cache_read,
        output=int(usage.get("output_tokens") or 0),
    )


def _retry_after(exc: anthropic.APIStatusError) -> int | None:
    value = exc.response.headers.get("retry-after")
    try:
        return max(1, int(float(value))) if value else None
    except ValueError:
        return None


def _error_type(exc: anthropic.APIError) -> str | None:
    body = getattr(exc, "body", None)
    if isinstance(body, dict):
        error = body.get("error")
        if isinstance(error, dict) and isinstance(error.get("type"), str):
            return str(error["type"])
    return None


def _map_error(exc: anthropic.APIError) -> GenerationError:
    detail = f"{type(exc).__name__}: {exc}"
    if isinstance(exc, anthropic.AuthenticationError | anthropic.PermissionDeniedError):
        return GenerationError(
            "llm_not_configured", "The Anthropic API rejected the credentials.", detail=detail
        )
    error_type = _error_type(exc)
    if isinstance(exc, anthropic.RateLimitError) or error_type == "rate_limit_error":
        return GenerationError(
            "llm_rate_limited",
            "The Anthropic API rate limit was reached. Try again shortly.",
            retryable=True,
            retry_after=_retry_after(exc) if isinstance(exc, anthropic.APIStatusError) else None,
            detail=detail,
        )
    if isinstance(exc, anthropic.APITimeoutError):
        return GenerationError(
            "llm_timeout",
            "The Anthropic API did not respond in time.",
            retryable=True,
            detail=detail,
        )
    if isinstance(exc, anthropic.APIConnectionError):
        return GenerationError(
            "llm_unavailable", "The Anthropic API is unreachable.", retryable=True, detail=detail
        )
    # Errors inside an already-started stream arrive as APIStatusError with HTTP 200;
    # the error type in the body tells overload apart from a bad request.
    if isinstance(exc, anthropic.APIStatusError) and (
        exc.status_code >= 500 or error_type in {"overloaded_error", "api_error"}
    ):
        return GenerationError(
            "llm_unavailable",
            "The Anthropic API is temporarily unavailable.",
            retryable=True,
            retry_after=_retry_after(exc),
            detail=detail,
        )
    return GenerationError(
        "llm_request_failed", "The answer could not be generated.", detail=detail
    )


def describe(sources: Sequence[SourceDocument]) -> str:
    """Short log description (never the passage text)."""
    return f"{len(sources)} documents / {sum(len(s.passages) for s in sources)} passages"
