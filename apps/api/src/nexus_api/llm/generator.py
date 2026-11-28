"""The answer-generation boundary (ADR 0016).

RAG code depends on this Protocol and these event types, not on LangChain or the
Anthropic SDK. The Claude implementation lives in ``claude.py``; tests use scripted
fakes. That keeps the paid API out of every routine test.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field
from typing import Protocol

from nexus_api.llm.pricing import TokenUsage


@dataclass(frozen=True, slots=True)
class SourceDocument:
    """One retrieved document, sent as one ``search_result`` block.

    ``passages`` become the block's text blocks, in order; citations refer to them
    by index, which is how answers map back to exact chunks and pages.
    """

    source: str
    title: str
    passages: Sequence[str]


@dataclass(frozen=True, slots=True)
class HistoryTurn:
    question: str
    answer: str


@dataclass(frozen=True, slots=True)
class GenerationRequest:
    system: str
    history: Sequence[HistoryTurn]
    sources: Sequence[SourceDocument]
    question: str
    max_output_tokens: int


@dataclass(frozen=True, slots=True)
class TextDelta:
    text: str
    block: int


@dataclass(frozen=True, slots=True)
class CitationRef:
    """A structured citation exactly as the API reported it (not yet validated)."""

    block: int
    search_result_index: int
    start_block_index: int
    end_block_index: int


@dataclass(frozen=True, slots=True)
class Completed:
    stop_reason: str | None
    usage: TokenUsage
    model: str
    stop_category: str | None = None


GenerationEvent = TextDelta | CitationRef | Completed


@dataclass(slots=True)
class GenerationError(Exception):
    """An upstream failure, mapped to a stable code (ADR 0021)."""

    code: str
    message: str
    retryable: bool = False
    retry_after: int | None = None
    detail: str | None = field(default=None, repr=False)  # logged, never returned

    def __post_init__(self) -> None:
        Exception.__init__(self, self.code)


class AnswerGenerator(Protocol):
    def stream(self, request: GenerationRequest) -> AsyncIterator[GenerationEvent]:
        """Yield text and citations as they arrive, then exactly one ``Completed``.

        Raises ``GenerationError`` for upstream failures. Cancelling the consumer
        must close the upstream request (no further tokens are billed).
        """
        ...
