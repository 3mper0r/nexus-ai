"""SQL for documents and chunks.

Transaction policy (ADR 0012): every function here is one short unit of work. No
transaction is ever open while PDF text is extracted or embeddings are computed;
the ingestion pipeline reads, works without a connection, then writes.
"""

from __future__ import annotations

import base64
import binascii
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

import numpy as np
from sqlalchemy import and_, delete, func, or_, select, text, update
from sqlalchemy import insert as orm_insert
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from nexus_api.db.models import Chunk, Document, DocumentStatus, FailureCode
from nexus_api.documents.chunking import TextChunk
from nexus_api.documents.errors import InvalidCursorError
from nexus_api.embeddings.base import FloatMatrix

ACTIVE_STATUSES = (DocumentStatus.PENDING, DocumentStatus.PROCESSING)


# --- Request-scoped operations (caller owns the session and commits) -------------


async def get_document(session: AsyncSession, document_id: uuid.UUID) -> Document | None:
    return await session.get(Document, document_id)


async def get_document_by_sha256(session: AsyncSession, sha256: str) -> Document | None:
    result = await session.execute(select(Document).where(Document.sha256 == sha256))
    return result.scalar_one_or_none()


async def insert_pending_document(
    session: AsyncSession, *, filename: str, sha256: str, size_bytes: int, page_count: int
) -> Document | None:
    """Insert a new pending document; ``None`` if the same content already exists.

    ``ON CONFLICT DO NOTHING`` makes concurrent uploads of identical bytes safe:
    exactly one insert wins, the loser re-reads the winner's row.
    """
    statement = (
        insert(Document)
        .values(
            id=uuid.uuid4(),
            filename=filename,
            sha256=sha256,
            size_bytes=size_bytes,
            page_count=page_count,
            status=DocumentStatus.PENDING,
            attempts=1,
        )
        .on_conflict_do_nothing(index_elements=[Document.sha256])
        .returning(Document)
    )
    result = await session.execute(statement)
    return result.scalar_one_or_none()


async def reset_for_reingestion(
    session: AsyncSession,
    document_id: uuid.UUID,
    *,
    filename: str,
    from_status: DocumentStatus,
) -> Document | None:
    """Move a ``failed`` (retry) or ``ready`` (model change) document back to ``pending``.

    Conditional on the status the caller observed, so two concurrent retries cannot
    both win. Existing chunks are removed in the same transaction: a pending
    document never has chunks, and chunk_count / embedding_model never describe
    chunks that no longer exist.
    """
    statement = (
        update(Document)
        .where(Document.id == document_id, Document.status == from_status)
        .values(
            status=DocumentStatus.PENDING,
            error_code=None,
            filename=filename,
            chunk_count=None,
            embedding_model=None,
            attempts=Document.attempts + 1,
            updated_at=func.now(),
        )
        .returning(Document)
    )
    document = (await session.execute(statement)).scalar_one_or_none()
    if document is not None:
        await session.execute(delete(Chunk).where(Chunk.document_id == document_id))
    return document


async def delete_document(session: AsyncSession, document_id: uuid.UUID) -> bool:
    """Delete a document that is not being ingested (chunks cascade)."""
    result = await session.execute(
        delete(Document)
        .where(Document.id == document_id, Document.status.not_in(ACTIVE_STATUSES))
        .returning(Document.id)
    )
    return result.scalar_one_or_none() is not None


@dataclass(frozen=True, slots=True)
class DocumentPage:
    items: list[Document]
    next_cursor: str | None


def _encode_cursor(document: Document) -> str:
    raw = f"{document.created_at.isoformat()}|{document.id}".encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _decode_cursor(cursor: str) -> tuple[datetime, uuid.UUID]:
    try:
        raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)).decode()
        created_at, document_id = raw.split("|", 1)
        return datetime.fromisoformat(created_at), uuid.UUID(document_id)
    except (binascii.Error, UnicodeDecodeError, ValueError) as exc:
        raise InvalidCursorError("The pagination cursor is invalid.") from exc


async def list_documents(session: AsyncSession, *, limit: int, cursor: str | None) -> DocumentPage:
    """Newest first, keyset-paginated on ``(created_at, id)``.

    Keyset pagination stays O(limit) at any depth and never skips or repeats rows
    when documents are inserted between page loads (unlike OFFSET).
    """
    statement = select(Document).order_by(Document.created_at.desc(), Document.id.desc())
    if cursor is not None:
        created_at, document_id = _decode_cursor(cursor)
        statement = statement.where(
            or_(
                Document.created_at < created_at,
                and_(Document.created_at == created_at, Document.id < document_id),
            )
        )
    rows = list((await session.execute(statement.limit(limit + 1))).scalars())
    has_more = len(rows) > limit
    items = rows[:limit]
    return DocumentPage(items=items, next_cursor=_encode_cursor(items[-1]) if has_more else None)


# --- Ingestion operations (each opens and commits its own short transaction) ------


async def mark_processing(
    sessionmaker: async_sessionmaker[AsyncSession], document_id: uuid.UUID
) -> bool:
    async with sessionmaker() as session, session.begin():
        result = await session.execute(
            update(Document)
            .where(Document.id == document_id, Document.status == DocumentStatus.PENDING)
            .values(status=DocumentStatus.PROCESSING, updated_at=func.now())
            .returning(Document.id)
        )
        return result.scalar_one_or_none() is not None


