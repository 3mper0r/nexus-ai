# NEXUS AI

An AI engineering platform built as a production-quality portfolio project.

> **Status: Milestone 0 (Foundation).** This milestone delivers infrastructure and
> architecture only: a typed FastAPI backend, PostgreSQL + pgvector, a Next.js
> frontend, tests, CI and documentation. **No AI features (RAG, agents, embeddings,
> LLM calls) exist yet**; they arrive in later milestones.

## What exists today

| Area | Implemented in M0 |
|---|---|
| Backend | FastAPI app factory, lifespan-managed async DB engine, Pydantic Settings, structlog JSON/console logging, request-ID middleware, consistent error envelope, CORS, `/api/v1/health/live` + `/api/v1/health/ready` |
| Database | PostgreSQL 17 + pgvector 0.8.7 (Docker, `127.0.0.1:5434`), async SQLAlchemy 2.0, Alembic with migration `0001` enabling `vector` |
| Frontend | Next.js 16 App Router, strict TypeScript, Tailwind v4 design tokens, typed server-only API client with timeouts, live `/status` page |
| Quality | Ruff, mypy (strict), pytest (unit + real-DB integration), ESLint, `tsc`, GitHub Actions workflow |

## Repository layout

```text
nexus-ai/
├── apps/
│   ├── api/                 # Python backend (uv project, src layout)
│   │   ├── src/nexus_api/   # core/ (config, logging, errors, request id), db/, api/v1/
│   │   ├── migrations/      # Alembic (async env), versions/0001_enable_pgvector.py
│   │   └── tests/           # unit/ (no infra) and integration/ (real PostgreSQL)
│   └── web/                 # Next.js frontend (src/app, src/components, src/lib)
├── docs/
│   ├── architecture/        # system overview
│   ├── decisions/           # ADRs
│   ├── milestones/          # milestone reports
│   └── learning/            # learning guide + interview preparation
├── .github/workflows/ci.yml
├── compose.yaml             # PostgreSQL + pgvector (Compose project "nexus")
├── Makefile                 # developer command surface (`make help`)
├── .env.example             # backend + Compose configuration template
├── .nvmrc                   # Node 24.22.2
└── .python-version          # Python 3.12.16
```

## Prerequisites

| Tool | Version used | Notes |
|---|---|---|
| Python | 3.12.16 | uv picks it up from `.python-version` |
| uv | 0.13.9 | Python package/project manager |
| Node.js | 24.22.2 | `nvm use` reads `.nvmrc` |
| npm | 11.x | bundled with Node |
| Docker + Compose v2 | 26.x / 2.27+ | for PostgreSQL only |
| GNU Make | any | optional, wraps the commands below |

## Quick start

```bash
make setup            # create .env from .env.example, uv sync --locked, npm ci
make db-up            # start PostgreSQL + pgvector on 127.0.0.1:5434 (waits for healthy)
make migrate          # alembic upgrade head (enables pgvector)
make api              # http://127.0.0.1:8000  (docs: /docs)
make web              # http://localhost:3000  (in another terminal)
```

Open <http://localhost:3000/status> to see live liveness/readiness from the API.

## Configuration

Backend and Docker Compose share one file: **`.env`** at the repository root
(copy of [`.env.example`](.env.example), git-ignored). The backend reads **only**
process environment variables; the Makefile injects `.env` with
`uv run --env-file`.

