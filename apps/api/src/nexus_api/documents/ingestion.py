"""In-process background ingestion with a bounded queue (ADR 0012).

Lifecycle of one document::

    upload ── reserve() ──► INSERT pending ── submit() ──► [semaphore] ──► processing
          ──► extract pages (thread, page by page) ──► chunk (thread)
          ──► embed (thread, batch by batch) ──► ONE transaction: chunks + ready
          └─► any failure ──► failed + error_code

Guarantees and their limits:

* **Bounded:** at most ``ingest_queue_capacity`` accepted-but-unfinished documents
  (queued + running), so memory is bounded by roughly
  ``max_upload_bytes * ingest_queue_capacity``. Beyond that, uploads get 503 +
  ``Retry-After``. At most ``ingest_max_concurrency`` run at once.
* **Non-blocking:** parsing, tokenisation and inference run in worker threads.
  Work is split into pages / batches, so cancellation (shutdown) takes effect
  between steps instead of after a whole document.
* **No connection held while working:** status changes and the final write are
  separate short transactions (see ``repository``).
* **Explicit interruption handling:** on graceful shutdown in-flight documents are
  marked ``failed/interrupted``; after a crash, the next startup does the same
  before accepting new uploads (``fail_interrupted_documents``).
* **Not durable:** the PDF bytes live only in memory. An interrupted document must be
  uploaded again; the sha256 deduplication turns that re-upload into a retry of the
  same record. Single process only: a second API process would mark the first
  one's in-flight work as interrupted at its startup.
"""

from __future__ import annotations

import asyncio
import time
import uuid

import numpy as np
import structlog
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from nexus_api.core.config import Settings
from nexus_api.db.models import FailureCode
from nexus_api.documents import repository
from nexus_api.documents.chunking import chunk_pages
from nexus_api.documents.errors import IngestionNotReadyError, IngestionQueueFullError
from nexus_api.documents.pdf import PageTextLimitError, extract_page_text, open_pdf
from nexus_api.embeddings.base import Embedder, FloatMatrix
from nexus_api.embeddings.provider import EmbedderProvider, EmbeddingModelUnavailableError

logger = structlog.stdlib.get_logger(__name__)

RETRY_AFTER_SECONDS = "10"


class IngestionFailedError(Exception):
    def __init__(self, code: FailureCode) -> None:
        super().__init__(code.value)
        self.code = code


