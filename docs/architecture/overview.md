# Architecture overview (as of M2)

This document describes what is **implemented**. Planned components are listed
separately and explicitly marked as such.

## System context

```text
 Browser
   │  HTML / RSC stream
   ▼
┌───────────────────────────┐        server-side fetch          ┌──────────────────────────────┐
│  apps/web (Next.js 16)    │ ─────────────────────────────────▶ │  apps/api (FastAPI, ASGI)    │
│  Node 24 · :3000          │  /documents · /search · SSE answers│  uvicorn · 127.0.0.1:8000    │
│  - App Router, RSC        │  /search · X-Request-ID: web-<uuid>│  - IngestionRunner (asyncio) │
│  - Server Actions (upload)│  timeout: per call                 │  - worker threads: pypdf,    │
│  - server-only API client │                                    │    multilingual-e5-small     │
└───────────────────────────┘                                    └──────────────┬───────────────┘
                                                                                │ asyncpg (pool)
                                                                                ▼
                                                                 ┌──────────────────────────────┐
                                                                 │ PostgreSQL 17 + pgvector     │
                                                                 │ Docker · 127.0.0.1:5434→5432 │
                                                                 │ volume nexus_pgdata          │
                                                                 └──────────────────────────────┘
```

The browser never calls the API directly: pages are Server Components, uploads go through
a Server Action and streamed answers through a Route Handler proxy, so API access happens
Next.js-server → API. The API in turn calls the Anthropic Messages API (via LangChain's
`ChatAnthropic`) for grounded answers; that is the only external service, and only when
`ANTHROPIC_API_KEY` is set. CORS is nevertheless
configured explicitly for the browser-direct calls (e.g. SSE streaming) planned
in later milestones.

## Backend (`apps/api`)

```text
src/nexus_api/
├── main.py              create_app() factory, lifespan, middleware + router wiring, run()
├── core/
│   ├── config.py        Settings (pydantic-settings): validated, immutable, env-only
│   ├── logging.py       structlog ↔ stdlib bridge; JSON or console rendering
│   ├── request_id.py    pure-ASGI middleware: correlation ID, access log, 500 boundary
│   ├── body_limit.py    pure-ASGI middleware: request body size limit (413)
│   └── errors.py        error envelope models, AppError, exception handlers
├── db/
│   ├── base.py          DeclarativeBase + constraint naming convention
│   ├── models.py        Document, Chunk (vector(384)), DocumentStatus, FailureCode
│   ├── engine.py        create_engine(), create_sessionmaker()
│   └── health.py        check_database(): SELECT 1 bounded by asyncio.timeout
├── embeddings/          (no HTTP, no DB)
│   ├── base.py          Embedder Protocol, EMBEDDING_DIMENSION
│   ├── e5.py            multilingual-e5-small via sentence-transformers (CPU, offline)
│   ├── provider.py      background model loading, readiness state
│   └── download.py      `make models`: fetch the pinned revision
├── llm/                 (no HTTP, no DB)
│   ├── generator.py     AnswerGenerator Protocol, typed events, GenerationError
│   ├── claude.py        ChatAnthropic implementation: request layout, events, error mapping
│   └── pricing.py       dated price table, cost and worst-case reservation
├── rag/                 (no HTTP)
│   ├── context.py       budgeted, per-document context assembly
│   ├── citations.py     citation validation and numbering, markers
│   ├── prompt.py        constant system prompt, history window
│   ├── repository.py    conversations/messages SQL, advisory-locked budget admission
│   ├── service.py       ChatService: admission, streaming, outcomes, recovery, status
│   └── errors.py        404/409/429/503 domain errors
├── documents/           (no HTTP)
│   ├── pdf.py           validation + per-page extraction (pypdf), normalisation
│   ├── chunking.py      page-bounded, token-aware chunking (pure functions)
│   ├── repository.py    SQL: dedupe insert, status transitions, atomic save, search
│   ├── ingestion.py     IngestionRunner: bounded queue, threads, recovery
│   ├── service.py       upload use case: validate → dedupe → reserve → insert → submit
│   └── errors.py        AppError subclasses (413/415/422/404/409/503)
└── api/
    ├── deps.py          typed dependencies: settings, engine, session, provider, runner
    └── v1/
        ├── router.py    /api/v1 prefix
        ├── health.py    /health/live, /health/ready (database + embedding model)
        ├── documents.py POST/GET/GET{id}/DELETE /documents
        ├── search.py    POST /search
        └── conversations.py  /conversations CRUD, POST …/messages (SSE), GET /llm/status
```