| Variable | Default | Purpose |
|---|---|---|
| `APP_ENV` | `local` | `local` \| `test` \| `production` (production disables `/docs` and requires JSON logs) |
| `LOG_LEVEL` | `INFO` | Root log level |
| `LOG_FORMAT` | `console` | `console` (dev) or `json` |
| `API_HOST` / `API_PORT` | `127.0.0.1` / `8000` | Used by the `nexus-api` entry point |
| `CORS_ORIGINS` | `http://localhost:3000` | Comma-separated explicit origins; `*` is rejected |
| `POSTGRES_HOST` / `POSTGRES_PORT` | `127.0.0.1` / `5434` | Host-side address of the database |
| `POSTGRES_USER` / `POSTGRES_DB` | `nexus` / `nexus` | Also used by Compose to initialise the DB |
| `POSTGRES_PASSWORD` | **required** | No default, by design |
| `DB_POOL_SIZE` / `DB_MAX_OVERFLOW` | `5` / `5` | SQLAlchemy pool sizing |
| `DB_CONNECT_TIMEOUT_SECONDS` | `5` | asyncpg connect timeout |
| `DB_HEALTH_TIMEOUT_SECONDS` | `2` | Upper bound for the readiness DB check |

Frontend (optional `apps/web/.env.local`, see [`apps/web/.env.example`](apps/web/.env.example)):

| Variable | Default | Purpose |
|---|---|---|
| `API_BASE_URL` | `http://127.0.0.1:8000` | **Server-only.** API base URL as seen from the Next.js server |
| `API_TIMEOUT_MS` | `3000` | **Server-only.** Per-request timeout (100–30000) |

## Commands

Run `make help` for the full list.

| Command | What it does |
|---|---|
| `make db-up` / `make db-down` | Start (wait for healthy) / stop PostgreSQL. Data persists in volume `nexus_pgdata` |
| `make db-status`, `make db-logs`, `make db-psql` | Inspect the database container |
| `make db-reset` | **Destructive.** Deletes `nexus_pgdata` (asks for confirmation), recreates, migrates |
| `make migrate` / `make migrate-down` | Upgrade to head / revert one revision |
| `make migrate-status` | Current revision + history |
| `make migration name="..."` | Autogenerate a new revision |
| `make api` / `make web` | Run backend (reload) / frontend (dev) |
| `make lint`, `make typecheck`, `make format` | Ruff + ESLint, mypy + tsc, Ruff auto-format |
| `make test-unit` | Backend unit tests, **no infrastructure needed** |
| `make test-integration` | Backend integration tests against real PostgreSQL. **Fails** (never skips) if the DB is down |
| `make test` | All backend tests (requires `make db-up`) |
| `make api-check` / `make web-check` | Backend lint+types+unit / frontend lint+types+build |
| `make check` | Everything CI runs (requires `make db-up`) |

## Testing strategy

* **Unit tests** (`apps/api/tests/unit`) run the real ASGI app in-process via
  `httpx.ASGITransport` + `asgi-lifespan`. They are hermetic: environment variables
  are cleared per test and the database points at a closed port, which also exercises
  the *real* "database unavailable" and "database timeout" paths without mocks.
* **Integration tests** (`apps/api/tests/integration`, marker `integration`) use a real
  PostgreSQL. The session creates a throwaway `<POSTGRES_DB>_test` database, runs
  Alembic migrations against it, and drops it afterwards. Development data is never
  touched. The suite refuses to run with `APP_ENV=production` and only ever drops the
  derived `<POSTGRES_DB>_test` database. If PostgreSQL is unreachable the session
  **fails** with an actionable message.
* Deselect integration tests locally with `make test-unit` (`pytest -m "not integration"`).
* CI runs both, with a PostgreSQL service container.

## Documentation

* [Architecture overview](docs/architecture/overview.md)
* [Architecture decision records](docs/decisions/)
* [M0 implementation report](docs/milestones/m0-report.md)
* [M0 learning guide](docs/learning/m0-learning-guide.md) · [M0 interview preparation](docs/learning/m0-interview-prep.md)

## Known limitations (M0)

* No authentication/authorization yet; CORS is configured with `allow_credentials=False`.
* No Dockerfiles for the API or web app; only PostgreSQL is containerised.
* The GitHub Actions workflow has not yet run on GitHub (no remote configured).
* No AI functionality: see the roadmap in the M0 report.
