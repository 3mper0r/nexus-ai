# ADR 0012: Bounded in-process background ingestion with explicit recovery

* Status: Accepted (M1, 2026-11-27)

## Context
Ingesting a 100-page PDF takes tens of seconds on CPU. Holding the HTTP request open is
fragile (proxy timeouts, retries that duplicate work). M1 must not add Redis or another
queue service.

## Decision
* `POST /api/v1/documents` validates the file synchronously (size, magic bytes, structure,
  encryption, page count), stores a `pending` row and answers **202 Accepted** with a
  `Location` header. Clients poll `GET /api/v1/documents/{id}` (the web UI refreshes every
  2 s while anything is active).
* `IngestionRunner` (one per process, created in the lifespan):
  * **Bounded queue:** at most `INGEST_QUEUE_CAPACITY` (8) accepted-but-unfinished
    documents; beyond that **503 `ingestion_queue_full` + `Retry-After: 10`**. A slot is
    reserved before any database write and released if the write fails.
  * **Controlled concurrency:** `asyncio.Semaphore(INGEST_MAX_CONCURRENCY=1)`; CPU is the
    bottleneck, so more parallelism only adds contention.
  * **Off the event loop:** page extraction, chunking and each embedding batch run via
    `asyncio.to_thread`. Work is split per page / per batch, so cancellation takes effect
    between steps (a running thread cannot be interrupted).
  * **Short transactions:** `pending→processing`, the final write and failure marking are
    separate transactions; no connection is held while parsing or embedding (verified by
    an integration test that checks the pool and `pg_stat_activity` mid-embedding).
  * **Atomic completion:** chunks + `ready` in one transaction, guarded by
    `SELECT … FOR UPDATE` on a still-`processing` row (a concurrent delete/reset wins
    cleanly).
  * Task references are kept in a dict until completion (the event loop holds only weak
    references to tasks).
* **Interruption handling:**
  * Graceful shutdown cancels in-flight tasks and marks their documents
    `failed/interrupted` before the engine is disposed.
  * **Startup recovery:** before accepting any upload, every `pending`/`processing` row is
    marked `failed/interrupted` (they are orphans of a crash). Until that has succeeded
    (it retries every 2 s while the database is down), uploads get **503
    `ingestion_not_ready`**. This ordering prevents recovery from failing fresh uploads.
* **Admission requires a loaded model** (added in the M1 audit): while the embedding model
  is loading or unavailable, uploads get **503 `embedding_model_unavailable`** +
  `Retry-After: 5` before anything is written. Previously they were accepted and then
  failed as `internal_error`.
* Failure codes: `no_extractable_text`, `text_limit_exceeded`, `extraction_failed`,
  `embedding_failed`, `interrupted`, `internal_error`. Deleting an active document is
  refused (409).

## Verified live (2026-11-27)
SIGTERM during ingestion → shutdown in ~0.1 s, document `failed/interrupted`. SIGKILL during
ingestion → row left `processing`; next startup logs `ingestion_recovered_interrupted_documents
count=1` and marks it `failed/interrupted`; re-upload → `ready` (attempt 3). Liveness stayed
≤ 3 ms throughout a 117-page ingestion.

## Limitations (accepted for M1)
* **Not durable:** PDF bytes live only in memory; a crash loses in-flight work and the user
  must re-upload.
* **Single process only:** a second API process would mark the first one's active documents
  as interrupted at its startup; the queue limit is per process.
* No automatic retries or backoff; no per-client fairness or rate limiting.
* A pathological PDF can occupy the single worker thread for a long time (threads cannot be
  killed; there is no per-document timeout).

## Path beyond M1
A PostgreSQL-backed job table (`SELECT … FOR UPDATE SKIP LOCKED`) with stored originals,
leases/heartbeats instead of "fail everything at startup", separate worker processes with
hard timeouts, and retries with backoff. That removes every limitation above without adding
Redis.

## Rejected
Synchronous ingestion in the request (timeouts, duplicate work on client retry), FastAPI
`BackgroundTasks` (no bound, no visibility, no recovery story), Redis/arq or Celery
(explicitly out of scope for M1).