### Layering

| Layer | Knows about HTTP? | Example |
|---|---|---|
| `api/` (transport) | Yes | maps `DatabaseHealth` → 200/503 + response model |
| `documents/`, `embeddings/`, `db/` (domain/infrastructure) | No | `accept_upload()`, `chunk_pages()`, `search_chunks()`; domain errors subclass `AppError` |
| `core/` (cross-cutting) | Only at the edges | config, logging, middleware, error mapping |

### Request lifecycle

Middleware order (outermost first):

1. **Starlette `ServerErrorMiddleware`** (framework-provided, last resort)
2. **`CORSMiddleware`**: outermost of ours, so even our generated 500s carry CORS headers
3. **`RequestIdMiddleware`**: resolves/generates `X-Request-ID`, binds it to
   structlog context vars and `request.state`, writes it to the response header,
   logs `request_completed`, and converts unhandled exceptions into a generic
   500 envelope (stack trace logged, never returned)
4. **`BodySizeLimitMiddleware`**: rejects bodies above `MAX_UPLOAD_BYTES` + 64 KiB with 413,
   by `Content-Length` or while streaming (ADR 0015)
5. **`ExceptionMiddleware`**: our handlers for `HTTPException`, `RequestValidationError`, `AppError`
6. Router → dependencies → endpoint

### Application lifespan

* **Startup:** create the `AsyncEngine` (no connection is opened yet, so the API starts
  even if the DB is down) and the `async_sessionmaker`; start loading the embedding model in
  a worker thread (`EmbedderProvider`, including the torch import); start the
  `IngestionRunner`, whose first job is startup recovery (mark orphaned
  `pending`/`processing` documents `failed/interrupted`, retrying until the DB is
  reachable). Startup itself takes milliseconds; readiness reports the model as `loading`.
* **Shutdown:** stop the runner (cancel in-flight ingestion, mark it `interrupted`), stop
  waiting for the model loader, then `await engine.dispose()`. The order matters: the
  runner still writes status updates.

### Health semantics

| Probe | Touches DB | Healthy | Unhealthy |
|---|---|---|---|
| `GET /api/v1/health/live` | No | 200 `{"status":"ok"}` | process not serving |
| `GET /api/v1/health/ready` | Yes, `SELECT 1` within `DB_HEALTH_TIMEOUT_SECONDS` (bound covers pool wait, connect and query); plus embedding model state | 200 `ready` | 503 `not_ready`; `database.error: timeout \| unavailable`; `embedding_model.error: loading \| unavailable` |

### Error contract

```json
{"error": {"code": "validation_error", "message": "Request validation failed.",
           "request_id": "9f8d…", "details": [{"loc": ["query","limit"], "message": "…", "type": "int_parsing"}]}}
```

* `code` is a stable machine-readable slug; `message` is safe for display.
* Validation `details` omit Pydantic's echoed `input` (may contain secrets).
* Unhandled exceptions → `internal_error`, generic message, no internals.

### Database access

* One `AsyncEngine` per process, pool `DB_POOL_SIZE` + `DB_MAX_OVERFLOW`,
  `pool_pre_ping`, `pool_recycle=1800`, asyncpg connect timeout, `application_name=nexus-api`.
* One `AsyncSession` per request via `DbSessionDep`. **No implicit commit**: code that
  performs a unit of work calls `await session.commit()`; anything uncommitted is
  rolled back when the session closes (verified by integration tests). `commit()` returns
  the connection to the pool immediately, so long non-DB work after a commit (LLM calls)
  holds no connection. For streaming responses, see the guidance in ADR 0003.
* Alembic's `env.py` builds its URL from the same `Settings`, so migrations and the
  app can never target different databases.

