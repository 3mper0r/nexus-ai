# M1 Implementation Plan: Document Ingestion & Semantic Retrieval

* Date: 2026-11-27
* Status: **Approved 2026-11-27 with amendments.** D1 and D4 approved as proposed.
  **D2 changed:** `pypdf` (BSD) replaces PyMuPDF. **D3 changed:** `intfloat/multilingual-e5-small`
  replaces `bge-small-en-v1.5` (English + German retrieval). Where this plan still mentions
  PyMuPDF or bge, the amendments win; the M1 ADRs and report are authoritative.
* Builds on: [M0 report](m0-report.md), ADRs [0001–0008](../decisions/).

---

## 0. Basis for this plan, and the decisions that need your approval

**The seven-day roadmap was never written into the repository.** The only approved
sources are the M0 master specification (technology stack, the "aggressive seven-day
roadmap", the rule to *"introduce dependencies when they become necessary"*) and the
ordered *Outstanding work* list in the accepted M0 report (§11):

> Document ingestion (PyMuPDF) and first domain tables · Hugging Face embeddings + HNSW
> index · retrieval API · LangGraph agent with Claude · SSE streaming · MCP/FastMCP tools ·
> Langfuse tracing · RAGAS evaluation · authentication · containerised API/web.

This plan assumes the following milestone split. **Please confirm or correct it.** If the
approved roadmap differed, the scope in §1 changes.

| Day | Milestone | Content |
|---|---|---|
| 1 | M0 ✅ | Foundation (done, CI green) |
| **2** | **M1** | **PDF ingestion → chunks → Hugging Face embeddings → pgvector HNSW → retrieval API + minimal UI** |
| 3 | M2 | Grounded answers with Claude, citations, SSE streaming |
| 4 | M3 | LangGraph agent, MCP/FastMCP tool server |
| 5 | M4 | Langfuse tracing, RAGAS evaluation |
| 6 | M5 | Authentication, containerised API/web, hardening |
| 7 | M6 | Polish, demo data, documentation, portfolio write-up |

**Decisions that need your sign-off** (my recommendation comes first in each):

| # | Decision | Recommendation | Alternative |
|---|---|---|---|
| D1 | M1 scope | Retrieval only, **no LLM calls** (Claude arrives in M2) | Also add a first Claude answer endpoint |
| D2 | PyMuPDF licence (**AGPL-3.0** or commercial) | Keep PyMuPDF, as the spec requires. Add an AGPL-compatible `LICENSE` to the repo (none exists today) and document the obligation | Swap in `pypdf` (BSD), which has weaker layout extraction and would change the approved stack |
| D3 | Embedding model | `BAAI/bge-small-en-v1.5` (384-dim, MIT, CPU-friendly, English) | `intfloat/multilingual-e5-small` (384-dim, multilingual) if non-English documents matter |
| D4 | Ingestion execution | `202 Accepted` + in-process background task + status polling | Synchronous request (simpler, but blocks for tens of seconds), or a Redis job queue (new infrastructure) |

---

## 1. Objectives and scope

**Objective:** a user can upload a PDF, watch it reach `ready`, and run semantic search
over every ingested document, getting back ranked passages with document and **page
citations**. This retrieval layer is what M2 will put Claude on top of.

### In scope

* First domain tables: `documents` and `chunks` (Alembic `0002`), with an HNSW index.
* PDF upload API with strict validation (size, type, page count, encryption).
* Text extraction per page with PyMuPDF, plus normalisation.
* Token-aware, page-aware chunking with overlap. Our own code, about 100 lines, fully tested.
* Local Hugging Face embeddings via `sentence-transformers`, using CPU-only PyTorch.
* Background ingestion with an explicit status machine, bounded concurrency, and recovery
  at startup.
* Document list, detail and delete endpoints, plus `POST /api/v1/search`.
* Readiness extended: the API is ready only once the DB is reachable **and** the
  embedding model is loaded.
* Frontend: `/documents` (upload + list with status) and `/search` (query + cited results).
* Tests, ADRs, learning guide and interview prep for M1.

### Out of scope (later milestones)

LLM calls/answer generation (M2) · SSE (M2) · LangChain/LangGraph (M3; M1 needs neither) ·
MCP (M3) · Langfuse/RAGAS (M4; M1 adds only a tiny golden-set recall check) ·
authentication and multi-tenancy (M5) · OCR for scanned PDFs · non-PDF formats · hybrid
(BM25 + vector) search · re-ranking · storing original files · Dockerfiles for the API/web.

---

## 2. Architectural decisions

Each of these becomes an ADR (`0009`–`0014`).

### ADR 0009: Document/chunk data model

```text
documents                                   chunks
─────────────────────────────────────       ──────────────────────────────────────────
id            uuid  PK                      id            uuid PK
filename      text  (display only)          document_id   uuid FK → documents ON DELETE CASCADE
sha256        char(64) UNIQUE               ordinal       int   (0..n-1), UNIQUE(document_id, ordinal)
size_bytes    int                           page_start    int   (1-based)
page_count    int   null until parsed       page_end      int
status        text  CHECK in (pending,      text          text
              processing, ready, failed)    token_count   int
error_code    text  null                    embedding     vector(384) NOT NULL
embedding_model text  null                  created_at    timestamptz
chunk_count   int   null
created_at / updated_at timestamptz
```

* **`status` is `text` + `CHECK`, not a PostgreSQL `ENUM`.** Adding a value to a PG enum
  needs its own migration and can't be rolled back cleanly. A CHECK constraint is
  trivially altered. Python side: a `StrEnum`.
* **`sha256 UNIQUE`** makes uploads idempotent. Re-uploading a `ready` document returns
  the existing record (200). Re-uploading a `failed` one resets and re-ingests it.
* **`embedding_model` is recorded per document**, so a future model change can detect
  and re-embed stale rows instead of silently mixing vector spaces.
* **Fixed dimension `vector(384)`.** Changing models means a migration and re-embedding.
  That's intentional, and documented.
* **The original file is not stored.** Citations need only page numbers. Blob storage is
  deferred until a feature needs it (e.g. a PDF viewer).

### ADR 0010: HNSW index, cosine distance, normalised vectors

* `CREATE INDEX … USING hnsw (embedding vector_cosine_ops) WITH (m = 16, ef_construction = 64)`,
  which are pgvector's defaults. They're appropriate for under 1M rows.
* The embeddings are L2-normalised, so cosine and inner product give the same ranking. We
  use cosine (`<=>`) for readability: `score = 1 - distance`.
* `SET LOCAL hnsw.ef_search = :n` per search transaction (config `SEARCH_EF_SEARCH`,
  default 40), so it's tunable without a session-wide side effect.
* **Filtered search** (`document_ids` filter): HNSW post-filters, so a selective filter can
  return fewer than `top_k` rows. We enable pgvector 0.8's iterative index scans
  (`SET LOCAL hnsw.iterative_scan = relaxed_order`) and re-sort, with an integration test
  that would fail without it.
* HNSW was chosen over IVFFlat because IVFFlat needs data present *before* it builds lists
  and degrades as data drifts. HNSW has no training step and better recall/latency, at the
  cost of slower builds and more memory. That's acceptable at portfolio scale.

### ADR 0011: Local embeddings with sentence-transformers on CPU-only PyTorch

* The model is loaded **once** in the lifespan, then held on `app.state`, and exposed
  through an `Embedder` `Protocol`:

  ```python
  class Embedder(Protocol):
      model_name: str
      dimension: int
      def embed_documents(self, texts: Sequence[str]) -> list[list[float]]: ...
      def embed_query(self, text: str) -> list[float]: ...
  ```

  Tests inject a deterministic `HashEmbedder`. This is the one abstraction M1 justifies:
  it isolates a slow, 100 MB+ dependency from almost every test.
* **The model revision is pinned to a commit hash** (`EMBEDDING_MODEL_REVISION`), and
  `trust_remote_code` is never enabled. This guards the supply chain: a model repo can
  change underneath a tag.
* **Model download is an explicit step** (`make models`). At runtime the API sets
  `HF_HUB_OFFLINE=1`, so a fresh pod never stalls on the Hub, and a missing model fails
  readiness with a clear message instead of downloading at request time.
* The bge query instruction prefix is applied in `embed_query` only. This is an
  asymmetric-retrieval detail that's easy to get wrong.
* **CPU-only torch** comes via a uv explicit index (`download.pytorch.org/whl/cpu`). The
  default Linux wheel pulls in roughly 3 GB of CUDA libraries, which we don't use.
* Considered and rejected: the Hugging Face Inference API (network dependency, token
  management, rate limits, data leaves the machine) and `fastembed`/ONNX (not the
  approved "Hugging Face" stack, though it's lighter; could be revisited in M5 for image
  size).

### ADR 0012: Ingestion runs as a bounded in-process background task

```text
POST /documents ──► validate (size, magic, pages) ──► INSERT status=pending ──► 202 {id}
                                                             │
                                  IngestionRunner (asyncio.Semaphore(INGEST_MAX_CONCURRENCY))
                                                             ▼
   status=processing ─► to_thread(extract) ─► chunk ─► to_thread(embed, batched)
                     ─► ONE transaction: INSERT chunks + status=ready     (or status=failed + error_code)
```

* The PDF bytes stay in memory only for the task's lifetime. They're bounded by
  `MAX_UPLOAD_BYTES × INGEST_MAX_PENDING`. When the queue is full, the API returns
  **503 + `Retry-After`**.
* **No DB session is held during extraction or embedding**, following the M0 audit
  finding. Status updates use short, separate transactions. The final write is atomic:
  a document is either `ready` with all its chunks or `failed` with none.
* CPU-bound work (PyMuPDF, torch) runs through `asyncio.to_thread`, so the event loop
  keeps serving requests. Both libraries release the GIL in their native code.
* **Lifecycle:** the runner is started and stopped in the lifespan. On shutdown, in-flight
  tasks are cancelled and awaited. On **startup**, any `pending`/`processing` rows (from a
  crash or restart) are marked `failed` with `error_code=interrupted`, so nothing is stuck
  forever. The user re-uploads, and the sha256 dedupe resets the record.
* Trade-off accepted for M1: a crash loses in-flight work, and the design is
  single-process only (one uvicorn worker). A durable queue (arq/Redis, or a
  PostgreSQL-backed `SKIP LOCKED` queue) is noted for M5.

### ADR 0013: Page-aware, token-aware chunking (own implementation)

* Unit = tokens of the **embedding model's own tokenizer**, so no chunk is ever silently
  truncated by the model's 512-token limit. Target is 400 tokens with an overlap of 60
  (configurable). The splitter tries separators in order: paragraph → line → sentence →
  word.
* Each chunk keeps `page_start` and `page_end` for citations.
* We considered `langchain-text-splitters` and rejected it for M1. It counts characters
  or tiktoken tokens by default rather than our model's tokens, doesn't track pages, and
  a focused 100-line chunker is easier to test and teaches more. LangChain enters with
  LangGraph in M3.

### ADR 0014: Upload path and limits

* The browser uploads to a **Next.js Server Action**, which forwards to the API
  server-side, consistent with ADR 0007 (the browser never calls the API directly). This
  needs `experimental.serverActions.bodySizeLimit` raised from the 1 MB default to match
  `MAX_UPLOAD_BYTES`.
* The API enforces its own limits regardless of the client:
  * The size is checked **while reading** the stream, not from `Content-Length`. The API
    returns 413 when the limit is exceeded.
  * Leading `%PDF-` magic bytes (415 otherwise). The client-sent content-type isn't trusted.
  * PyMuPDF must open the file. Encrypted files are rejected with 422 `pdf_encrypted`.
    Over `MAX_PDF_PAGES` gets 422 `pdf_too_many_pages`.
  * `filename` is display-only: normalised, length-capped, and never used as a path.

### Error codes added to the M0 envelope

`file_too_large` (413) · `unsupported_media_type` (415) · `pdf_invalid` / `pdf_encrypted` /
`pdf_too_many_pages` (422) · `document_not_found` (404) · `ingestion_queue_full` (503) ·
`embedding_model_unavailable` (503). Failed-document `error_code`s: `no_extractable_text`
(scanned PDF, OCR out of scope), `extraction_failed`, `embedding_failed`, `interrupted`.

---

## 3. Required dependencies and justification

Versions were checked on PyPI on 2026-11-27. Exact versions get locked in `uv.lock` at
implementation, with bounded ranges in `pyproject.toml` following M0 policy (stable,
mutually compatible, not automatically the newest major).

| Package | Latest seen | Licence | Why it is needed now |
|---|---|---|---|
| `pymupdf` | 2.2 | **AGPL-3.0** / commercial | Approved stack. Fast, accurate per-page text extraction, page counts, encryption detection (see D2) |
| `sentence-transformers` | 6.3.0 | Apache-2.0 | Approved "Hugging Face embeddings". Loads the model with tokenizer, pooling and normalisation correct for the model card |
| `torch` (CPU wheel index) | 2.15.0 | BSD-style | Inference runtime required by sentence-transformers. CPU index avoids CUDA wheels |
| `pgvector` (Python) | 0.5.1 | MIT | SQLAlchemy `Vector` column type and distance operators. Avoids hand-rolled casts |
| `python-multipart` | 0.0.34 | Apache-2.0 | Required by FastAPI for `UploadFile`/form parsing |

Transitive and notable: `transformers`, `huggingface-hub`, `tokenizers`, `numpy`,
`safetensors`. These come in through sentence-transformers and aren't declared directly.
`numpy` is the exception: we'll declare it if our code imports it.

**Dev:** none expected. PDFs for tests are **generated in-test with PyMuPDF**, so no binary
fixtures are committed.

**Frontend:** none. Server Actions, `FormData` and existing components are enough.

**Deliberately not added:** LangChain/LangGraph, `anthropic`, Langfuse, RAGAS,
`langchain-text-splitters`, `tiktoken`, Redis/arq, Celery.

**Compatibility checks at implementation:** sentence-transformers ↔ torch ↔ transformers
versions resolve together on Python 3.12. The PyMuPDF 2.x import path and API
(`pymupdf` vs the legacy `fitz`) are confirmed against its docs. mypy: torch and
sentence-transformers ship type hints, PyMuPDF partially; any `ignore_missing_imports`
override is scoped to one module and justified, as with asyncpg in M0.

**Footprint:** about 700 MB to 1 GB for the venv with CPU torch, plus about 130 MB for the
model. The CI cache keys on `uv.lock` (uv cache) and the model revision (HF cache).

---

## 4. Proposed repository changes

```text
apps/api/
├── pyproject.toml                      + deps, + [tool.uv.index] pytorch-cpu, + markers "model"
├── migrations/versions/0002_documents_and_chunks.py   NEW (tables, CHECK, HNSW, reversible)
├── src/nexus_api/
│   ├── core/config.py                  + ingestion/embedding/search settings (validated)
│   ├── core/errors.py                  + new AppError subclasses
│   ├── main.py                         lifespan: load embedder, start/stop runner, reconcile
│   ├── db/models.py                    NEW  Document, Chunk ORM models
│   ├── documents/                      NEW  domain package (no HTTP imports)
│   │   ├── pdf.py                      validate + extract pages (PyMuPDF), text normalisation
│   │   ├── chunking.py                 token/page-aware chunker (pure functions)
│   │   ├── repository.py               SQL: create/get/list/delete, bulk-insert chunks, status
│   │   ├── ingestion.py                IngestionRunner (semaphore, task set, reconcile)
│   │   └── search.py                   vector search query (ef_search, iterative scan, filters)
│   ├── embeddings/
│   │   ├── base.py                     NEW  Embedder Protocol
│   │   └── sentence_transformer.py     NEW  real implementation (offline, pinned revision)
│   └── api/v1/
│       ├── documents.py                NEW  POST/GET/GET{id}/DELETE
│       ├── search.py                   NEW  POST /search
│       └── health.py                   readiness: + embedding model check
├── scripts/download_model.py           NEW  `make models` (one-time, pinned revision)
└── tests/
    ├── fakes.py                        NEW  HashEmbedder, PDF factory helpers
    ├── unit/test_chunking.py, test_pdf.py, test_documents_api.py, test_search_api.py, test_config.py (+)
    ├── integration/test_documents_db.py, test_search_db.py, test_migrations.py
    └── model/test_embedder.py          NEW  real model, marker "model"
apps/web/src/
├── app/documents/page.tsx              NEW  list + status, upload form
├── app/documents/actions.ts            NEW  "use server" upload/delete actions
├── app/search/page.tsx                 NEW  query form + cited results
├── lib/api/{documents.ts,search.ts,types.ts(+)}   typed server-only calls + guards
├── lib/api/client.ts                   + apiPost / multipart support (same ApiResult contract)
├── components/{document-status.tsx,search-result.tsx}
└── next.config.ts                      serverActions.bodySizeLimit
.github/workflows/ci.yml                + HF model cache step, + `pytest -m model` step
Makefile                                + models, test-model targets
.env.example                            + new variables (documented)
docs/decisions/0009–0014                NEW ADRs
docs/architecture/overview.md           updated to M1 reality
docs/milestones/m1-report.md            at the end
docs/learning/m1-learning-guide.md, m1-interview-prep.md
LICENSE                                 NEW (pending D2)
README.md                               status, features, config, commands
```

**New configuration** (validated like M0's: bounds and cross-field checks, e.g.
`CHUNK_OVERLAP_TOKENS < CHUNK_TOKENS ≤ model max`):

`MAX_UPLOAD_BYTES` (20 MiB) · `MAX_PDF_PAGES` (500) · `EMBEDDING_MODEL_NAME` ·
`EMBEDDING_MODEL_REVISION` (required commit hash) · `EMBEDDING_BATCH_SIZE` (32) ·
`EMBEDDING_THREADS` (torch intra-op threads) · `CHUNK_TOKENS` (400) ·
`CHUNK_OVERLAP_TOKENS` (60) · `INGEST_MAX_CONCURRENCY` (1) · `INGEST_MAX_PENDING` (10) ·
`SEARCH_TOP_K_MAX` (20) · `SEARCH_EF_SEARCH` (40) · `HF_HOME` (model cache path).

**API surface:**

| Method & path | Success | Notes |
|---|---|---|
| `POST /api/v1/documents` (multipart `file`) | 202 new · 200 duplicate | returns `DocumentOut` |
| `GET /api/v1/documents?limit=&cursor=` | 200 | keyset pagination on `(created_at, id)` |
| `GET /api/v1/documents/{id}` | 200 / 404 | status, counts, error_code |
| `DELETE /api/v1/documents/{id}` | 204 / 404 | cascades chunks. Rejected with 409 while `processing` |
| `POST /api/v1/search` `{query, top_k, document_ids?}` | 200 | `[{chunk_id, document_id, filename, page_start, page_end, text, score}]` |

---

## 5. Implementation steps

Each step ends green (`make check`) before the next one starts.

1. **Dependencies and tooling.** Add the deps with the CPU torch index, `uv lock`, and
   confirm that `uv sync --locked` doesn't pull CUDA (inspect the installed packages).
   Pin the model revision, add `make models`, and confirm offline loading works.
2. **Config and errors.** Add the new `Settings` fields and validation, the new `AppError`
   subclasses, and unit tests.
3. **Schema.** ORM models plus migration `0002` (tables, CHECK, unique, FK cascade, HNSW).
   Integration tests cover upgrade, downgrade and re-upgrade, and the index exists with
   the expected opclass.
4. **PDF module.** Validation and per-page extraction, normalisation (NFC, strip `\x00`
   because PostgreSQL `text` rejects NUL, collapse whitespace), and the
   `no_extractable_text` path. Unit tests use generated PDFs (normal, empty-text,
   encrypted, truncated, oversized page count).
5. **Chunker.** Pure functions driven by a tokenizer protocol. Unit tests use a fake
   tokenizer, plus one test against the real tokenizer under marker `model`.
6. **Embedder.** Protocol, the real implementation, and the `HashEmbedder` fake. Model
   tests check dimension 384, unit norm, determinism, the query prefix, and a semantic
   sanity check (a relevant passage outranks an unrelated one).
7. **Repository and search SQL.** Bulk chunk insert (one executemany), status
   transitions, keyset listing, the search query with `SET LOCAL` settings. Integration
   tests run with known vectors.
8. **Ingestion runner.** Semaphore, task tracking, the atomic final write, failure
   mapping, startup reconciliation, shutdown cancellation. Integration tests use the fake
   embedder against the real DB.
9. **HTTP layer.** Document and search routers, readiness extension, OpenAPI response
   models. Unit/API tests cover every status code in §4.
10. **Frontend.** API client additions with runtime guards, Server Actions, `/documents`
    (status badge, refresh while any document is `pending`/`processing`) and `/search`
    (results with "p. 3–4" citations, empty and error states). Then lint, typecheck,
    build, and a no-backend build check.
11. **CI.** Cache the HF model keyed by revision, `make models`, and add a
    `pytest -m model` step. Note the CI duration change.
12. **Live verification.** Upload a real multi-page PDF, poll to `ready`, search, and
    check citations. Then cover the edge cases: duplicate, too large, wrong type,
    encrypted, scanned, the queue-full 503, delete during processing (409), kill -TERM
    during ingestion followed by a restart (expect `interrupted`), DB stopped mid-ingestion.
13. **Retrieval smoke evaluation.** A small committed golden set (about 10 queries over one
    generated or public-domain PDF) asserting recall@5 ≥ a stated threshold. This is a
    precursor to RAGAS in M4.
14. **Docs.** ADRs 0009–0014, architecture overview, README, M1 report, learning guide,
    interview prep.
15. **Review and audit**, then commit only after your approval.

---

## 6. Testing strategy

| Layer | Marker / location | Infra | What it proves |
|---|---|---|---|
| Unit | `tests/unit` | none | chunking invariants, PDF validation/extraction, config, API status codes and envelopes with `HashEmbedder` and a closed-port DB (as in M0) |
| Integration | `-m integration` | PostgreSQL | migration up/down, constraints (sha256 unique, CHECK, cascade), bulk insert, atomic ready/failed, reconciliation, search ordering, filtered search with iterative scan, `EXPLAIN` shows the HNSW index is used, no connection held during embedding (`pg_stat_activity`) |
| Model | `-m model` (new) | model in HF cache | real embedder dimension/normalisation/prefix, real-tokenizer chunk limits, golden-set recall@5 |
| Frontend | CI | none | ESLint, `tsc`, production build without a backend |
| Live | manual, recorded in the report | full stack | §5 step 12 scenarios |

**Chunker invariants** (each one a test): no empty chunks; every chunk ≤ `CHUNK_TOKENS`;
consecutive chunks overlap by ≤ `CHUNK_OVERLAP_TOKENS`; concatenation covers all source
text; page ranges are monotonic and within `1..page_count`; output is deterministic.

**Retained M0 policies:** integration and model tests **fail, not skip**, when their
infrastructure is missing. The throwaway `<db>_test` database is used, and the production
refusal stays in place. Local `make test-unit` still needs no Docker and no model.

---

## 7. Security and performance considerations

### Security

* **Untrusted PDFs** are the main new attack surface. We enforce a streamed size cap,
  magic bytes, a page cap, and reject encrypted files. Extraction is text-only (we never
  render, run JavaScript or follow embedded files). **Residual risk:** a pathological PDF
  could pin a thread, and threads can't be killed. Process isolation with a hard timeout
  is noted for M5 and documented as a known limitation.
* **No authentication yet** (M5). Mitigations: the API stays on `127.0.0.1`, the queue is
  bounded (503), uploads are size-capped, and deletes are by UUID only. This is stated
  plainly in the README.
* **Model supply chain:** pinned revision hash, `trust_remote_code=False`, offline at
  runtime, `safetensors` weights only.
* **Injection:** all SQL is parameterised through SQLAlchemy. `SET LOCAL` values are
  validated integers from `Settings`, never request input.
* **Data handling:** document text isn't logged. Logs carry `document_id`, sizes, counts
  and timings only. The original bytes aren't persisted.
* **Licence compliance:** the AGPL obligation for PyMuPDF (D2).
* **Error envelope:** extraction and embedding exceptions are logged with tracebacks and
  surfaced only as stable `error_code`s.

### Performance

* The model is loaded once (cold start of a few seconds; readiness reflects it). We run
  **one uvicorn worker**, because each worker would hold its own model copy, about 400 MB
  RSS.
* Embedding runs in batches (`EMBEDDING_BATCH_SIZE`) in a thread. The torch thread count
  is capped, so ingestion doesn't starve the event loop or search latency.
  `INGEST_MAX_CONCURRENCY=1` by default because CPU is the bottleneck.
* Chunks are bulk-inserted in a single executemany in one transaction. The HNSW index is
  maintained on insert, which is acceptable at this scale.
* Search costs one query embedding (about 10–30 ms on CPU) plus an HNSW scan with
  `ef_search=40` and `LIMIT top_k`. The target is **p95 < 150 ms** for 10k chunks on this
  machine, measured and reported.
* Memory is bounded by `MAX_UPLOAD_BYTES × (INGEST_MAX_PENDING + concurrency)`.
* Planned measurements for the report: ingestion throughput (pages/s, chunks/s), search
  p50/p95, API RSS before and after model load, CI duration delta.

---

## 8. Definition of done

- [ ] D1–D4 resolved and the roadmap confirmed
- [ ] Dependencies locked. `uv sync --locked` installs CPU-only torch (verified: no `nvidia-*` packages)
- [ ] Model revision pinned. `make models` works. The API loads offline. Readiness is 503 without the model
- [ ] Migration `0002` is reversible. Constraints and HNSW index are verified by integration tests
- [ ] Upload validation returns the exact statuses and error codes in §2/§4. Size enforced while streaming
- [ ] Ingestion is atomic (`ready` with all chunks, or `failed` with none). No DB connection held during extraction or embedding (verified)
- [ ] Bounded queue (503 + `Retry-After`). Shutdown cancels cleanly. Startup marks interrupted work `failed`
- [ ] Idempotent re-upload by sha256. Failed documents can be re-ingested
- [ ] Search returns ranked chunks with page citations. Filtering returns `top_k` results when available (iterative scan). `EXPLAIN` shows the HNSW index
- [ ] Frontend `/documents` and `/search` work end to end. Build passes without a backend. No server env in the client bundle
- [ ] Unit, integration and model suites pass locally. Chunker invariants covered. Golden-set recall@5 meets its threshold
- [ ] CI green on GitHub with the model step, including the setup-uv v10.4.0 change from M0
- [ ] Performance figures from §7 measured and reported
- [ ] ADRs 0009–0014, architecture overview, README, M1 report, learning guide and interview prep reflect what was built
- [ ] Reviewed, audited and approved before commit

---

## 9. M1 learning curriculum

Same format as the M0 guide: what, why, how it works internally, the TS/Node equivalent,
ecosystem differences, production mistakes, trade-offs. Each topic will reference the real
M1 source files.

1. **File uploads in FastAPI.** `UploadFile`, `SpooledTemporaryFile`, `python-multipart`,
   and why `Content-Length` can't be trusted.
2. **CPU-bound vs I/O-bound work in Python.** The GIL, `asyncio.to_thread`, when native
   code releases the GIL, and process pools.
3. **Background work in an ASGI process.** `asyncio.Task` lifetimes, keeping references,
   cancellation, semaphores, why FastAPI `BackgroundTasks` isn't a queue, and durable
   alternatives.
4. **PDF internals and PyMuPDF.** Pages, content streams, why text order isn't guaranteed,
   scanned vs text PDFs, encryption.
5. **Tokenization.** WordPiece/BPE, why the embedding model's tokenizer must count the
   chunks, and max sequence length truncation.
6. **Chunking strategy.** Size vs recall, overlap, structure-aware splitting, citations.
7. **Embeddings.** What a vector represents, normalisation, cosine vs inner product vs L2,
   asymmetric query prefixes, and why vectors from different models are incomparable.
8. **The Hugging Face ecosystem.** Hub, model cards, revisions, cache layout, offline mode,
   `trust_remote_code`, safetensors.
9. **PyTorch as a runtime dependency.** Wheels, CPU vs CUDA indexes, thread pools, memory.
10. **pgvector in depth.** The `vector` type, operators, HNSW internals (layers, `m`,
    `ef_construction`, `ef_search`), recall vs latency, filtering and iterative scans,
    `EXPLAIN`.
11. **SQLAlchemy beyond M0.** Relationships and cascades, custom column types, bulk
    inserts, keyset pagination.
12. **Alembic for indexes and constraints.** Why `CREATE INDEX CONCURRENTLY` can't run in a
    transaction, CHECK vs ENUM, writing safe downgrades.
13. **`typing.Protocol` and test doubles.** Structural typing, fakes vs mocks, and testing
    ML-adjacent code deterministically.
14. **Next.js Server Actions for uploads.** `FormData`, body size limits, and
    forwarding to a backend.

---

## 10. TypeScript-to-Python comparisons (preview for the guide)

**Interfaces ↔ `Protocol`.** Both are structural. Python checks only statically (mypy),
unless `@runtime_checkable` is added.

```ts
interface Embedder { readonly dimension: number; embedQuery(text: string): number[]; }
const fake: Embedder = { dimension: 384, embedQuery: () => new Array(384).fill(0) };
```
```python
class Embedder(Protocol):
    dimension: int
    def embed_query(self, text: str) -> list[float]: ...

class HashEmbedder:            # no "implements": satisfies the Protocol by shape
    dimension = 384
    def embed_query(self, text: str) -> list[float]: ...
```

**Offloading CPU work: `worker_threads` ↔ `asyncio.to_thread`.** Node threads don't share
a heap. Python threads share everything, but the GIL serialises Python bytecode, so the
gain comes only from native code that releases the GIL.

```ts
const vectors = await runInWorker("embed", texts);        // separate isolate, message passing
```
```python
vectors = await asyncio.to_thread(embedder.embed_documents, texts)   # same memory, GIL rules
```

**Concurrency limit: `p-limit` ↔ `asyncio.Semaphore`.**

```ts
const limit = pLimit(1); await limit(() => ingest(doc));
```
```python
async with self._semaphore:
    await self._ingest(doc)
```

**Fire-and-forget: a floating promise ↔ an `asyncio.Task`.** Both are lost silently unless
you keep a reference and observe errors. In Python, an unreferenced task can even be
garbage-collected mid-flight.

```python
task = asyncio.create_task(self._run(doc_id, data))
self._tasks.add(task); task.add_done_callback(self._tasks.discard)
```

**Union of string literals ↔ `StrEnum`.**

```ts
type DocumentStatus = "pending" | "processing" | "ready" | "failed";
```
```python
class DocumentStatus(StrEnum):
    PENDING = "pending"; PROCESSING = "processing"; READY = "ready"; FAILED = "failed"
```

**Generators.** The chunker yields lazily, just like `function*`.

```ts
function* chunks(pages: Page[]): Generator<Chunk> { /* yield … */ }
```
```python
def chunk_pages(pages: Sequence[Page], ...) -> Iterator[Chunk]: ...   # yield …
```

**Multipart upload: Route Handler ↔ FastAPI.**

```ts
const file = (await req.formData()).get("file") as File;   // whole file buffered
```
```python
async def upload(file: UploadFile) -> ...:
    data = await read_limited(file, settings.max_upload_bytes)   # spooled; read in bounded chunks
```

---

## 11. Technical interview questions (15)

These are the questions with key points for the answers. The full model answers and
common wrong answers go into `docs/learning/m1-interview-prep.md` after implementation,
grounded in the real code, as in M0. ★ marks questions aimed at engineers moving into AI.

1. **Why must chunk sizes be measured with the embedding model's tokenizer rather than
   characters or tiktoken?**
   Key points: models truncate silently past their max sequence length (512 for bge), so
   the tail of an oversize chunk is never embedded. Other tokenizers miscount. Wrong
   answer: "characters ÷ 4 is close enough".

2. ★ **Cosine similarity, inner product, L2: which do you use, and when are they
   equivalent?**
   Key points: for unit-normalised vectors, inner product = cosine and L2² = 2 − 2·cos,
   so all three give the same ranking. Use the metric the model was trained for, and the
   matching pgvector opclass. Wrong answer: "it doesn't matter, ever", or mismatching the
   index opclass and the query operator, which silently skips the index.

3. **HNSW vs IVFFlat in pgvector: trade-offs, and what `m`, `ef_construction` and
   `ef_search` control.**
   Key points: graph vs cluster lists, no training step vs needing data before the build,
   recall vs latency vs memory, build time.

4. ★ **A filtered vector search with `WHERE document_id = ANY(...)` returns 3 rows when
   you asked for 10. Why, and how do you fix it?**
   Key points: the ANN index post-filters within its `ef_search` candidate set. Fixes:
   iterative scans (pgvector 0.8), raising `ef_search`, partial indexes, or exact search
   for small filtered sets.

5. **Why is embedding inside `async def` without `to_thread` a production incident? And
   why does `to_thread` help at all, given the GIL?**
   Key points: it blocks the event loop for every request. Torch and PyMuPDF release the
   GIL in native code, so threads give real overlap. Pure-Python CPU work needs
   processes.

6. **What's wrong with FastAPI `BackgroundTasks` as a job queue? What did we choose, and
   what would you change at scale?**
   Key points: no durability, retries, visibility or backpressure, and it's tied to the
   process. Our choice: a bounded runner plus startup reconciliation. At scale: a durable
   queue (`SKIP LOCKED` or Redis), idempotent jobs, separate workers.

7. **How do you make ingestion atomic and idempotent?**
   Key points: sha256 uniqueness, the status machine, a single final transaction for
   chunks + `ready`, reconciliation of orphaned `processing` rows, retry semantics.

8. **Why not hold a database session while embedding a 300-page PDF?**
   Key points: pool exhaustion (pool size 5 + overflow 5), idle-in-transaction locks,
   long transactions blocking vacuum. Commit early and use short transactions.

9. **How do you safely accept untrusted PDF uploads?**
   Key points: a streamed size limit (not `Content-Length`), magic bytes rather than the
   client content-type, page and encryption limits, no filename-as-path, text-only
   extraction, resource isolation, and residual risk acknowledged.

10. ★ **You change the embedding model. What breaks, and how do you migrate without
    downtime?**
    Key points: vector spaces are incomparable and the dimension may change. Record the
    model per row, re-embed into a new column or table, dual-read or cut over, then
    rebuild the index.

11. ★ **Why pin a Hugging Face model by revision, and what does `trust_remote_code`
    risk?**
    Key points: mutable repos give reproducibility drift. `trust_remote_code` is arbitrary
    code execution at load time. Prefer safetensors over pickle.

12. **Why does a PostgreSQL `ENUM` for `status` cause migration pain, and what did we use
    instead?**
    Key points: `ALTER TYPE … ADD VALUE` restrictions and no clean removal. Use text +
    CHECK + a Python `StrEnum`.

13. **How do you test code that depends on a 130 MB neural model without making the unit
    suite slow or flaky?**
    Key points: a Protocol plus a deterministic fake, invariant-based tests, a separate
    model-marked suite, a golden set for quality, and fail-not-skip.

14. **Why do we run one uvicorn worker in M1, and what's the scaling path?**
    Key points: each process holds its own model copy, and the in-process queue would be
    split across workers. The path: separate embedding/ingestion workers, a model server,
    horizontal API replicas behind a durable queue.

15. ★ **Retrieval returns plausible but wrong passages. How do you diagnose it?**
    Key points: measure first (golden set, recall@k, MRR). Then inspect the chunking
    (boundaries, size), the query prefix, normalisation, `ef_search` recall vs exact
    search, filtering effects, and whether hybrid search or re-ranking is needed. This
    leads into RAGAS in M4.