class IngestionRunner:
    def __init__(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        embedder_provider: EmbedderProvider,
        settings: Settings,
        *,
        recovery_retry_seconds: float = 2.0,
    ) -> None:
        self._sessionmaker = sessionmaker
        self._embedder_provider = embedder_provider
        self._settings = settings
        self._recovery_retry_seconds = recovery_retry_seconds
        self._semaphore = asyncio.Semaphore(settings.ingest_max_concurrency)
        self._outstanding = 0
        self._tasks: dict[uuid.UUID, asyncio.Task[None]] = {}
        self._accepting = False
        self._recovery_task: asyncio.Task[None] | None = None

    # --- lifecycle --------------------------------------------------------------

    def start(self) -> None:
        self._recovery_task = asyncio.create_task(self._recover(), name="ingestion-recovery")

    async def _recover(self) -> None:
        """Fail orphaned documents, then start accepting uploads.

        Retries until the database is reachable: an API that starts while the
        database is down must not accept uploads before recovery has run, or it
        could later mark its own fresh documents as interrupted.
        """
        while True:
            try:
                count = await repository.fail_interrupted_documents(self._sessionmaker)
            except Exception as exc:
                logger.warning(
                    "ingestion_recovery_failed",
                    error_type=type(exc).__name__,
                    retry_in_seconds=self._recovery_retry_seconds,
                )
                await asyncio.sleep(self._recovery_retry_seconds)
                continue
            if count:
                logger.warning("ingestion_recovered_interrupted_documents", count=count)
            self._accepting = True
            return

    async def shutdown(self) -> None:
        self._accepting = False
        pending: list[asyncio.Task[None]] = list(self._tasks.values())
        if self._recovery_task is not None:
            pending.append(self._recovery_task)
        interrupted = [doc_id for doc_id, task in self._tasks.items() if not task.done()]
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        for document_id in interrupted:
            await self._fail(document_id, FailureCode.INTERRUPTED)

    # --- admission ----------------------------------------------------------------

    @property
    def accepting(self) -> bool:
        return self._accepting

    @property
    def outstanding(self) -> int:
        return self._outstanding

    def ensure_accepting(self) -> str:
        """Admission check before any database access; returns the active model id.

        Raises 503 until startup recovery has run, and while the embedding model is
        loading or unavailable (accepting then would only produce failed documents).
        """
        if not self._accepting:
            raise IngestionNotReadyError(
                "Document ingestion is starting up.", headers={"Retry-After": "5"}
            )
        return self._embedder_provider.get().model_id

    def reserve(self) -> None:
        """Claim a queue slot before any database write. Raises 503 errors."""
        self.ensure_accepting()
        if self._outstanding >= self._settings.ingest_queue_capacity:
            raise IngestionQueueFullError(
                "Too many documents are being processed. Try again shortly.",
                headers={"Retry-After": RETRY_AFTER_SECONDS},
            )
        self._outstanding += 1

    def release(self) -> None:
        """Give back a reserved slot that was not submitted."""
        self._outstanding -= 1

    def submit(self, document_id: uuid.UUID, data: bytes) -> None:
        """Start ingestion for a reserved slot. The slot is released when it ends."""
        task = asyncio.create_task(self._run(document_id, data), name=f"ingest-{document_id}")
        # Keep a strong reference: the event loop only holds weak references to
        # tasks, so an unreferenced task can be garbage-collected mid-flight.
        self._tasks[document_id] = task
        task.add_done_callback(lambda _: self._tasks.pop(document_id, None))

    def is_active(self, document_id: uuid.UUID) -> bool:
        return document_id in self._tasks

    # --- pipeline -------------------------------------------------------------------

    async def _run(self, document_id: uuid.UUID, data: bytes) -> None:
        try:
            async with self._semaphore:
                await self._ingest(document_id, data)
        finally:
            self._outstanding -= 1

    async def _ingest(self, document_id: uuid.UUID, data: bytes) -> None:
        log = logger.bind(document_id=str(document_id))
        try:
            claimed = await repository.mark_processing(self._sessionmaker, document_id)
        except Exception:
            # Database unavailable: the row stays pending and is marked interrupted
            # by the next startup's recovery (documented limitation, ADR 0012).
            log.exception("ingestion_claim_failed")
            return
        if not claimed:
            log.info("ingestion_skipped", reason="document no longer pending")
            return
        started = time.perf_counter()
        try:
            pages = await self._extract(data)
            embedder = self._embedder()
            chunks = await asyncio.to_thread(
                chunk_pages,
                pages,
                embedder,
                max_tokens=self._settings.chunk_tokens,
                overlap_tokens=self._settings.chunk_overlap_tokens,
            )
            if not chunks:
                raise IngestionFailedError(FailureCode.NO_EXTRACTABLE_TEXT)
            embeddings = await self._embed([chunk.text for chunk in chunks])
            saved = await repository.save_ready(
                self._sessionmaker,
                document_id,
                chunks=chunks,
                embeddings=embeddings,
                embedding_model=embedder.model_id,
            )
        except asyncio.CancelledError:
            raise
        except IngestionFailedError as failure:
            log.warning("ingestion_failed", code=failure.code)
            await self._fail(document_id, failure.code)
            return
        except Exception:
            log.exception("ingestion_crashed")
            await self._fail(document_id, FailureCode.INTERNAL_ERROR)
            return
        log.info(
            "ingestion_completed" if saved else "ingestion_discarded",
            pages=len(pages),
            chunks=len(chunks),
            seconds=round(time.perf_counter() - started, 3),
        )

    async def _extract(self, data: bytes) -> list[str]:
        """Extract page by page, stopping as soon as a text limit is exceeded."""
        pages: list[str] = []
        total_chars = 0
        try:
            pdf = await asyncio.to_thread(open_pdf, data, max_pages=self._settings.max_pdf_pages)
            for index in range(pdf.page_count):
                text = await asyncio.to_thread(
                    extract_page_text, pdf, index, max_chars=self._settings.max_page_chars
                )
                total_chars += len(text)
                if total_chars > self._settings.max_document_chars:
                    raise PageTextLimitError(f"document: {total_chars} characters")
                pages.append(text)
        except (asyncio.CancelledError, IngestionFailedError):
            raise
        except PageTextLimitError as exc:
            logger.warning("pdf_text_limit_exceeded", detail=str(exc))
            raise IngestionFailedError(FailureCode.TEXT_LIMIT_EXCEEDED) from exc
        except Exception as exc:
            logger.warning("pdf_extraction_error", error_type=type(exc).__name__)
            raise IngestionFailedError(FailureCode.EXTRACTION_FAILED) from exc
        if not any(pages):
            raise IngestionFailedError(FailureCode.NO_EXTRACTABLE_TEXT)
        return pages

    def _embedder(self) -> Embedder:
        try:
            return self._embedder_provider.get()
        except EmbeddingModelUnavailableError as exc:
            raise IngestionFailedError(FailureCode.EMBEDDING_FAILED) from exc

    async def _embed(self, texts: list[str]) -> FloatMatrix:
        try:
            embedder = self._embedder()
            size = self._settings.embedding_batch_size
            batches = [
                await asyncio.to_thread(embedder.embed_passages, texts[i : i + size])
                for i in range(0, len(texts), size)
            ]
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("embedding_error", error_type=type(exc).__name__)
            raise IngestionFailedError(FailureCode.EMBEDDING_FAILED) from exc
        return np.concatenate(batches).astype(np.float32, copy=False)

    async def _fail(self, document_id: uuid.UUID, code: FailureCode) -> None:
        try:
            await repository.mark_failed(self._sessionmaker, document_id, code)
        except Exception:
            # The row stays active; the next startup marks it interrupted.
            logger.exception("ingestion_mark_failed_error", document_id=str(document_id))