## Ingestion and search (M1)

```text
POST /documents ─ size (413) ─ %PDF- magic (415) ─ pypdf open: encrypted / pages (422)
   ─ runner accepting? (503) ─ sha256 dedupe (200 existing) ─ reserve slot (503 + Retry-After)
   ─ INSERT pending · COMMIT ─ 202 + Location ───────────────────────────────────┐
                                                                                ▼
IngestionRunner task  [Semaphore(INGEST_MAX_CONCURRENCY=1)]         (in-process, asyncio)
   UPDATE → processing                                            (short transaction)
   to_thread: open + extract page 1..n (pypdf, NFKC, hyphen re-join)  (no DB connection)
   to_thread: chunk each page, ≤ 400 model tokens, overlap 60
   to_thread × batches of 16: embed "passage: …" (e5, 384-d, unit length)
   ONE transaction: SELECT … FOR UPDATE (still processing?) · INSERT chunks · status=ready
   on error → failed + error_code ∈ {no_extractable_text, extraction_failed,
              embedding_failed, interrupted, internal_error}

POST /search {query, top_k, document_ids?}
   to_thread: embed "query: …" ─ one transaction:
   set_config('hnsw.ef_search', 64, local) · set_config('hnsw.iterative_scan', 'relaxed_order', local)
   SELECT … FROM chunks JOIN documents WHERE embedding_model = <active model>
   ORDER BY embedding <=> :q LIMIT top_k   (HNSW, vector_cosine_ops) → page citations
```

Details and trade-offs: ADRs [0009](../decisions/0009-document-and-chunk-data-model.md)–[0015](../decisions/0015-upload-limits-and-validation.md).

## Grounded answers (M2)

```text
POST /api/v1/conversations/{id}/messages {content, document_ids?}
 prepare (JSON errors, nothing stored on rejection)
   key configured? (503) · chat recovered? (503) · generation slot free? (429 llm_busy)
   short txn: conversation exists (404) + last 4 completed Q&A pairs
   to_thread: embed "query: <previous question>\n<question>" → HNSW top-8 (short txn)
   assemble context (24k chars, per document, by position)
   no passages → store no_context turn, fixed answer, no API call
   ONE txn under pg_advisory_xact_lock: no streaming turn in this conversation (409);
       today's ledger COALESCE(cost, reservation) + worst case ≤ budget (429); insert user row +
       assistant row {status: streaming} + llm_usage row {reserved_cost_usd}
       (the ledger has no FK: deleting conversations never removes spend)
 stream (text/event-stream; NO database connection held)
   meta → ChatAnthropic.astream(system[cache] + history[cache on last] +
          [search_result per document (one text block per chunk), question])
        → delta… / citation (validated against what was sent) / markers [n](#cite-n)
   done {status, grounded, usage incl. cache, cost} | error {code, retryable}
   ONE short txn: content, citations snapshot, usage, cost, status
   disconnect/aclose → upstream closed, status cancelled (partial kept, shielded write)
   never started (client gone before the first byte) → response guard: cancelled, cost 0
 startup: streaming rows → interrupted (reservation keeps counting)
```

Details: ADRs [0016](../decisions/0016-rag-pipeline-and-answer-generator.md)–[0021](../decisions/0021-chat-ui-markdown-and-error-model.md).

## Frontend (`apps/web`)

