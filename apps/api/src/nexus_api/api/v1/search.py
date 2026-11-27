"""Semantic search over ingested chunks."""

from __future__ import annotations

import asyncio
import time
import uuid
from typing import Annotated

from fastapi import APIRouter
from pydantic import BaseModel, Field, StringConstraints

from nexus_api.api.deps import DbSessionDep, EmbedderProviderDep, SettingsDep
from nexus_api.core.errors import AppError, ErrorResponse
from nexus_api.documents.repository import search_chunks

router = APIRouter(prefix="/search", tags=["search"])

QueryText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1000)]


class SearchRequest(BaseModel):
    query: QueryText
    top_k: int = Field(default=5, ge=1)
    document_ids: list[uuid.UUID] | None = Field(default=None, max_length=50)


class SearchResult(BaseModel):
    chunk_id: uuid.UUID
    document_id: uuid.UUID
    filename: str
    ordinal: int
    page_start: int
    page_end: int
    text: str
    score: float


class SearchResponse(BaseModel):
    query: str
    results: list[SearchResult]
    embedding_model: str
    took_ms: float


class TopKTooLargeError(AppError):
    status_code = 422
    code = "top_k_too_large"


@router.post(
    "",
    summary="Semantic search with page citations",
    responses={code: {"model": ErrorResponse} for code in (422, 503)},
)
async def search(
    request: SearchRequest,
    session: DbSessionDep,
    provider: EmbedderProviderDep,
    settings: SettingsDep,
) -> SearchResponse:
    if request.top_k > settings.search_top_k_max:
        raise TopKTooLargeError(f"top_k must be at most {settings.search_top_k_max}.")
    started = time.perf_counter()
    embedder = provider.get()
    # ~15 ms of CPU-bound inference: off the event loop.
    query_vector = await asyncio.to_thread(embedder.embed_query, request.query)
    hits = await search_chunks(
        session,
        query_vector,
        top_k=request.top_k,
        ef_search=settings.search_ef_search,
        embedding_model=embedder.model_id,
        document_ids=request.document_ids,
    )
    return SearchResponse(
        query=request.query,
        results=[SearchResult.model_validate(hit, from_attributes=True) for hit in hits],
        embedding_model=embedder.model_id,
        took_ms=round((time.perf_counter() - started) * 1000, 2),
    )
