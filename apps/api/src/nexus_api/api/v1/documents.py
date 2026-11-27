"""Document upload, status and management endpoints.

Transport only: parsing the multipart form, choosing status codes and shaping
responses. Validation, deduplication and ingestion live in ``nexus_api.documents``.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, File, Query, Response, UploadFile, status
from pydantic import BaseModel, ConfigDict

from nexus_api.api.deps import DbSessionDep, IngestionRunnerDep, SettingsDep
from nexus_api.core.errors import ErrorResponse
from nexus_api.db.models import Document, DocumentStatus
from nexus_api.documents import repository
from nexus_api.documents.errors import DocumentBusyError, DocumentNotFoundError
from nexus_api.documents.service import accept_upload

router = APIRouter(prefix="/documents", tags=["documents"])


class DocumentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    filename: str
    status: DocumentStatus
    error_code: str | None
    size_bytes: int
    page_count: int
    chunk_count: int | None
    attempts: int
    embedding_model: str | None
    sha256: str
    created_at: datetime
    updated_at: datetime


class DocumentListOut(BaseModel):
    items: list[DocumentOut]
    next_cursor: str | None


def _out(document: Document) -> DocumentOut:
    return DocumentOut.model_validate(document)


_ERRORS: dict[int | str, dict[str, object]] = {
    code: {"model": ErrorResponse} for code in (404, 409, 413, 415, 422, 503)
}
_UPLOAD_RESPONSES: dict[int | str, dict[str, object]] = {
    200: {"model": DocumentOut, "description": "Duplicate of an existing document"},
    **_ERRORS,
}


@router.post(
    "",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Upload a PDF for ingestion",
    responses=_UPLOAD_RESPONSES,
)
async def upload_document(
    file: Annotated[UploadFile, File(description="PDF file")],
    response: Response,
    session: DbSessionDep,
    runner: IngestionRunnerDep,
    settings: SettingsDep,
) -> DocumentOut:
    # The body size is already bounded by BodySizeLimitMiddleware; this read is
    # therefore bounded too, and accept_upload re-checks the exact file size.
    data = await file.read()
    outcome = await accept_upload(session, runner, settings, filename=file.filename, data=data)
    response.status_code = status.HTTP_202_ACCEPTED if outcome.queued else status.HTTP_200_OK
    response.headers["Location"] = f"/api/v1/documents/{outcome.document.id}"
    return _out(outcome.document)


@router.get("", summary="List documents (newest first)", responses=_ERRORS)
async def list_documents(
    session: DbSessionDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    cursor: Annotated[str | None, Query(max_length=200)] = None,
) -> DocumentListOut:
    page = await repository.list_documents(session, limit=limit, cursor=cursor)
    return DocumentListOut(items=[_out(item) for item in page.items], next_cursor=page.next_cursor)


@router.get(
    "/{document_id}", summary="Get one document and its ingestion status", responses=_ERRORS
)
async def get_document(document_id: uuid.UUID, session: DbSessionDep) -> DocumentOut:
    document = await repository.get_document(session, document_id)
    if document is None:
        raise DocumentNotFoundError("Document not found.")
    return _out(document)


@router.delete(
    "/{document_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a document and its chunks",
    responses=_ERRORS,
)
async def delete_document(document_id: uuid.UUID, session: DbSessionDep) -> None:
    deleted = await repository.delete_document(session, document_id)
    if not deleted:
        existing = await repository.get_document(session, document_id)
        if existing is None:
            raise DocumentNotFoundError("Document not found.")
        raise DocumentBusyError("The document is still being processed.")
    await session.commit()