```text
src/
├── app/
│   ├── layout.tsx         fonts (geist, self-hosted), metadata, AppShell
│   ├── globals.css        Tailwind v4 + @theme design tokens (dark, cyan accent)
│   ├── page.tsx           overview (static)
│   ├── status/page.tsx    static shell + <Suspense> dynamic HealthPanel (PPR)
│   ├── documents/         page.tsx (upload + library, PPR), actions.ts (Server Actions)
│   ├── search/page.tsx    GET form (next/form) + results streamed behind <Suspense>
│   ├── chat/              layout (conversation list, budget, disclosure), page, [id]/page, actions
│   └── api/conversations/[id]/messages/route.ts   SSE proxy (same-origin, JSON, abort-linked)
├── components/            app-shell, nav-link (client), status-badge
│   ├── documents/         upload-form, document-library, auto-refresh, delete-button
│   ├── search/            result-card (page citation, similarity)
│   └── chat/              chat-thread (streaming client), answer-markdown, conversation-nav/buttons
└── lib/
    ├── env.server.ts      server-only env parsing (lazy, validated)
    ├── format.ts          bytes, timestamps, page labels, failure messages
    └── api/
        ├── types.ts       response contracts + runtime type guards
        ├── client.ts      server-only apiRequest(): JSON/multipart/204, timeout, ApiResult
        ├── health.ts      getLiveness(), getReadiness()
        ├── documents.ts   listDocuments(), uploadDocument(), deleteDocument()
        ├── search.ts      searchDocuments()
        └── conversations.ts   conversations CRUD, getLlmStatus()
    (lib/sse.ts: fetch-stream SSE reader; types.ts: chat contracts + stream event guards)
```

* **Cache Components / PPR:** `/status` prerenders a static shell at build time; the
  `HealthPanel` calls `connection()` and streams at request time. The build therefore
  never contacts the backend, and every view shows fresh status.
* **Server-only boundary:** `API_BASE_URL`/`API_TIMEOUT_MS` are not `NEXT_PUBLIC_`, are
  read lazily, and modules that use them `import "server-only"` (importing them from a
  Client Component fails the build). Verified: zero occurrences in `.next/static`.
* **Failure handling:** `apiRequest` never throws for transport/protocol problems; it
  returns `ApiResult<T>` with `kind: timeout | network | http | invalid_response` (plus the
  API's error `code`), and the UI renders each state.
* **Uploads:** `<form action>` → `uploadAction` (Server Action, `useActionState`) → API
  multipart. `refresh()` re-renders the library in the same round trip; `AutoRefresh` calls
  `router.refresh()` every 2 s while any document is pending/processing.
* **Search** is a GET form, so result pages are shareable URLs (`/search?q=…&doc=…&k=…`).
* **Chat** streams through the Route Handler proxy into a client component that parses SSE
  with `fetch` + `ReadableStream`, batches deltas per animation frame and renders Markdown
  with `react-markdown` (raw HTML as text, no images); Stop aborts the fetch, which aborts
  the proxy's upstream request and makes the API cancel generation.
* The primary nav reads the pathname inside `<Suspense>` (required for dynamic routes such
  as `/chat/[id]` under Cache Components).

## Infrastructure

* `compose.yaml` with explicit project name `nexus` → container `nexus-postgres-1`,
  network `nexus_default`, volume `nexus_pgdata`. Isolated from other Compose projects.
* Port published on **loopback only** (`127.0.0.1:5434`). The host PostgreSQL on 5432
  is untouched.
* Health check `pg_isready`; `make db-up` uses `docker compose up --wait`.
* The `vector` extension is created **only** by Alembic migration `0001`.

## CI (`.github/workflows/ci.yml`)

Two parallel jobs on `ubuntu-24.04`:

* **backend**: Python from `.python-version`, uv 0.13.9 (via `astral-sh/setup-uv` pinned to the
  v10.4.0 commit SHA), `uv sync --locked`, ruff
  check, ruff format --check, mypy, unit tests (`not integration and not model`),
  `alembic upgrade head`, integration tests (`integration and not model`) against a
  `pgvector/pgvector:0.8.7-pg17-bookworm` service. **No model download**: a deterministic
  fake embedder is injected.
* **frontend**: Node from `.nvmrc`, `npm ci`, ESLint, `next typegen && tsc`, `next build`
  (no backend available, by design).

`.github/workflows/model.yml` (separate): downloads the pinned model (cached by revision)
and runs `pytest -m model` (real-model tests and end-to-end ingestion/search) on manual
dispatch, weekly, and on pushes that touch `embeddings/`, `documents/` or `uv.lock`.

Live Anthropic API tests (`-m llm`) are opt-in, local only; routine CI uses fakes and
offline wire-format contract tests.

## Planned (not implemented)

LangGraph agents and MCP/FastMCP tools (M3), Langfuse tracing and RAGAS evaluation (M4),
authentication, a durable job queue and containerised API/web (M5).
