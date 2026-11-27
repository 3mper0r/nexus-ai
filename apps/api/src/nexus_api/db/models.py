"""ORM models for documents and their embedded chunks (migration ``0002``)."""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
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
