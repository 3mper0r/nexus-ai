"""Conversations and grounded answers (SSE)."""

from __future__ import annotations

import asyncio
import json
import uuid
from contextlib import aclosing
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Query, status
from pydantic import BaseModel, ConfigDict, Field, StringConstraints
from sse_starlette import EventSourceResponse, ServerSentEvent
from starlette.types import Receive, Scope, Send

from nexus_api.api.deps import ChatServiceDep, DbSessionDep
from nexus_api.core.errors import ErrorResponse
from nexus_api.db.models import Conversation, Message
from nexus_api.rag import repository
from nexus_api.rag.errors import ConversationBusyAppError, ConversationNotFoundError
from nexus_api.rag.service import ChatService, PreparedAnswer

router = APIRouter(tags=["conversations"])

QuestionText = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=4000)
]
TitleText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]


class ConversationCreate(BaseModel):
    title: TitleText | None = None


class ConversationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str
    created_at: datetime
    updated_at: datetime


class MessageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    role: str
    content: str
    status: str
    citations: list[dict[str, Any]]
    grounded: bool | None
    model: str | None
    input_tokens: int | None
    cache_creation_tokens: int | None
    cache_read_tokens: int | None
    output_tokens: int | None
    cost_usd: float | None
    stop_reason: str | None
    stop_category: str | None
    error_code: str | None
    ttft_ms: int | None
    total_ms: int | None
    created_at: datetime


class ConversationDetail(ConversationOut):
    messages: list[MessageOut]


class AskRequest(BaseModel):
    content: QuestionText
    document_ids: list[uuid.UUID] | None = Field(default=None, max_length=50)


class LlmStatus(BaseModel):
    configured: bool
    ready: bool
    model: str
    effort: str
    max_output_tokens: int
    daily_budget_usd: float
    spent_today_usd: float | None  # None: database unavailable
    remaining_today_usd: float | None
    active_generations: int
    max_concurrent_generations: int


_ERRORS: dict[int | str, dict[str, object]] = {
    code: {"model": ErrorResponse} for code in (404, 409, 422, 429, 503)
}


def _message_out(message: Message) -> MessageOut:
    out = MessageOut.model_validate(message)
    out.cost_usd = float(message.cost_usd) if message.cost_usd is not None else None
    return out


async def _conversation(session: DbSessionDep, conversation_id: uuid.UUID) -> Conversation:
    conversation = await repository.get_conversation(session, conversation_id)
    if conversation is None:
        raise ConversationNotFoundError("Conversation not found.")
    return conversation


@router.post(
    "/conversations",
    status_code=status.HTTP_201_CREATED,
    summary="Start a conversation",
    responses=_ERRORS,
)
async def create_conversation(body: ConversationCreate, session: DbSessionDep) -> ConversationOut:
    conversation = await repository.create_conversation(session, body.title)
    await session.commit()
    return ConversationOut.model_validate(conversation)


@router.get("/conversations", summary="List conversations (most recent first)")
async def list_conversations(
    session: DbSessionDep, limit: Annotated[int, Query(ge=1, le=100)] = 50
) -> list[ConversationOut]:
    return [
        ConversationOut.model_validate(c)
        for c in await repository.list_conversations(session, limit=limit)
    ]


@router.get(
    "/conversations/{conversation_id}",
    summary="A conversation with its messages",
    responses=_ERRORS,
)
async def get_conversation(conversation_id: uuid.UUID, session: DbSessionDep) -> ConversationDetail:
    conversation = await _conversation(session, conversation_id)
    messages = await repository.list_messages(session, conversation_id)
    return ConversationDetail(
        **ConversationOut.model_validate(conversation).model_dump(),
        messages=[_message_out(m) for m in messages],
    )


@router.delete(
    "/conversations/{conversation_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a conversation and its messages",
    responses=_ERRORS,
)
async def delete_conversation(conversation_id: uuid.UUID, session: DbSessionDep) -> None:
    await _conversation(session, conversation_id)
    if await repository.has_streaming_message(session, conversation_id):
        raise ConversationBusyAppError("An answer is still being generated in this conversation.")
    await repository.delete_conversation(session, conversation_id)
    await session.commit()


@router.post(
    "/conversations/{conversation_id}/messages",
    summary="Ask a question; the grounded answer streams as Server-Sent Events",
    response_class=EventSourceResponse,
    responses={
        200: {
            "description": "text/event-stream with events meta, delta, citation, done, error",
            "content": {"text/event-stream": {}},
        },
        **_ERRORS,
    },
)
async def ask(
    conversation_id: uuid.UUID, body: AskRequest, chat: ChatServiceDep
) -> EventSourceResponse:
    # Admission happens here, before the response starts: errors are plain JSON.
    prepared = await chat.prepare(conversation_id, body.content, body.document_ids)
    return answer_response(chat, prepared)


class _AnswerEventSourceResponse(EventSourceResponse):
    """Cleans up an admitted answer even if its stream never starts.

    The response, not the endpoint, runs the generator. If the client is gone before
    the first event (``http.response.start`` fails, or the disconnect listener wins),
    the generator is never started and so never runs its ``finally``.
    """

    def __init__(self, chat: ChatService, prepared: PreparedAnswer, content: Any) -> None:
        super().__init__(
            content,
            ping=15,
            headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
        )
        self._chat = chat
        self._prepared = prepared

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        try:
            await super().__call__(scope, receive, send)
        finally:
            await asyncio.shield(self._chat.abandon(self._prepared))


def answer_response(chat: ChatService, prepared: PreparedAnswer) -> EventSourceResponse:
    async def events() -> Any:
        # aclosing: sse-starlette closes this iterator (aclose) when a send fails;
        # the close must reach chat.stream so it records the outcome and frees the slot.
        async with aclosing(chat.stream(prepared)) as stream:
            async for event in stream:
                yield ServerSentEvent(
                    event=event["event"], data=json.dumps(event["data"], ensure_ascii=False)
                )

    return _AnswerEventSourceResponse(chat, prepared, events())


@router.get("/llm/status", summary="Answer model configuration, budget and usage today")
async def llm_status(chat: ChatServiceDep) -> LlmStatus:
    return LlmStatus.model_validate(await chat.status())
