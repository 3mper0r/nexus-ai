# M0 Implementation Report: Project Foundation

* Date: 2026-11-24
* Status: **Accepted** after review and engineering audit (2026-11-24). Final local results:
  **57 tests passing: 44 unit, 13 integration.** Remote CI **verified** on GitHub Actions
  (2026-11-27, workflow run #1, commit `ac80017`: all jobs passed).

## 1. Executive summary

M0 delivers a reproducible full-stack foundation with no AI features:

* a typed FastAPI backend with validated configuration, structured logging, request
  correlation, a consistent error contract, and liveness/readiness probes;
* PostgreSQL 17 + pgvector 0.8.7 in an isolated Compose project, with async
  SQLAlchemy and Alembic (pgvector enabled by migration `0001`);
* a Next.js 16 frontend with design tokens and a live, server-rendered status page
  backed by a typed, timeout-bounded, server-only API client;
* 57 backend tests (44 unit and 13 real-database integration tests), strict linting and typing on both
  stacks, a CI workflow, and architecture, ADR, learning and interview documentation.

All quality gates pass locally and in GitHub Actions (run #1 on commit `ac80017`, see §7b).

## 2. Implemented functionality

| Requirement | Implementation | Verified by |
|---|---|---|
| Env-based config | `Settings` (pydantic-settings), frozen, `SecretStr` password, CORS/port/timeout validation, production rules | `tests/unit/test_config.py` (13 tests) |
| Lifespan | engine + sessionmaker created at startup, `engine.dispose()` at shutdown | unit startup test; integration `pg_stat_activity` test; live SIGTERM log |
| Structured logging | structlog ↔ stdlib bridge, JSON/console, request ID in context, tracebacks without locals | `test_request_id.py`, `test_errors.py` |
| Request correlation | pure ASGI middleware; validated incoming `X-Request-ID` else UUID4; header + logs + error body | 14 tests (incl. 30 concurrent requests) + live curl + web→API log correlation |
| Error handling | single envelope; 404/405/422/AppError/500; no internals leaked | `test_errors.py` (5 tests) |
| CORS | explicit origins, wildcard rejected, `X-Request-ID` exposed | unit test + live preflight |
| Versioned routing | `/api/v1` | all endpoint tests |
| Liveness | `/api/v1/health/live`, DB-independent | unit (DB down) + live with DB stopped |
| Readiness | `SELECT 1` within `DB_HEALTH_TIMEOUT_SECONDS`; 200 / 503 | unit (refused, timeout), integration (up, wrong password, missing DB, pool exhausted, repeated failures), live (stopped/recovered) |
| Database | `pgvector/pgvector:0.8.7-pg17-bookworm`, `127.0.0.1:5434`, volume, health check | live checks |
| Migrations | async Alembic env using `Settings`; `0001` enables `vector`; reversible | integration tests + `make migrate-status` |
| Session lifecycle | per-request `AsyncSession`, explicit commit, rollback on close/error | integration test |
| Frontend | App Router, strict TS, Tailwind v4 tokens, layout shell, overview + `/status` | lint/typecheck/build + live HTTP |
| API client | server-only, `no-store`, `AbortSignal.timeout`, runtime guards, `ApiResult<T>` | live: up / DB down / API down / API hanging |
| CI | backend + frontend jobs | GitHub Actions run #1 (`ac80017`): backend ✅, frontend ✅ |

## 3. Final repository structure

```text
nexus-ai/
├── .github/workflows/ci.yml
├── apps/
│   ├── api/
│   │   ├── alembic.ini
│   │   ├── migrations/{env.py, script.py.mako, versions/0001_enable_pgvector.py}
│   │   ├── pyproject.toml, uv.lock
│   │   ├── src/nexus_api/
│   │   │   ├── __init__.py, main.py
│   │   │   ├── api/{deps.py, v1/{router.py, health.py}}
│   │   │   ├── core/{config.py, errors.py, logging.py, request_id.py}
│   │   │   └── db/{base.py, engine.py, health.py}
│   │   └── tests/
│   │       ├── conftest.py
│   │       ├── unit/{conftest.py, test_app.py, test_config.py, test_errors.py, test_integration_safety.py, test_request_id.py}
│   │       └── integration/{conftest.py, test_database.py}
│   └── web/
│       ├── .env.example, .npmrc, package.json, package-lock.json
│       ├── eslint.config.mjs, next.config.ts, tsconfig.json
│       └── src/
│           ├── app/{layout.tsx, globals.css, page.tsx, status/page.tsx}
│           ├── components/{app-shell.tsx, nav-link.tsx, status-badge.tsx}
│           └── lib/{env.server.ts, api/{client.ts, health.ts, types.ts}}
├── docs/
│   ├── architecture/overview.md
│   ├── decisions/{README.md, 0001…0008}
│   ├── learning/{m0-learning-guide.md, m0-interview-prep.md}
│   └── milestones/m0-report.md
├── .env.example, .gitignore, .nvmrc, .python-version
├── compose.yaml, Makefile, README.md
```

## 4. Architecture and significant decisions

See [architecture overview](../architecture/overview.md) and ADRs:
[0001 monorepo](../decisions/0001-monorepo-layout.md) ·
[0002 uv/Ruff/mypy](../decisions/0002-uv-and-python-quality-tooling.md) ·
[0003 async SQLAlchemy](../decisions/0003-async-sqlalchemy-asyncpg.md) ·
[0004 pgvector via Alembic](../decisions/0004-pgvector-managed-by-alembic.md) ·
[0005 probes](../decisions/0005-health-probe-semantics.md) ·
[0006 correlation & errors](../decisions/0006-request-correlation-and-error-envelope.md) ·
[0007 frontend API access](../decisions/0007-frontend-server-side-api-access.md) ·
[0008 integration test policy](../decisions/0008-integration-test-database-policy.md).

## 5. Files created or modified

* **Modified:** `.python-version` (`3.12` → `3.12.16`).
* **Created:** every other file listed in §3. `.nvmrc` unchanged.
* **Local, git-ignored:** `.env` (copied from `.env.example`), `apps/api/.venv`,
  `apps/web/node_modules`, `apps/web/.next`, caches.
* **Outside the repo:** none. Docker objects created: container `nexus-postgres-1`,
  network `nexus_default`, volume `nexus_pgdata`.

## 6. Dependency versions (resolved, locked)

**Runtimes:** Python 3.12.16 · Node.js 24.22.2 · npm 11.19.1 · uv 0.13.9 ·
Docker 26.1.3 · Compose 2.27.1 · PostgreSQL 17 + pgvector 0.8.7.

**Backend runtime:** fastapi 0.165.0 · starlette 1.10.0 · uvicorn 0.54.0 (uvloop 0.23.0) ·
pydantic 2.14.4 · pydantic-settings 2.16.3 · sqlalchemy 2.0.58 · asyncpg 0.32.0 ·
alembic 1.20.1 · structlog 26.1.0 (47 packages in `uv.lock`).

**Backend dev:** ruff 0.17.6 · mypy 2.4.1 · pytest 9.2.0 · pytest-asyncio 1.5.0 ·
httpx 0.28.1 · asgi-lifespan 2.1.0.

**Frontend:** next 16.4.6 · react / react-dom 19.3.0 · tailwindcss 4.3.3 ·
@tailwindcss/turbopack 4.3.3 · typescript 5.9.3 · eslint 9.39.5 · eslint-config-next 16.4.6 ·
@types/node 24.19.2 · geist 1.9.2 · server-only 0.0.1.

**CI actions:** actions/checkout@v6 · actions/setup-python@v6 · actions/setup-node@v6 (all
`node24`) · astral-sh/setup-uv v10.4.0 pinned to commit `1db35dd` (was `@v6`, see §7b).

## 7. Verification results (executed locally on 2026-11-24)

| Check | Result |
|---|---|
| `uv lock --check`, `uv sync --locked` | pass |
| `npm ci` from scratch (after deleting `node_modules`, `.next`) | pass, 0 vulnerabilities |
| `ruff check` / `ruff format --check` | pass (29 files formatted) |
| `mypy` (strict) | pass, 30 source files |
| ESLint `--max-warnings=0` | pass |
| `next typegen && tsc --noEmit` | pass |
| `pytest -m "not integration"` | **44 passed** |
| `pytest -m integration` (DB up) | **13 passed** |
| `pytest` (all) | **57 passed** |
| `pytest -m integration` (DB stopped) | **13 errors, 0 skipped, exit code 1**: fails as required |
| `pytest -m integration` with `APP_ENV=production` | refused before any DDL |
| `npm audit` (all / `--omit=dev`) | 0 vulnerabilities (19 prod, 378 dev packages) |
| `uv audit` (all / `--no-dev`) | no known vulnerabilities (46 / 28 packages) |
| `next build` with backend stopped | pass; `/` static, `/status` partial prerender |
| Client bundle scan for `API_BASE_URL` / API URL | 0 matches in `.next/static` |
| `make check` (CI equivalent) | pass |

Runtime verification (live processes):

| Scenario | Observed |
|---|---|
| DB up | `/live` 200; `/ready` 200 `{"status":"ready", … latency_ms}` |
| Valid `X-Request-ID: demo-trace-1234` | echoed in response |
| Invalid `X-Request-ID: bad id <script>` | replaced with generated UUID4 hex |
| 404 / 405 | envelope with `request_id`; 405 keeps `Allow: GET` |
| CORS preflight from `http://localhost:3000` | 200 + `access-control-allow-origin` |
| Web `/status` (all up) | API live, DB ready; new timestamp and correlation ID per request |
| Web → API correlation | `request_id=web-<uuid>` present in API logs |
| `docker compose stop postgres` | `/live` 200; `/ready` **503** `unavailable` in 2.6 ms; web shows "not ready" |
| `docker compose down` + `make db-up` (volume kept) | `alembic current` = `0001 (head)` without re-migrating; `vector 0.8.7` present |
| DB restarted, API not restarted | `/ready` back to 200 (`pool_pre_ping`) |
| API SIGTERM | `application_shutdown` logged after engine disposal; graceful exit |
| Web `/status` with API stopped | HTTP 200, "API is unreachable (ECONNREFUSED)" |
| Web `/status` with API hanging (`API_TIMEOUT_MS=500`) | rendered in 0.58 s with "API did not respond within 500 ms" |
| Other containers / host PostgreSQL | `avs-postgres`, `avs-redis`, `mongodb` and host `:5432` untouched |

**Not verified locally:** `actionlint` was not run because executing a downloaded binary
was declined in this environment. The workflow has since been validated by real execution
on GitHub (§7b).

## 7a. Engineering audit (2026-11-24)

Each property was probed empirically against the real database, then locked in with tests.

| Area | Result |
|---|---|
| Session release on success / exception / cancellation | ✅ connection returned; asyncpg cancels the server-side query on cancellation |
| Commit releases connection mid-request | ✅ (property needed for long LLM calls) |
| Streaming + `DbSessionDep` | ⚠️ by design an uncommitted session is held until the stream ends. Guidance in ADR 0003 |
| Request-ID validation (8–128 chars, `[A-Za-z0-9._-]`) | ✅ |
| Context isolation (concurrent) and cleanup | ✅ 30–50 concurrent requests, no bleed; no ID after request |
| Streaming through middleware; mid-stream exception | ✅ no buffering; no leak; connection aborted |
| Readiness timeout covers pool wait + connect + query | ✅ |
| Timed-out connects close sockets; pool never leaks | ✅ |
| Readiness with wrong password / unknown DB | ❌ **was 500**, now 503 (defect fixed, regression-tested) |
| Integration DDL safeguards | ⚠️ **added** production refusal and name checks (unit-tested) |
| Docs accuracy | ⚠️ two inaccuracies corrected (test count, ADR 0002 attribution) |

## 7b. Remote CI verification (2026-11-27)

GitHub Actions workflow **run #1** for commit `ac80017` (push to `main`, remote
`origin` on GitHub) **passed**:

| Job | Result | Duration |
|---|---|---|
| Backend (lint · types · unit · integration) | ✅ pass | 39 s |
| Frontend (lint · types · build) | ✅ pass | 34 s |
| Workflow total (jobs run in parallel) | ✅ pass | 42 s |

This confirms on a clean `ubuntu-24.04` runner: locked installs (`uv sync --locked`,
`npm ci`), Ruff, mypy, ESLint, `tsc`, unit tests, `alembic upgrade head` and the real-DB
integration tests against the `pgvector/pgvector:0.8.7-pg17-bookworm` service container,
and the frontend production build with no backend.

**Follow-up: Node.js 20 deprecation warning.** The run reported that
`astral-sh/setup-uv@v6` uses the deprecated Node.js 20 runtime (`runs.using: "node20"`
in its `action.yml`; GitHub moved runners to Node 24 by default on 2026-06-16 and
scheduled Node 20's removal for 2026-09-23). Verified from the upstream repository:

* `v7.0.0` (2025-10-07) was the first `node24` release. Its only breaking changes were the
  runtime and removal of the `server-url` input, which this workflow does not use.
* Since `v8.0.0` the project **no longer publishes floating major/minor tags** (`@v8`,
  `@v10` do not exist) and recommends an immutable tag or commit SHA.
* Latest release `v10.4.0` = commit `1db35dd8eee11c5894872e0b383ae6c406cf738d`,
  `runs.using: "node24"`. The inputs used here (`version`, `enable-cache`,
  `cache-dependency-glob`) are unchanged. Defaults that changed but don't affect us:
  `prune-cache` now defaults to `false` (v9) and `enable-cache: auto` skips
  `pull_request_target`/`workflow_run`/`release` events (v10). We set `enable-cache: true`
  and trigger only on `push`/`pull_request`.
* The other three actions (`checkout`, `setup-python`, `setup-node` at `@v6`) already
  declare `node24`.

The workflow now pins `astral-sh/setup-uv@1db35dd… # v10.4.0`. This change is **not yet
verified remotely**. It will be confirmed by the next CI run once it is pushed.

## 8. Local startup

```bash
make setup && make db-up && make migrate
make api        # terminal 1 → http://127.0.0.1:8000/docs
make web        # terminal 2 → http://localhost:3000/status
```

## 9. Known limitations

* No authentication or authorisation; CORS `allow_credentials=False`.
* No Dockerfiles for API/web; no production deployment configuration (out of scope).
* Request logging logs every probe at INFO (may be noisy under an orchestrator).
* TS API types are hand-maintained mirrors of Pydantic models (no OpenAPI codegen yet).
* The local user `~/.npmrc` sets `package-lock=false`; the project `.npmrc` overrides it.
  Other per-user npm settings could still differ.
* Integration tests require Docker locally (by design; `make test-unit` otherwise).

## 10. Deviations from the specification (with rationale)

| Deviation | Rationale |
|---|---|
| Added `Makefile` | Approved in planning. |
| TypeScript 5.9.3 instead of latest 7.x | `typescript-eslint` supports TS < 6.1; chosen for compatibility. |
| ESLint 9 instead of 10 | `eslint-config-next` React plugins target ESLint 9. |
| SQLAlchemy 2.0.x instead of 2.1.x | Mature, widely deployed line; bounded `<2.1`. |
| GitHub Actions at `@v6` (one major behind latest) | "Stable over newest" instruction. |
| Added `apps/web/.npmrc` | User-level npm config disabled lockfiles; required for reproducibility. |
| Added `structlog`, `asgi-lifespan`, `geist`, `server-only` | Structured logging; real lifespan in tests; build-time-network-free fonts; enforced server-only boundary. |
| Kept Next's generated `cacheComponents` + `partialPrefetching` | Generator defaults in 16.4, documented as stable; the status page uses `connection()` + Suspense accordingly. |
| Health endpoints under `/api/v1/health/*` | Consistent with versioned routing. |
| Integration tests use a throwaway `<db>_test` database | Isolation from development data. |

## 11. Outstanding work (next milestones, not started)

Document ingestion (PyMuPDF) and first domain tables · Hugging Face embeddings + HNSW
index · retrieval API · LangGraph agent with Claude · SSE streaming · MCP/FastMCP tools ·
Langfuse tracing · RAGAS evaluation · authentication · containerised API/web.

## 12. Definition of done

- [x] Environment inspected; no conflicts with existing services
- [x] Monorepo structure with clear app/infra/docs boundaries
- [x] FastAPI app: settings, lifespan, logging, CORS, versioned routing, probes, errors
- [x] Request ID middleware: generate/validate, header, logs, error bodies
- [x] Readiness bounded by timeout; 503 when DB unavailable; liveness independent
- [x] Async engine disposed on shutdown (verified)
- [x] PostgreSQL + pgvector on `127.0.0.1:5434`, named volume, health check, pinned image
- [x] pgvector managed exclusively by Alembic; initial migration reversible
- [x] Session/transaction lifecycle implemented and tested against real DB
- [x] Next.js strict TS, Tailwind tokens, layout, typed API client, status page
- [x] Build succeeds without backend; status fetched per request; bounded timeout; graceful failure; no server env in client bundle
- [x] Makefile with documented commands
- [x] Lockfiles: `uv.lock`, `package-lock.json`
- [x] Ruff, mypy strict, pytest, ESLint, tsc, next build all passing locally
- [x] Integration tests fail (not skip) when DB unavailable; selectable/deselectable by marker
- [x] CI workflow defined for all required checks
- [x] CI executed on GitHub (run #1, commit `ac80017`, 2026-11-27: all jobs passed)
- [x] README, architecture overview, ADRs, report, learning guide, interview prep
- [x] Reviewed, audited and approved for commit
