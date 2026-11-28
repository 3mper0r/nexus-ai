"""SQL for conversations and messages.

Transaction policy (ADR 0019): every function is one short unit of work. No
transaction is open while Claude generates; the service reads, generates without a
connection, then writes.

Budget enforcement (ADR 0020): ``admit_generation`` runs in one transaction under a
PostgreSQL advisory lock. It sums today's spend, counting in-flight generations at
their reserved worst-case cost, and inserts the new reservation only if it fits.
Spend lives in the ``llm_usage`` ledger, not in ``messages``, so deleting a
conversation cannot reopen the budget.
Concurrent requests serialise on the lock, so they cannot both pass the check and
overspend together. Because reservations live in the database, the check also holds
across processes and survives restarts.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import Select, delete, func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from nexus_api.db.models import Conversation, LlmUsage, Message, MessageRole, MessageStatus

# Arbitrary, fixed key for pg_advisory_xact_lock: "NEXUS LLM budget".
BUDGET_LOCK_KEY = 0x4E5855_4C4C4D
TITLE_MAX = 80


class ConversationBusyError(Exception):
    """An answer is already being generated in this conversation."""


class BudgetExceededError(Exception):
    def __init__(self, spent: Decimal, budget: Decimal) -> None:
        super().__init__("daily LLM budget exceeded")
        self.spent = spent
        self.budget = budget


def _start_of_today_utc() -> datetime:
    now = datetime.now(UTC)
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


def _spent_today_query() -> Select[tuple[Decimal]]:
    return select(
        func.coalesce(func.sum(func.coalesce(LlmUsage.cost_usd, LlmUsage.reserved_cost_usd)), 0)
    ).where(LlmUsage.created_at >= _start_of_today_utc())


async def spent_today(session: AsyncSession) -> Decimal:
    value = (await session.execute(_spent_today_query())).scalar_one()
    return Decimal(value or 0)


# --- conversations (request-scoped session; caller commits) --------------------------


async def create_conversation(session: AsyncSession, title: str | None) -> Conversation:
    conversation = Conversation(id=uuid.uuid4(), title=(title or "New conversation")[:TITLE_MAX])
    session.add(conversation)
    await session.flush()
    await session.refresh(conversation)
    return conversation


async def list_conversations(session: AsyncSession, *, limit: int) -> list[Conversation]:
    result = await session.execute(
        select(Conversation).order_by(Conversation.updated_at.desc()).limit(limit)
    )
    return list(result.scalars())


async def get_conversation(
    session: AsyncSession, conversation_id: uuid.UUID
) -> Conversation | None:
    return await session.get(Conversation, conversation_id)


async def list_messages(session: AsyncSession, conversation_id: uuid.UUID) -> list[Message]:
    result = await session.execute(
        select(Message)
        .where(Message.conversation_id == conversation_id)
        .order_by(Message.created_at, Message.role.desc())  # user before assistant on ties
    )
    return list(result.scalars())


async def has_streaming_message(session: AsyncSession, conversation_id: uuid.UUID) -> bool:
    result = await session.execute(
        select(func.count())
        .select_from(Message)
        .where(
            Message.conversation_id == conversation_id,
            Message.status == MessageStatus.STREAMING,
        )
    )
    return bool(result.scalar_one())


async def delete_conversation(session: AsyncSession, conversation_id: uuid.UUID) -> bool:
    result = await session.execute(
        delete(Conversation).where(Conversation.id == conversation_id).returning(Conversation.id)
    )
    return result.scalar_one_or_none() is not None


async def completed_pairs(
    session: AsyncSession, conversation_id: uuid.UUID, *, limit: int
) -> list[tuple[str, str]]:
    """Question/answer pairs whose answer completed (or was truncated), oldest first.

    Failed, cancelled, refused and interrupted turns are not replayed as history.
    """
    if limit == 0:
        return []
    messages = await list_messages(session, conversation_id)
    pairs: list[tuple[str, str]] = []
    pending_question: str | None = None
    for message in messages:
        if message.role == MessageRole.USER:
            pending_question = message.content
        elif pending_question is not None:
            if message.status in (MessageStatus.COMPLETE, MessageStatus.TRUNCATED):
                pairs.append((pending_question, message.content))
            pending_question = None
    return pairs[-limit:]


# --- generation lifecycle (each opens its own short transaction) ----------------------


@dataclass(frozen=True, slots=True)
class AdmittedGeneration:
    user_message_id: uuid.UUID
    assistant_message_id: uuid.UUID
    reserved_usd: Decimal


async def admit_generation(
    sessionmaker: async_sessionmaker[AsyncSession],
    *,
    conversation_id: uuid.UUID,
    question: str,
    model: str,
    reservation_usd: Decimal,
    budget_usd: Decimal,
    retrieved: list[dict[str, Any]],
) -> AdmittedGeneration:
    async with sessionmaker() as session, session.begin():
        await session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": BUDGET_LOCK_KEY})
        # Under the same lock: one generation at a time per conversation, so turns
        # cannot interleave and history stays a clean question/answer sequence.
        if await has_streaming_message(session, conversation_id):
            raise ConversationBusyError
        spent = await spent_today(session)
        if spent + reservation_usd > budget_usd:
            raise BudgetExceededError(spent, budget_usd)
        user_id, assistant_id = await _insert_turn(session, conversation_id, question)
        session.add(
            Message(
                id=assistant_id,
                conversation_id=conversation_id,
                role=MessageRole.ASSISTANT,
                content="",
                status=MessageStatus.STREAMING,
                citations=[],
                retrieved=retrieved,
                model=model,
                reserved_cost_usd=reservation_usd,
            )
        )
        session.add(LlmUsage(id=assistant_id, model=model, reserved_cost_usd=reservation_usd))
        return AdmittedGeneration(user_id, assistant_id, reservation_usd)


async def store_no_context_turn(
    sessionmaker: async_sessionmaker[AsyncSession],
    *,
    conversation_id: uuid.UUID,
    question: str,
    answer: str,
) -> AdmittedGeneration:
    async with sessionmaker() as session, session.begin():
        # Same lock and rule as admit_generation: no turn may interleave with an
        # answer that is still streaming in this conversation.
        await session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": BUDGET_LOCK_KEY})
        if await has_streaming_message(session, conversation_id):
            raise ConversationBusyError
        user_id, assistant_id = await _insert_turn(session, conversation_id, question)
        session.add(
            Message(
                id=assistant_id,
                conversation_id=conversation_id,
                role=MessageRole.ASSISTANT,
                content=answer,
                status=MessageStatus.NO_CONTEXT,
                citations=[],
                retrieved=[],
                grounded=False,
                cost_usd=Decimal(0),
                completed_at=func.now(),
            )
        )
        return AdmittedGeneration(user_id, assistant_id, Decimal(0))


async def _insert_turn(
    session: AsyncSession, conversation_id: uuid.UUID, question: str
) -> tuple[uuid.UUID, uuid.UUID]:
    user_id, assistant_id = uuid.uuid4(), uuid.uuid4()
    session.add(
        Message(
            id=user_id,
            conversation_id=conversation_id,
            role=MessageRole.USER,
            content=question,
            status=MessageStatus.COMPLETE,
            citations=[],
            retrieved=[],
        )
    )
    # Both rows get the same created_at (now() is the transaction start time);
    # list_messages orders user before assistant on ties.
    await session.flush()
    conversation = await session.get(Conversation, conversation_id)
    if conversation is not None:
        if conversation.title == "New conversation":
            conversation.title = " ".join(question.split())[:TITLE_MAX]
        conversation.updated_at = datetime.now(UTC)
    return user_id, assistant_id


@dataclass(frozen=True, slots=True)
class GenerationOutcome:
    status: MessageStatus
    content: str
    citations: list[dict[str, Any]]
    grounded: bool | None
    model: str | None
    input_tokens: int | None
    cache_creation_tokens: int | None
    cache_read_tokens: int | None
    output_tokens: int | None
    cost_usd: Decimal | None
    stop_reason: str | None
    stop_category: str | None
    error_code: str | None
    ttft_ms: int | None
    total_ms: int

    @classmethod
    def cancelled_before_start(cls, model: str) -> GenerationOutcome:
        """The stream never started, so Claude was never called: cost is zero, which
        releases the reservation."""
        return cls(
            status=MessageStatus.CANCELLED,
            content="",
            citations=[],
            grounded=None,
            model=model,
            input_tokens=None,
            cache_creation_tokens=None,
            cache_read_tokens=None,
            output_tokens=None,
            cost_usd=Decimal(0),
            stop_reason=None,
            stop_category=None,
            error_code=None,
            ttft_ms=None,
            total_ms=0,
        )


async def finalize_generation(
    sessionmaker: async_sessionmaker[AsyncSession],
    message_id: uuid.UUID,
    outcome: GenerationOutcome,
) -> None:
    """Record the outcome. Without a known cost, the reservation keeps counting."""
    async with sessionmaker() as session, session.begin():
        finalized = await session.execute(
            update(Message)
            .where(Message.id == message_id, Message.status == MessageStatus.STREAMING)
            .values(
                status=outcome.status,
                content=outcome.content,
                citations=outcome.citations,
                grounded=outcome.grounded,
                model=outcome.model,
                input_tokens=outcome.input_tokens,
                cache_creation_tokens=outcome.cache_creation_tokens,
                cache_read_tokens=outcome.cache_read_tokens,
                output_tokens=outcome.output_tokens,
                cost_usd=outcome.cost_usd,
                stop_reason=outcome.stop_reason,
                stop_category=outcome.stop_category,
                error_code=outcome.error_code,
                ttft_ms=outcome.ttft_ms,
                total_ms=outcome.total_ms,
                completed_at=func.now(),
            )
            .returning(Message.id)
        )
        if finalized.first() is not None and outcome.cost_usd is not None:
            await session.execute(
                update(LlmUsage).where(LlmUsage.id == message_id).values(cost_usd=outcome.cost_usd)
            )


async def fail_interrupted_generations(sessionmaker: async_sessionmaker[AsyncSession]) -> int:
    """At startup: any ``streaming`` row was orphaned by a crash or restart.

    Its reservation stays as the (conservative) cost because the real usage is unknown.
    """
    async with sessionmaker() as session, session.begin():
        result = await session.execute(
            update(Message)
            .where(Message.status == MessageStatus.STREAMING)
            .values(
                status=MessageStatus.INTERRUPTED,
                error_code="interrupted",
                completed_at=func.now(),
            )
            .returning(Message.id)
        )
        return len(result.all())


async def spent_today_standalone(sessionmaker: async_sessionmaker[AsyncSession]) -> Decimal:
    async with sessionmaker() as session:
        return await spent_today(session)
