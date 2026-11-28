"""Grounded-answer use case: admission, retrieval, generation, persistence.

Two phases (ADR 0019):

``prepare`` (before any response byte): readiness, LLM configuration, a generation
slot (max concurrent generations), conversation lookup, retrieval, context assembly,
and the budget reservation. Every failure here is an ordinary JSON error (4xx/503).

``stream`` (inside the SSE response): calls the generator **without any database
connection**, emits ``meta``/``delta``/``citation``/``done``/``error`` events, and records
the outcome in one short transaction at the end, also when the client disconnects
(``cancelled``) or the upstream fails (``failed``, partial text kept).
"""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import AsyncGenerator, Sequence
from contextlib import aclosing
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, cast

import structlog
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from nexus_api.core.config import Settings
from nexus_api.db.models import MessageStatus
from nexus_api.documents.repository import search_chunks
from nexus_api.embeddings.provider import EmbedderProvider
from nexus_api.llm.generator import (
    AnswerGenerator,
    CitationRef,
    Completed,
    GenerationError,
    GenerationEvent,
    GenerationRequest,
    TextDelta,
)
from nexus_api.llm.pricing import PRICES, cost_usd, reservation_usd
from nexus_api.rag import repository
from nexus_api.rag.citations import CitationResolver, marker
from nexus_api.rag.context import ContextDocument, assemble_context, context_chars
from nexus_api.rag.errors import (
    ChatNotReadyError,
    ConversationBusyAppError,
    ConversationNotFoundError,
    LlmBudgetExceededError,
    LlmBusyError,
    LlmNotConfiguredError,
)
from nexus_api.rag.prompt import NO_CONTEXT_ANSWER, SYSTEM_PROMPT, history_turns, prompt_chars

logger = structlog.stdlib.get_logger(__name__)

SseEvent = dict[str, Any]  # {"event": name, "data": payload}


class GenerationSlots:
    """Non-blocking concurrency limit: a full house is a 429, not a queue."""

    def __init__(self, limit: int) -> None:
        self.limit = limit
        self.active = 0

    def try_acquire(self) -> bool:
        if self.active >= self.limit:
            return False
        self.active += 1
        return True

    def release(self) -> None:
        self.active = max(0, self.active - 1)


@dataclass(slots=True)
class PreparedAnswer:
    conversation_id: uuid.UUID
    user_message_id: uuid.UUID
    message_id: uuid.UUID
    question: str
    request: GenerationRequest | None  # None: nothing retrieved, answer without Claude
    documents: Sequence[ContextDocument]
    model: str
    started: bool = False  # stream() has begun and owns cleanup from then on
    released: bool = False  # the generation slot has been given back


def _seconds_until_utc_midnight() -> int:
    now = datetime.now(UTC)
    tomorrow = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return max(60, int((tomorrow - now).total_seconds()))