async def mark_failed(
    sessionmaker: async_sessionmaker[AsyncSession],
    document_id: uuid.UUID,
    code: FailureCode,
) -> None:
    async with sessionmaker() as session, session.begin():
        await session.execute(
            update(Document)
            .where(Document.id == document_id, Document.status.in_(ACTIVE_STATUSES))
            .values(status=DocumentStatus.FAILED, error_code=code, updated_at=func.now())
        )


async def save_ready(
    sessionmaker: async_sessionmaker[AsyncSession],
    document_id: uuid.UUID,
    *,
    chunks: Sequence[TextChunk],
    embeddings: FloatMatrix,
    embedding_model: str,
) -> bool:
    """Store all chunks and flip the document to ``ready`` in ONE transaction.

    Either the document is ``ready`` with every chunk, or (on any error) nothing
    is written and it stays ``processing`` for the caller to mark ``failed``.
    Returns ``False`` if the document was deleted or reset meanwhile.
    """
    if len(chunks) != len(embeddings):
        raise ValueError("one embedding per chunk is required")
    async with sessionmaker() as session, session.begin():
        locked = await session.execute(
            select(Document.id)
            .where(Document.id == document_id, Document.status == DocumentStatus.PROCESSING)
            .with_for_update()
        )
        if locked.scalar_one_or_none() is None:
            return False
        await session.execute(delete(Chunk).where(Chunk.document_id == document_id))
        if chunks:
            # One executemany round trip for all rows.
            await session.execute(
                orm_insert(Chunk),
                [
                    {
                        "id": uuid.uuid4(),
                        "document_id": document_id,
                        "ordinal": chunk.ordinal,
                        "page_start": chunk.page_start,
                        "page_end": chunk.page_end,
                        "text": chunk.text,
                        "token_count": chunk.token_count,
                        "embedding": vector,
                    }
                    for chunk, vector in zip(chunks, embeddings, strict=True)
                ],
            )
        await session.execute(
            update(Document)
            .where(Document.id == document_id)
            .values(
                status=DocumentStatus.READY,
                error_code=None,
                embedding_model=embedding_model,
                chunk_count=len(chunks),
                updated_at=func.now(),
            )
        )
        return True


async def fail_interrupted_documents(sessionmaker: async_sessionmaker[AsyncSession]) -> int:
    """At startup: no ingestion can be running yet, so any active row was orphaned
    by a crash or restart. Mark it ``failed/interrupted`` so it is never stuck."""
    async with sessionmaker() as session, session.begin():
        result = await session.execute(
            update(Document)
            .where(Document.status.in_(ACTIVE_STATUSES))
            .values(
                status=DocumentStatus.FAILED,
                error_code=FailureCode.INTERRUPTED,
                updated_at=func.now(),
            )
            .returning(Document.id)
        )
        return len(result.all())


# --- Search ------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SearchHit:
    chunk_id: uuid.UUID
    document_id: uuid.UUID
    filename: str
    ordinal: int
    page_start: int
    page_end: int
    text: str
    score: float


async def search_chunks(
    session: AsyncSession,
    query_embedding: FloatMatrix,
    *,
    top_k: int,
    ef_search: int,
    embedding_model: str,
    document_ids: Sequence[uuid.UUID] | None = None,
) -> list[SearchHit]:
    """Approximate nearest-neighbour search by cosine distance over the HNSW index.

    * ``hnsw.ef_search`` is the candidate-list size (recall vs latency), set with
      ``set_config(..., is_local => true)`` so it applies to this transaction only.
    * ``hnsw.iterative_scan = relaxed_order`` (pgvector >= 0.8): when the planner
      scans the HNSW index and filters afterwards (e.g. most rows belong to another
      embedding model), the index keeps scanning until ``top_k`` matching rows are
      found, instead of returning only the matches among the first ``ef_search``
      candidates. Relaxed order may return rows slightly out of order, so results
      are re-sorted. (A selective ``document_ids`` filter is usually planned on the
      ``(document_id, ordinal)`` btree with an exact sort instead.)
    * Only chunks embedded by ``embedding_model`` are compared: vectors from
      different models live in unrelated spaces and their distances are meaningless.
    """
    vector = np.asarray(query_embedding, dtype=np.float32)
    distance = Chunk.embedding.cosine_distance(vector).label("distance")
    statement = (
        select(
            Chunk.id,
            Chunk.document_id,
            Document.filename,
            Chunk.ordinal,
            Chunk.page_start,
            Chunk.page_end,
            Chunk.text,
            distance,
        )
        .join(Document, Document.id == Chunk.document_id)
        .where(Document.embedding_model == embedding_model)
        .order_by(distance)
        .limit(top_k)
    )
    if document_ids:
        statement = statement.where(Chunk.document_id.in_(document_ids))

    async with session.begin():
        await session.execute(
            text(
                "SELECT set_config('hnsw.ef_search', :ef, true), "
                "set_config('hnsw.iterative_scan', 'relaxed_order', true)"
            ),
            {"ef": str(ef_search)},
        )
        rows = (await session.execute(statement)).all()

    hits = [
        SearchHit(
            chunk_id=row.id,
            document_id=row.document_id,
            filename=row.filename,
            ordinal=row.ordinal,
            page_start=row.page_start,
            page_end=row.page_end,
            text=row.text,
            score=round(1.0 - float(row.distance), 6),
        )
        for row in rows
    ]
    hits.sort(key=lambda hit: hit.score, reverse=True)
    return hits
