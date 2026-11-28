"""ORM models for documents and their embedded chunks (migration ``0002``)."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from nexus_api.db.base import Base
from nexus_api.embeddings.base import EMBEDDING_DIMENSION


class DocumentStatus(StrEnum):
    PENDING = "pending"
    PROCESSING = "processing"
    READY = "ready"
    FAILED = "failed"


class FailureCode(StrEnum):
    """Why a document ended in ``failed``. Stable values: the UI and API rely on them."""

    NO_EXTRACTABLE_TEXT = "no_extractable_text"  # e.g. a scanned PDF (OCR is out of scope)
    EXTRACTION_FAILED = "extraction_failed"
    EMBEDDING_FAILED = "embedding_failed"
    TEXT_LIMIT_EXCEEDED = "text_limit_exceeded"  # extracted text above the configured caps
    INTERRUPTED = "interrupted"  # process stopped or restarted mid-ingestion
    INTERNAL_ERROR = "internal_error"


# Stored as VARCHAR + CHECK rather than a PostgreSQL ENUM: adding or removing a value
# is a one-line constraint change, and downgrades stay simple.
_status_type = Enum(
    DocumentStatus,
    native_enum=False,
    create_constraint=False,
    length=16,
    values_callable=lambda enum: [member.value for member in enum],
    validate_strings=True,
)


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    filename: Mapped[str] = mapped_column(String(255))
    sha256: Mapped[str] = mapped_column(String(64), unique=True)
    size_bytes: Mapped[int] = mapped_column(Integer)
    page_count: Mapped[int] = mapped_column(Integer)
    status: Mapped[DocumentStatus] = mapped_column(_status_type)
    error_code: Mapped[str | None] = mapped_column(String(64))
    attempts: Mapped[int] = mapped_column(Integer, default=1)
    embedding_model: Mapped[str | None] = mapped_column(String(255))
    chunk_count: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'processing', 'ready', 'failed')", name="status_valid"
        ),
        CheckConstraint("page_count >= 1", name="page_count_positive"),
        Index("ix_documents_created_at_id", created_at.desc(), id.desc()),
    )


class Chunk(Base):
    __tablename__ = "chunks"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    # No separate index: uq_chunks_document_id_ordinal (document_id, ordinal)
    # already serves lookups, filters and the cascade delete by document_id.
    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"))
    ordinal: Mapped[int] = mapped_column(Integer)
    page_start: Mapped[int] = mapped_column(Integer)
    page_end: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)
    token_count: Mapped[int] = mapped_column(Integer)
    embedding: Mapped[list[float]] = mapped_column(Vector(EMBEDDING_DIMENSION))

    __table_args__ = (
        UniqueConstraint("document_id", "ordinal", name="uq_chunks_document_id_ordinal"),
        CheckConstraint("page_start >= 1 AND page_end >= page_start", name="page_range_valid"),
        Index(
            "ix_chunks_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_with={"m": 16, "ef_construction": 64},
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )


# --- M2: conversations ----------------------------------------------------------------


class MessageRole(StrEnum):
    USER = "user"
    ASSISTANT = "assistant"


class MessageStatus(StrEnum):
    """Lifecycle of a message. User messages are always ``complete``."""

    STREAMING = "streaming"  # generation in progress (holds a budget reservation)
    COMPLETE = "complete"
    NO_CONTEXT = "no_context"  # nothing retrieved: fixed answer, Claude not called
    TRUNCATED = "truncated"  # stop_reason max_tokens
    REFUSED = "refused"  # stop_reason refusal (no automatic fallback)
    FAILED = "failed"  # upstream or internal error; partial text kept
    CANCELLED = "cancelled"  # client disconnected; partial text kept
    INTERRUPTED = "interrupted"  # process stopped mid-generation; found at startup


_MESSAGE_STATUSES = ", ".join(f"'{s.value}'" for s in MessageStatus)


class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    title: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (Index("ix_conversations_updated_at", updated_at.desc()),)


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE")
    )
    role: Mapped[str] = mapped_column(String(16))
    content: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(16))
    # Snapshot of validated citations (filename, pages, passage text): history stays
    # readable after a document is deleted or re-ingested.
    citations: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    retrieved: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    grounded: Mapped[bool | None] = mapped_column(Boolean)
    model: Mapped[str | None] = mapped_column(String(100))
    input_tokens: Mapped[int | None] = mapped_column(Integer)  # uncached input only
    cache_creation_tokens: Mapped[int | None] = mapped_column(Integer)
    cache_read_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    reserved_cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    stop_reason: Mapped[str | None] = mapped_column(String(32))
    stop_category: Mapped[str | None] = mapped_column(String(64))
    error_code: Mapped[str | None] = mapped_column(String(64))
    ttft_ms: Mapped[int | None] = mapped_column(Integer)
    total_ms: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint("role IN ('user', 'assistant')", name="role_valid"),
        CheckConstraint(f"status IN ({_MESSAGE_STATUSES})", name="status_valid"),
        Index("ix_messages_conversation_id_created_at", "conversation_id", "created_at"),
        Index("ix_messages_created_at", "created_at"),
    )


class LlmUsage(Base):
    """One row per admitted generation: the budget ledger (ADR 0020).

    ``id`` is the assistant message id, deliberately without a foreign key, so that
    deleting a conversation never deletes spend. ``cost_usd`` is NULL until the actual
    cost is known; budget sums use ``COALESCE(cost_usd, reserved_cost_usd)``.
    """

    __tablename__ = "llm_usage"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    model: Mapped[str] = mapped_column(String(100))
    reserved_cost_usd: Mapped[Decimal] = mapped_column(Numeric(12, 6))
    cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (Index("ix_llm_usage_created_at", "created_at"),)
