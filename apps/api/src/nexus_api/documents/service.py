"""Upload use case: validation, deduplication and hand-off to ingestion.

Deduplication and retry semantics (by SHA-256 of the file bytes):

==============================  ======================================  ============
Existing document               Outcome                                 HTTP status
==============================  ======================================  ============
none                            new ``pending`` document, ingested      202
``pending``/``processing``      returned unchanged (already queued)     200
``ready``, active model         returned unchanged (idempotent)         200
``ready``, another model        re-ingested, ``attempts + 1``           202
``failed``                      reset to ``pending``, ``attempts + 1``  202
==============================  ======================================  ============
"""

from __future__ import annotations

import asyncio
import hashlib
import re
import unicodedata
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from nexus_api.core.config import Settings
from nexus_api.db.models import Document, DocumentStatus
from nexus_api.documents import repository
from nexus_api.documents.errors import FileTooLargeError
from nexus_api.documents.ingestion import IngestionRunner
from nexus_api.documents.pdf import open_pdf

MAX_FILENAME_LENGTH = 255
DEFAULT_FILENAME = "document.pdf"
_UNSAFE_FILENAME_CHARS = re.compile(r"[\x00-\x1f\x7f<>:\"|?*]")


def sanitize_filename(raw: str | None) -> str:
    """Display name only: never used as a filesystem path.

    Strips any directory part (POSIX or Windows), control and reserved characters,
    normalises Unicode (NFC) and caps the length while keeping the extension.
    """
    if not raw:
        return DEFAULT_FILENAME
    name = raw.replace("\\", "/").rsplit("/", 1)[-1]
    name = unicodedata.normalize("NFC", name)
    # Control (Cc), format (Cf: e.g. U+202E RIGHT-TO-LEFT OVERRIDE, which can make
    # "report\u202efdp.exe" display as "reportexe.pdf"), surrogate, private-use and
    # line/paragraph separator characters have no place in a display name.
    name = "".join(
        ch for ch in name if unicodedata.category(ch) not in {"Cc", "Cf", "Cs", "Co", "Zl", "Zp"}
    )
    name = _UNSAFE_FILENAME_CHARS.sub("", name)
    name = " ".join(name.split()).strip(" .")
    if not name:
        return DEFAULT_FILENAME
    if len(name) > MAX_FILENAME_LENGTH:
        stem, dot, extension = name.rpartition(".")
        if dot and 0 < len(extension) <= 10:
            name = stem[: MAX_FILENAME_LENGTH - len(extension) - 1] + "." + extension
        else:
            name = name[:MAX_FILENAME_LENGTH]
    return name


def _inspect(data: bytes, max_pages: int) -> tuple[int, str]:
    pdf = open_pdf(data, max_pages=max_pages)
    return pdf.page_count, hashlib.sha256(data).hexdigest()


def _needs_ingestion(document: Document, active_model: str) -> bool:
    """Failed documents are retried; ready documents embedded by a different model
    are invisible to search (vector spaces differ) and are re-embedded."""
    if document.status == DocumentStatus.FAILED:
        return True
    return document.status == DocumentStatus.READY and document.embedding_model != active_model


@dataclass(frozen=True, slots=True)
class UploadOutcome:
    document: Document
    queued: bool  # True: (re)queued for ingestion -> 202; False: existing -> 200


async def accept_upload(
    session: AsyncSession,
    runner: IngestionRunner,
    settings: Settings,
    *,
    filename: str | None,
    data: bytes,
) -> UploadOutcome:
    if len(data) > settings.max_upload_bytes:
        raise FileTooLargeError(
            f"The file exceeds the {settings.max_upload_bytes // (1024 * 1024)} MiB limit."
        )
    # Structural validation before anything is stored: rejects non-PDFs, broken
    # and encrypted files, and page counts above the limit with a 4xx right away.
    # Hashing 20 MB takes tens of milliseconds, so it runs in the same thread.
    page_count, sha256 = await asyncio.to_thread(_inspect, data, settings.max_pdf_pages)
    display_name = sanitize_filename(filename)
    # Before touching the database: until startup recovery has run, the state of
    # active documents is not trustworthy (they may be orphans of a crash), and
    # without a loaded model every accepted document would fail.
    active_model = runner.ensure_accepting()

    existing = await repository.get_document_by_sha256(session, sha256)
    if existing is not None and not _needs_ingestion(existing, active_model):
        return UploadOutcome(document=existing, queued=False)

    runner.reserve()  # 503 when the queue is full; nothing has been written yet
    try:
        if existing is None:
            document = await repository.insert_pending_document(
                session,
                filename=display_name,
                sha256=sha256,
                size_bytes=len(data),
                page_count=page_count,
            )
        else:
            document = await repository.reset_for_reingestion(
                session, existing.id, filename=display_name, from_status=existing.status
            )
        await session.commit()
    except BaseException:
        runner.release()
        raise

    if document is None:
        # Lost a race with a concurrent upload of the same bytes: report the winner.
        runner.release()
        winner = await repository.get_document_by_sha256(session, sha256)
        if winner is None:  # pragma: no cover - the winner cannot vanish in between
            raise RuntimeError("deduplication race lost to a deleted document")
        return UploadOutcome(document=winner, queued=False)

    # Only after commit: the background task must see the row.
    runner.submit(document.id, data)
    return UploadOutcome(document=document, queued=True)