class ChatService:
    def __init__(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        embedder_provider: EmbedderProvider,
        settings: Settings,
        generator: AnswerGenerator | None,
        *,
        recovery_retry_seconds: float = 2.0,
    ) -> None:
        self._sessionmaker = sessionmaker
        self._embedder_provider = embedder_provider
        self._settings = settings
        self._generator = generator
        self._recovery_retry_seconds = recovery_retry_seconds
        self.slots = GenerationSlots(settings.llm_max_concurrent_generations)
        self._ready = False
        self._recovery_task: asyncio.Task[None] | None = None

    # --- lifecycle ------------------------------------------------------------------

    @property
    def configured(self) -> bool:
        return self._generator is not None

    @property
    def ready(self) -> bool:
        return self._ready

    def start(self) -> None:
        self._recovery_task = asyncio.create_task(self._recover(), name="chat-recovery")

    async def _recover(self) -> None:
        """Mark generations orphaned by a crash as interrupted, then accept new ones."""
        while True:
            try:
                count = await repository.fail_interrupted_generations(self._sessionmaker)
            except Exception as exc:
                logger.warning(
                    "chat_recovery_failed",
                    error_type=type(exc).__name__,
                    retry_in_seconds=self._recovery_retry_seconds,
                )
                await asyncio.sleep(self._recovery_retry_seconds)
                continue
            if count:
                logger.warning("chat_recovered_interrupted_generations", count=count)
            self._ready = True
            return

    async def shutdown(self) -> None:
        if self._recovery_task is not None and not self._recovery_task.done():
            self._recovery_task.cancel()
            await asyncio.gather(self._recovery_task, return_exceptions=True)

    # --- phase 1: admission ---------------------------------------------------------

    async def prepare(
        self,
        conversation_id: uuid.UUID,
        question: str,
        document_ids: Sequence[uuid.UUID] | None,
    ) -> PreparedAnswer:
        if self._generator is None:
            raise LlmNotConfiguredError(
                "Grounded answers need an Anthropic API key (ANTHROPIC_API_KEY)."
            )
        if not self._ready:
            raise ChatNotReadyError("Chat is starting up.", headers={"Retry-After": "5"})
        if not self.slots.try_acquire():
            raise LlmBusyError(
                "Too many answers are being generated. Try again shortly.",
                headers={"Retry-After": "5"},
            )
        try:
            return await self._prepare(conversation_id, question, document_ids)
        except BaseException:
            self.slots.release()
            raise

    async def _prepare(
        self,
        conversation_id: uuid.UUID,
        question: str,
        document_ids: Sequence[uuid.UUID] | None,
    ) -> PreparedAnswer:
        settings = self._settings
        embedder = self._embedder_provider.get()  # 503 while the model loads

        async with self._sessionmaker() as session:
            if await repository.get_conversation(session, conversation_id) is None:
                raise ConversationNotFoundError("Conversation not found.")
            pairs = await repository.completed_pairs(
                session, conversation_id, limit=settings.rag_history_turns
            )
        history = history_turns(pairs, max_turns=settings.rag_history_turns)

        # D5: no query rewriting; a follow-up is retrieved together with the
        # previous question so that "and in Germany?" keeps its subject.
        retrieval_query = question if not history else f"{history[-1].question}\n{question}"
        query_vector = await asyncio.to_thread(embedder.embed_query, retrieval_query)
        async with self._sessionmaker() as session:
            hits = await search_chunks(
                session,
                query_vector,
                top_k=settings.rag_top_k,
                ef_search=settings.search_ef_search,
                embedding_model=embedder.model_id,
                document_ids=document_ids,
            )
        documents = assemble_context(hits, char_budget=settings.rag_context_char_budget)

        if not documents:
            try:
                stored = await repository.store_no_context_turn(
                    self._sessionmaker,
                    conversation_id=conversation_id,
                    question=question,
                    answer=NO_CONTEXT_ANSWER,
                )
            except repository.ConversationBusyError as exc:
                raise ConversationBusyAppError(
                    "An answer is already being generated in this conversation."
                ) from exc
            return PreparedAnswer(
                conversation_id,
                stored.user_message_id,
                stored.assistant_message_id,
                question,
                None,
                [],
                settings.llm_model,
            )

        request = GenerationRequest(
            system=SYSTEM_PROMPT,
            history=history,
            sources=[d.as_source() for d in documents],
            question=question,
            max_output_tokens=settings.llm_max_output_tokens,
        )
        reservation = Decimal(
            str(
                reservation_usd(
                    settings.llm_model,
                    prompt_chars=prompt_chars(SYSTEM_PROMPT, history, question)
                    + context_chars(documents),
                    max_output_tokens=settings.llm_max_output_tokens,
                )
            )
        )
        try:
            admitted = await repository.admit_generation(
                self._sessionmaker,
                conversation_id=conversation_id,
                question=question,
                model=settings.llm_model,
                reservation_usd=reservation,
                budget_usd=Decimal(str(settings.llm_daily_budget_usd)),
                retrieved=[
                    {
                        "chunk_id": str(p.chunk_id),
                        "document_id": str(p.document_id),
                        "page": p.page,
                        "score": p.score,
                    }
                    for d in documents
                    for p in d.passages
                ],
            )
        except repository.ConversationBusyError as exc:
            raise ConversationBusyAppError(
                "An answer is already being generated in this conversation."
            ) from exc
        except repository.BudgetExceededError as exc:
            raise LlmBudgetExceededError(
                f"The daily API budget (${settings.llm_daily_budget_usd:.2f}) is used up "
                f"(${exc.spent:.4f} spent or reserved). It resets at midnight UTC.",
                headers={"Retry-After": str(_seconds_until_utc_midnight())},
            ) from exc
        return PreparedAnswer(
            conversation_id,
            admitted.user_message_id,
            admitted.assistant_message_id,
            question,
            request,
            documents,
            settings.llm_model,
        )

    # --- phase 2: streaming -----------------------------------------------------------

    def _release(self, prepared: PreparedAnswer) -> None:
        if not prepared.released:
            prepared.released = True
            self.slots.release()

    async def abandon(self, prepared: PreparedAnswer) -> None:
        """Clean up an admitted answer whose stream never started (the client went away
        before the first byte, or the response failed to start). Without this, an
        unstarted generator runs no ``finally``: the slot would leak and the row would
        stay ``streaming``, blocking the conversation until a restart. No-op once
        ``stream()`` has started, because it then cleans up itself."""
        if prepared.started:
            return
        prepared.started = True
        try:
            if prepared.request is not None:
                logger.info("answer_abandoned", message_id=str(prepared.message_id))
                await self._finalize(
                    prepared.message_id,
                    repository.GenerationOutcome.cancelled_before_start(prepared.model),
                )
        finally:
            self._release(prepared)

    async def stream(self, prepared: PreparedAnswer) -> AsyncGenerator[SseEvent, None]:
        if prepared.started:
            raise RuntimeError("stream() may only be consumed once")
        prepared.started = True
        try:
            yield _event(
                "meta",
                {
                    "conversation_id": str(prepared.conversation_id),
                    "user_message_id": str(prepared.user_message_id),
                    "message_id": str(prepared.message_id),
                    "model": prepared.model,
                    "sources": [
                        {
                            "document_id": str(d.document_id),
                            "filename": d.filename,
                            "passages": len(d.passages),
                        }
                        for d in prepared.documents
                    ],
                },
            )
            if prepared.request is None:
                yield _event("delta", {"text": NO_CONTEXT_ANSWER})
                yield _event(
                    "done",
                    {
                        "message_id": str(prepared.message_id),
                        "status": MessageStatus.NO_CONTEXT,
                        "grounded": False,
                        "cost_usd": 0.0,
                    },
                )
                return
            # aclosing: if the response closes this generator (aclose) instead of
            # cancelling it, the inner one is closed too and records the outcome.
            async with aclosing(self._generate(prepared, prepared.request)) as events:
                async for event in events:
                    yield event
        finally:
            self._release(prepared)

    async def _generate(
        self, prepared: PreparedAnswer, request: GenerationRequest
    ) -> AsyncGenerator[SseEvent, None]:
        assert self._generator is not None  # noqa: S101 - checked in prepare()
        log = logger.bind(message_id=str(prepared.message_id))
        resolver = CitationResolver(prepared.documents)
        parts: list[str] = []
        pending: list[int] = []  # citation numbers of the current block
        current_block: int | None = None
        completed: Completed | None = None
        started = time.perf_counter()
        ttft_ms: int | None = None

        def close_block() -> str:
            markers = "".join(marker(n) for n in dict.fromkeys(pending))
            pending.clear()
            if markers:
                parts.append(markers)
            return markers

        def outcome(
            status: MessageStatus, *, error_code: str | None = None
        ) -> repository.GenerationOutcome:
            usage = completed.usage if completed else None
            model = completed.model if completed else prepared.model
            price_model = model if model in PRICES else prepared.model
            return repository.GenerationOutcome(
                status=status,
                content="".join(parts),
                citations=[c.as_json() for c in resolver.citations],
                grounded=bool(resolver.citations)
                if status in (MessageStatus.COMPLETE, MessageStatus.TRUNCATED)
                else None,
                model=model,
                input_tokens=usage.input_uncached if usage else None,
                cache_creation_tokens=usage.cache_creation if usage else None,
                cache_read_tokens=usage.cache_read if usage else None,
                output_tokens=usage.output if usage else None,
                # Unknown usage (cancelled/failed before the end): cost stays NULL and
                # the reservation keeps counting against the budget.
                cost_usd=Decimal(str(cost_usd(price_model, usage))) if usage else None,
                stop_reason=completed.stop_reason if completed else None,
                stop_category=completed.stop_category if completed else None,
                error_code=error_code,
                ttft_ms=ttft_ms,
                total_ms=int((time.perf_counter() - started) * 1000),
            )

        try:
            upstream = cast(
                "AsyncGenerator[GenerationEvent, None]", self._generator.stream(request)
            )
            async with (
                asyncio.timeout(self._settings.llm_timeout_seconds),
                aclosing(upstream),
            ):
                async for item in upstream:
                    if isinstance(item, TextDelta):
                        if (
                            current_block is not None
                            and item.block != current_block
                            and (markers := close_block())
                        ):
                            yield _event("delta", {"text": markers})
                        current_block = item.block
                        if ttft_ms is None:
                            ttft_ms = int((time.perf_counter() - started) * 1000)
                        parts.append(item.text)
                        yield _event("delta", {"text": item.text})
                    elif isinstance(item, CitationRef):
                        if current_block is not None and item.block != current_block:
                            if markers := close_block():
                                yield _event("delta", {"text": markers})
                            current_block = item.block
                        resolved = resolver.resolve(item)
                        if resolved is not None:
                            citation, is_new = resolved
                            pending.append(citation.number)
                            if is_new:
                                yield _event("citation", citation.as_json())
                    elif isinstance(item, Completed):
                        completed = item
            if markers := close_block():
                yield _event("delta", {"text": markers})

            status = {
                "max_tokens": MessageStatus.TRUNCATED,
                "refusal": MessageStatus.REFUSED,
            }.get((completed.stop_reason or "") if completed else "", MessageStatus.COMPLETE)
            result = outcome(status)
            await self._finalize(prepared.message_id, result)
            log.info(
                "answer_completed",
                status=status,
                citations=len(resolver.citations),
                dropped_citations=resolver.dropped,
                cost_usd=str(result.cost_usd),
                ttft_ms=ttft_ms,
            )
            yield _event("done", _done_payload(prepared.message_id, result))
        except (asyncio.CancelledError, GeneratorExit):
            # Client disconnected: the upstream stream is closed by aclosing above.
            # Log first: under sse-starlette's anyio cancel scope, cancellation is
            # re-delivered at every await, so code after the shielded write never runs
            # (the write itself completes in its own task).
            log.info("answer_cancelled", chars=sum(len(p) for p in parts))
            await self._finalize(prepared.message_id, outcome(MessageStatus.CANCELLED))
            raise
        except GenerationError as exc:
            log.warning("answer_failed", code=exc.code, detail=exc.detail)
            await self._finalize(
                prepared.message_id, outcome(MessageStatus.FAILED, error_code=exc.code)
            )
            yield _error_event(
                prepared.message_id, exc.code, exc.message, exc.retryable, exc.retry_after
            )
        except TimeoutError:
            log.warning("answer_timeout")
            await self._finalize(
                prepared.message_id, outcome(MessageStatus.FAILED, error_code="llm_timeout")
            )
            yield _error_event(
                prepared.message_id, "llm_timeout", "The answer took too long.", True, None
            )
        except Exception:
            log.exception("answer_crashed")
            await self._finalize(
                prepared.message_id, outcome(MessageStatus.FAILED, error_code="internal_error")
            )
            yield _error_event(
                prepared.message_id,
                "internal_error",
                "The answer could not be generated.",
                False,
                None,
            )

    async def _finalize(self, message_id: uuid.UUID, result: repository.GenerationOutcome) -> None:
        """Persist even while being cancelled; a failure leaves the row ``streaming``,
        which the next startup marks interrupted."""
        try:
            await asyncio.shield(
                repository.finalize_generation(self._sessionmaker, message_id, result)
            )
        except Exception:
            logger.exception("answer_finalize_failed", message_id=str(message_id))

    # --- status -----------------------------------------------------------------------

    async def status(self) -> dict[str, Any]:
        """Configuration and today's spend. Degrades (spend unknown) without a database."""
        budget = Decimal(str(self._settings.llm_daily_budget_usd))
        spent: Decimal | None
        try:
            spent = await repository.spent_today_standalone(self._sessionmaker)
        except Exception as exc:
            logger.warning("llm_status_spend_unavailable", error_type=type(exc).__name__)
            spent = None
        return {
            "configured": self.configured,
            "ready": self._ready,
            "model": self._settings.llm_model,
            "effort": self._settings.llm_effort,
            "max_output_tokens": self._settings.llm_max_output_tokens,
            "daily_budget_usd": float(budget),
            "spent_today_usd": float(spent) if spent is not None else None,
            "remaining_today_usd": float(max(Decimal(0), budget - spent))
            if spent is not None
            else None,
            "active_generations": self.slots.active,
            "max_concurrent_generations": self.slots.limit,
        }


def _event(name: str, data: dict[str, Any]) -> SseEvent:
    return {"event": name, "data": data}


def _error_event(
    message_id: uuid.UUID, code: str, message: str, retryable: bool, retry_after: int | None
) -> SseEvent:
    return _event(
        "error",
        {
            "message_id": str(message_id),
            "code": code,
            "message": message,
            "retryable": retryable,
            "retry_after": retry_after,
        },
    )


def _done_payload(message_id: uuid.UUID, result: repository.GenerationOutcome) -> dict[str, Any]:
    return {
        "message_id": str(message_id),
        "status": result.status,
        "grounded": result.grounded,
        "stop_reason": result.stop_reason,
        "stop_category": result.stop_category,
        "model": result.model,
        "usage": {
            "input_tokens": result.input_tokens,
            "cache_creation_tokens": result.cache_creation_tokens,
            "cache_read_tokens": result.cache_read_tokens,
            "output_tokens": result.output_tokens,
        },
        "cost_usd": float(result.cost_usd) if result.cost_usd is not None else None,
        "ttft_ms": result.ttft_ms,
        "total_ms": result.total_ms,
    }
