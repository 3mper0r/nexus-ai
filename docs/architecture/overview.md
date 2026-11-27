# Architecture overview (as of M0)

This document describes what is **implemented**. Planned components are listed
separately and explicitly marked as such.

## System context

```text
 Browser
   │  HTML / RSC stream
   ▼
┌───────────────────────────┐        server-side fetch          ┌──────────────────────────────┐
│  apps/web (Next.js 16)    │ ─────────────────────────────────▶ │  apps/api (FastAPI, ASGI)    │
│  Node 24 · :3000          │  GET /api/v1/health/*              │  uvicorn · 127.0.0.1:8000    │
│  - App Router, RSC        │  X-Request-ID: web-<uuid>          │                              │
│  - server-only API client │  timeout: API_TIMEOUT_MS           │                              │
└───────────────────────────┘                                    └──────────────┬───────────────┘
                                                                                │ asyncpg (pool)
                                                                                ▼
                                                                 ┌──────────────────────────────┐
                                                                 │ PostgreSQL 17 + pgvector     │
                                                                 │ Docker · 127.0.0.1:5434→5432 │
                                                                 │ volume nexus_pgdata          │
                                                                 └──────────────────────────────┘
```

The browser never calls the API directly in M0: the status page is a Server
Component, so API access happens Next.js-server → API. CORS is nevertheless
configured explicitly for the browser-direct calls (e.g. SSE streaming) planned
in later milestones.

## Backend (`apps/api`)

```text
src/nexus_api/
├── main.py            create_app() factory, lifespan, middleware + router wiring, run()
├── core/
│   ├── config.py      Settings (pydantic-settings): validated, immutable, env-only
│   ├── logging.py     structlog ↔ stdlib bridge; JSON or console rendering
│   ├── request_id.py  pure-ASGI middleware: correlation ID, access log, 500 boundary
│   └── errors.py      error envelope models, AppError, exception handlers
├── db/
│   ├── base.py        DeclarativeBase + constraint naming convention (no tables yet)
│   ├── engine.py      create_engine(), create_sessionmaker()
│   └── health.py      check_database(): SELECT 1 bounded by asyncio.timeout
└── api/
    ├── deps.py        typed dependencies: settings, engine, per-request session
    └── v1/
        ├── router.py  /api/v1 prefix
        └── health.py  /health/live, /health/ready (+ response models)
```

### Layering

| Layer | Knows about HTTP? | Example |
|---|---|---|
| `api/` (transport) | Yes | maps `DatabaseHealth` → 200/503 + response model |
| `db/`, future `services/` (domain/infrastructure) | No | `check_database()` returns a dataclass; domain errors subclass `AppError` |
| `core/` (cross-cutting) | Only at the edges | config, logging, middleware, error mapping |

### Request lifecycle

Middleware order (outermost first):

1. **Starlette `ServerErrorMiddleware`** (framework-provided, last resort)
2. **`CORSMiddleware`**: outermost of ours, so even our generated 500s carry CORS headers
3. **`RequestIdMiddleware`**: resolves/generates `X-Request-ID`, binds it to
   structlog context vars and `request.state`, writes it to the response header,
   logs `request_completed`, and converts unhandled exceptions into a generic
   500 envelope (stack trace logged, never returned)
4. **`ExceptionMiddleware`**: our handlers for `HTTPException`, `RequestValidationError`, `AppError`
5. Router → dependencies → endpoint

### Application lifespan

* **Startup:** create the `AsyncEngine` (no connection is opened yet, so the API starts
  even if the DB is down), the `async_sessionmaker`, and store them plus settings on `app.state`.
* **Shutdown:** `await engine.dispose()` closes all pooled connections (verified by an
  integration test that inspects `pg_stat_activity`).

### Health semantics

| Probe | Touches DB | Healthy | Unhealthy |
|---|---|---|---|
| `GET /api/v1/health/live` | No | 200 `{"status":"ok"}` | process not serving |
| `GET /api/v1/health/ready` | Yes, `SELECT 1` within `DB_HEALTH_TIMEOUT_SECONDS` (bound covers pool wait, connect and query) | 200 `ready` | 503 `not_ready` with `error: timeout \| unavailable` (unreachable, refused, auth/catalog errors) |

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

## Frontend (`apps/web`)

```text
src/
├── app/
│   ├── layout.tsx         fonts (geist, self-hosted), metadata, AppShell
│   ├── globals.css        Tailwind v4 + @theme design tokens (dark, cyan accent)
│   ├── page.tsx           overview (static)
│   └── status/page.tsx    static shell + <Suspense> dynamic HealthPanel (PPR)
├── components/            app-shell, nav-link (client), status-badge
└── lib/
    ├── env.server.ts      server-only env parsing (lazy, validated)
    └── api/
        ├── types.ts       response contracts + runtime type guards
        ├── client.ts      server-only apiGet(): timeout, no-store, typed ApiResult
        └── health.ts      getLiveness(), getReadiness()
```

* **Cache Components / PPR:** `/status` prerenders a static shell at build time; the
  `HealthPanel` calls `connection()` and streams at request time. The build therefore
  never contacts the backend, and every view shows fresh status.
* **Server-only boundary:** `API_BASE_URL`/`API_TIMEOUT_MS` are not `NEXT_PUBLIC_`, are
  read lazily, and modules that use them `import "server-only"` (importing them from a
  Client Component fails the build). Verified: zero occurrences in `.next/static`.
* **Failure handling:** `apiGet` never throws for transport/protocol problems; it returns
  `ApiResult<T>` with `kind: timeout | network | http | invalid_response`, and the UI
  renders each state.

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
  check, ruff format --check, mypy, unit tests, `alembic upgrade head`, integration tests
  against a `pgvector/pgvector:0.8.7-pg17-bookworm` service.
* **frontend**: Node from `.nvmrc`, `npm ci`, ESLint, `next typegen && tsc`, `next build`
  (no backend available, by design).

## Planned (not implemented)

Document ingestion (PyMuPDF), embeddings (Hugging Face), retrieval over pgvector,
LangGraph agents with Claude, MCP/FastMCP tools, SSE streaming, Langfuse tracing,
RAGAS evaluation, authentication.
