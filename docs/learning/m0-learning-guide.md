# M0 Learning Guide: Python & backend foundations for a TypeScript engineer

Every section follows the same shape: **what we built → why → how it works →
TypeScript/Node equivalent → key differences → common production mistakes →
trade-offs.** File references point at the real implementation.

---

## 1. Python project structure and modules

**What:** `apps/api/src/nexus_api/` is a *package* (a directory with `__init__.py`);
each `.py` file is a *module*. We use the **src layout**.

**Why:** with `src/`, `import nexus_api` only works once the project is installed into
the virtualenv (uv does this as an editable install). Tests therefore import the same
package production would, not whatever happens to be in the current directory.

**How:** `import nexus_api.core.config` → Python searches `sys.path` (venv
`site-packages`, which contains a pointer to `src/`), executes the module **once**, and
caches it in `sys.modules`. Module-level code runs at first import, which is why
`main.py` exposes a `create_app()` factory instead of building the app at import time.

| TypeScript | Python |
|---|---|
| `export function f()` / `import { f } from "./x"` | everything public by default; `from nexus_api.x import f` |
| `index.ts` barrel | `__init__.py` (we keep it nearly empty) |
| `"paths": {"@/*": ["./src/*"]}` | the installed package name *is* the absolute import root |
| ESM module cache | `sys.modules` |

**Differences:** no `export` keyword; a leading underscore (`_handle_validation_error`)
is the "private" convention. Relative imports exist (`from .x import y`) but absolute
imports are clearer. Circular imports fail at *runtime* (partially initialised module),
not at bundle time.

**Production mistakes:** side effects at import time (opening DB connections, reading
env) → slow, untestable, order-dependent startup; naming a module like a stdlib/library
module (`logging.py` at top level shadows `logging`; ours is safely namespaced as
`nexus_api.core.logging`); `sys.path` hacks instead of installing the package.

---

## 2. Virtual environments and uv

**What:** `apps/api/.venv` holds an isolated interpreter + packages. `uv sync` creates it
from `uv.lock`; `uv run <cmd>` runs inside it without "activating".

**Why:** Python installs packages into an interpreter, not a project. Without a venv,
two projects on the same machine share (and break) each other's dependencies. Note the
system `python3` here is **3.8**; everything goes through uv, which provides 3.12.16.

**How:** a venv is a directory with a `pyvenv.cfg`, a `bin/python` symlink to a base
interpreter, and its own `site-packages`. uv resolves the full dependency graph, writes
`uv.lock` (exact versions + hashes for all platforms), and hard-links packages from a
global cache, which makes it fast.

| npm | uv |
|---|---|
| `node_modules/` (per project, automatic) | `.venv/` (per project, explicit) |
| `npm install` / `npm ci` | `uv sync` / `uv sync --locked` |
| `npx tool` | `uv run tool` / `uvx tool` |
| `.nvmrc` + nvm | `.python-version` + uv-managed interpreters |

**Differences:** Node resolves modules by walking up directories; Python uses one flat
`site-packages` per interpreter, so **only one version of a package can exist per venv**.
No nested dependency trees, and conflicts must be resolved, not duplicated.

**Production mistakes:** `pip install` into the system Python; committing `.venv`;
CI using `uv sync` without `--locked` (silently re-resolves); mixing `pip` and `uv` in
the same venv.

---

## 3. `pyproject.toml` and dependency management

**What:** `apps/api/pyproject.toml` declares metadata, runtime `dependencies`, a `dev`
**dependency group** (PEP 735), the build backend, and tool configuration (Ruff, mypy, pytest).

**Why:** one standard file, like `package.json`, but also hosting tool config that in JS
lives in `.eslintrc`, `tsconfig.json`, `vitest.config.ts`.

**How:** ranges such as `"sqlalchemy[asyncio]>=2.0.40,<2.1"` are *constraints*; `uv.lock`
records the *solution*. `[asyncio]` is an **extra**: optional dependencies of that
package (here: `greenlet`). `[project.scripts]` creates the `nexus-api` executable,
like `"bin"` in package.json.

```toml
[dependency-groups]          # ≈ devDependencies
dev = ["ruff>=0.17,<0.18", "pytest>=8.4,<10", ...]
```

**Differences:** no `^`/`~` shorthand. We write explicit upper bounds for libraries
whose minor versions can break us (FastAPI 0.x, Ruff), mirroring "stay on the current
line". Ruff's rule set changes in minor releases, so an unbounded range would turn CI
red without a code change.

**Production mistakes:** no lockfile in an *application*; over-tight pins in a
*library* (breaks consumers); forgetting that extras must be requested
(`sqlalchemy` without `[asyncio]` fails at runtime on some platforms).

---

## 4. Python type hints and runtime behaviour

**What:** every function in the backend is annotated; mypy runs in `strict` mode.

**Key point:** like TypeScript, **annotations are not enforced at runtime** by
Python itself. Unlike TypeScript, they are *not erased*: they're available at runtime
(`__annotations__`) and libraries such as Pydantic and FastAPI **read them to build
validators and dependency graphs**.

```ts
function f(x: number): string        // erased by tsc
```
```python
def f(x: int) -> str: ...            # kept; f("oops") still runs unless something validates
```

| TypeScript | Python |
|---|---|
| `string \| null` | `str \| None` |
| `Record<string, T>` | `dict[str, T]` |
| `"a" \| "b"` | `Literal["a", "b"]` |
| `type X = ...` | `X = ...` alias / `type X = ...` (3.12) |
| `(v: unknown) => v is T` | `TypeGuard[T]` / `TypeIs[T]` |
| `readonly`, `as const` | `Final`, frozen dataclasses / models |
| structural typing | nominal by default; `Protocol` for structural |

`from __future__ import annotations` (used in every module) stores annotations as strings
and evaluates them lazily. It allows forward references and is cheaper at import time.

**Production mistakes:** trusting annotations as validation at API boundaries (use
Pydantic); `Any` leaking everywhere; `# type: ignore` without an error code. mypy's
`warn_unused_ignores` (part of strict) flagged five stale ignores during M0.

---

## 5. Pydantic and runtime validation

**What:** `Settings` (`core/config.py`) and all response models (`api/v1/health.py`,
`core/errors.py`) are Pydantic models.

**Why:** types tell mypy what *should* be true; Pydantic makes it *true at runtime*
by parsing untrusted input (env vars, JSON bodies, query strings).

**How:** Pydantic v2 compiles a validation schema in Rust (`pydantic-core`) from the
annotations. `Settings` adds sources: init kwargs > env vars > defaults.
Highlights from our config:

```python
postgres_password: SecretStr                         # required, masked in repr/logs
api_port: int = Field(default=8000, ge=1, le=65535)  # constrained
cors_origins: Annotated[list[str], NoDecode] = [...] # comma-separated env → list (custom validator)
model_config = SettingsConfigDict(frozen=True)       # immutable after startup
```

`@model_validator(mode="after")` enforces cross-field rules (JSON logs in production).

| TypeScript (zod) | Pydantic |
|---|---|
| `z.object({...})` + `z.infer<>` | the class *is* both schema and type |
| `schema.parse(x)` | `Model(**x)` / `Model.model_validate(x)` |
| `z.string().url()` | `HttpUrl`, `Field(...)` constraints |
| `.refine()` | `@field_validator`, `@model_validator` |

**Differences:** Pydantic **coerces** by default (`"9001"` → `9001`) in lax mode. That
is desirable for env vars, but use strict mode/types where coercion would hide bugs.

**Production mistakes:** logging settings objects containing plain-`str` secrets (use
`SecretStr`); reading `os.environ` ad hoc throughout the codebase; mutable global
settings modified in tests (ours are frozen and passed explicitly).

---

## 6. FastAPI routing and dependency injection

**What:** `APIRouter`s composed under `/api/v1`; handlers declare dependencies with
`Annotated[T, Depends(fn)]` aliases (`SettingsDep`, `EngineDep`, `DbSessionDep`).

**How:** at startup FastAPI inspects each handler signature. Path/query/body parameters
become validated inputs; `Depends(...)` parameters form a DAG resolved per request
(cached within a request). A dependency that `yield`s is a context manager: code after
`yield` runs on teardown, which is how `get_db_session` guarantees the session closes.

```python
async def readiness(response: Response, engine: EngineDep, settings: SettingsDep) -> ReadinessResponse:
```

| Express/Nest | FastAPI |
|---|---|
| `router.get("/x", handler)` | `@router.get("/x")` |
| `req.query.limit` + manual parsing | `limit: int` parameter, validated, 422 on failure |
| Nest providers / `@Inject()` | `Depends(fn)`: plain functions, no container |
| middleware for per-request resources | `yield` dependencies |
| return value + `res.status()` | return model; `response.status_code = 503` |

**Production mistakes:** creating engines/clients inside dependencies (a pool per
request!). Create them in the lifespan and expose them via dependencies. Wrapping a
`yield` dependency in another generator (`async for s in inner(): yield s`), which
we hit in M0: the inner generator isn't closed when the endpoint raises, which leaked a
connection until GC. The integration suite's `ResourceWarning` (warnings are errors)
caught it.

---

## 7. Python async/await and event loops

**What:** the whole request path is `async`: the ASGI server, middleware, handlers,
SQLAlchemy async engine, and asyncpg.

**How:** `async def` returns a coroutine; `await` yields control to the **event loop**
(asyncio, or uvloop under uvicorn) until the awaited I/O completes. One thread
interleaves thousands of requests, the same model as Node.

| Node | Python |
|---|---|
| implicit, always-running loop | explicit: `asyncio.run(main())`, or the server owns it |
| Promises start eagerly | coroutines are **lazy**: nothing runs until awaited/scheduled |
| `Promise.all` | `asyncio.gather` / `TaskGroup` |
| `AbortSignal.timeout(ms)` | `async with asyncio.timeout(s):` (used in `db/health.py`) |
| AsyncLocalStorage | `contextvars` (structlog binds `request_id` here) |

**Critical difference:** in Node almost every I/O API is async. In Python, the
standard library and most older libraries are **blocking**. Calling `time.sleep`,
`requests.get` or a sync DB driver in an `async def` freezes **every** request on that
worker. Ruff's `ASYNC` rules flag common cases.

**Production mistakes:** blocking calls in async handlers; forgetting `await` (a
coroutine that never runs, and Python warns `coroutine was never awaited`); unbounded
waits without timeouts; `asyncio.run()` inside an already-running loop. This is why our
Alembic-migrating tests are **sync** functions: Alembic's env calls `asyncio.run`.

**Trade-off:** CPU-bound work (PDF parsing, local embeddings later) must go to a thread/
process pool (`asyncio.to_thread`, `run_in_executor`) or a worker, never inline.

---

## 8. ASGI and Uvicorn

**What:** FastAPI is an **ASGI application**: an async callable
`app(scope, receive, send)`. **Uvicorn** is the ASGI server that speaks HTTP and calls it.

**How:** see `core/request_id.py`, a raw ASGI middleware. It wraps `send` to inject a
header into the `http.response.start` message. The **lifespan protocol** sends
`startup`/`shutdown` events, which FastAPI maps to our `lifespan` context manager.

| Node | Python |
|---|---|
| `http.createServer((req, res) => …)` | `async def app(scope, receive, send)` |
| Express middleware `(req, res, next)` | ASGI app wrapping another ASGI app |
| `server.listen()` | `uvicorn module:factory --factory` |
| graceful `SIGTERM` handling | uvicorn drains, then runs lifespan shutdown (verified: `application_shutdown` logged) |

**Why a factory (`--factory`):** importing the module has no side effects; tests build
isolated apps with explicit `Settings`.

**Production mistakes:** `--reload` in production; one uvicorn worker on a multi-core
machine (use several workers/processes or replicas); trusting `X-Forwarded-*` headers
without `--proxy-headers`/`--forwarded-allow-ips` configured correctly; using
`BaseHTTPMiddleware` with streaming responses.

---

## 9. SQLAlchemy sessions and transactions

**What:** one `AsyncEngine` (connection pool) per process; one `AsyncSession` per request.

**How:** the **engine** owns the pool. A **session** is a unit-of-work: it checks out a
connection on first use, begins a transaction implicitly, tracks ORM objects, and
`commit()` flushes + commits. **Closing a session with an open transaction rolls it back.**
Our integration test proves all three cases: explicit commit persists; no commit →
rolled back; exception → rolled back (and returns a 500 envelope).

| Prisma/TypeORM | SQLAlchemy |
|---|---|
| `new PrismaClient()` singleton | `create_async_engine()` once, in lifespan |
| `prisma.$transaction(async tx => …)` | `async with session.begin():` or explicit `commit()` |
| auto-commit per query | transaction begins implicitly; **nothing persists until commit** |
| `$disconnect()` | `await engine.dispose()` (verified via `pg_stat_activity`) |

Settings explained: `expire_on_commit=False` (objects stay readable after commit; async
can't lazily refresh), `pool_pre_ping=True` (detects dead connections after DB restarts;
that is why readiness recovered without restarting the API), `pool_recycle=1800`.

**Streaming caveat (verified in the M0 audit):** a `yield` dependency's teardown runs
after a `StreamingResponse` finishes, so an *uncommitted* session holds its connection
for the whole stream. Committing first releases it immediately.

**Production mistakes:** sharing one session across concurrent tasks (sessions are not
concurrency-safe); long transactions held open across slow calls (e.g. an LLM request);
N+1 lazy loading (impossible in async; use `selectinload`); pool exhaustion from leaked
sessions.

---

## 10. Alembic migrations

**What:** `apps/api/migrations/` with an **async** `env.py`; revision `0001` enables pgvector.

**How:** each revision has `revision`/`down_revision` forming a linked list (a DAG with
branches). `alembic upgrade head` runs `upgrade()` functions in order and records the
current revision in the `alembic_version` table. `--autogenerate` diffs
`Base.metadata` (our models) against the live DB to draft a migration.

Our choices:
* `env.py` builds the URL from **the same `Settings`** as the app, so there is no second source of truth.
* Readable revision IDs (`0001`) and `file_template = %%(rev)s_%%(slug)s`.
* A constraint **naming convention** on `Base.metadata` so autogenerated constraint names
  are deterministic across environments.
* A Ruff post-write hook formats generated migrations.

| Prisma Migrate / Knex | Alembic |
|---|---|
| `prisma migrate dev` | `alembic revision --autogenerate` + review + `upgrade head` |
| `_prisma_migrations` table | `alembic_version` table |
| down migrations (Knex) | `downgrade()`, tested for `0001` |

**Production mistakes:** trusting autogenerate blindly (it misses renames, some type
changes, server defaults, and extensions); editing a migration that has already been
applied elsewhere; destructive migrations without backfill or expand/contract steps;
running migrations from every app replica on boot (race). Run them once, as a
deploy step.

---

## 11. PostgreSQL and pgvector

**What:** PostgreSQL 17 with the `vector` extension 0.8.7.

**How:** pgvector adds a `vector(n)` column type, distance operators (`<->` L2,
`<=>` cosine distance, `<#>` negative inner product) and ANN indexes (HNSW, IVFFlat).
Our integration test runs `'[1,2,3]'::vector <-> '[1,2,4]'::vector = 1.0`.

**Why in Postgres:** embeddings live next to the documents and metadata they describe,
giving one transaction, one backup, and SQL filters + similarity in one query (ADR 0004).

**TS analogy:** like choosing Postgres + `pg` over adding Pinecone: fewer moving parts.

**Production mistakes (relevant from M1):** no ANN index (sequential scans); mixing
embedding models/dimensions in one column; picking a distance operator that doesn't match
how the model was trained (cosine for most sentence-transformers); forgetting
`ef_search`/`lists` tuning; creating the extension outside migrations.

---

## 12. Docker networking and health checks

**What:** `compose.yaml` runs one service with project name `nexus`, publishing
`127.0.0.1:5434 → 5432` and a `pg_isready` health check.

**How:**
* **Port publishing** maps a host socket to a container port. Binding to `127.0.0.1`
  keeps it off the LAN (`0.0.0.0` would expose it; Docker's iptables rules can
  bypass host firewalls like `ufw`).
* Compose creates a project network `nexus_default`; containers reach each other by
  **service name** (`postgres:5432`). The host reaches them via the published port
  (`127.0.0.1:5434`). The **container port stays 5432**; only the host side changes,
  which is why it doesn't collide with the host PostgreSQL on 5432.
* **Health checks** run inside the container; `docker compose up --wait` blocks until
  healthy (`make db-up`).
* **Named volume** `nexus_pgdata` survives `docker compose down` (verified: migration
  state persisted after the container was removed and recreated); `down -v` deletes it.
* `$$POSTGRES_USER` escapes Compose interpolation so the variable expands in the container.

**Production mistakes:** `localhost` inside a container (that's the container itself);
"container running" ≠ "database ready" (use health checks); unpinned `latest` images;
publishing databases on `0.0.0.0`.

---

## 13. pytest fixtures and test isolation

**What:** `tests/conftest.py` (shared), `tests/unit/conftest.py` (hermetic env),
`tests/integration/conftest.py` (real DB lifecycle).

**How:** fixtures are functions injected **by parameter name**. Scope (`function`,
`session`) controls lifetime; `yield` fixtures have teardown. `conftest.py` files make
fixtures available to their directory tree.

| Vitest/Jest | pytest |
|---|---|
| `beforeEach`/`afterEach` | function-scoped `yield` fixture |
| `beforeAll` | `scope="session"` fixture (our test DB) |
| `vi.stubEnv` | `monkeypatch.setenv/delenv` (auto-restored) |
| `describe.skip` / tags | markers: `@pytest.mark.integration`, `-m "not integration"` |
| `test.each` | `@pytest.mark.parametrize` |

Our isolation rules:
* Unit tests: an autouse fixture **deletes every Settings env var**. Before it existed,
  `make test` (which loads `.env`) changed the outcome of "default value" tests. That
  real bug was found during M0.
* Integration tests: a throwaway `nexus_test` database, created, migrated and dropped per session.
* `filterwarnings = ["error"]`: resource leaks and deprecations fail tests.
* No mocks on the DB path: "DB down" is a real closed port; "DB hangs" is a real TCP
  server that never answers.

**Production mistakes:** tests depending on developer env; order-dependent tests sharing
state; mocking the thing you're trying to verify; `skip` when infrastructure is missing
(false green). We **fail** instead.

---

## 14. Ruff and mypy

**Ruff** ≈ ESLint + Prettier, in one Rust binary. `ruff format` is the formatter
(Black-compatible); `ruff check` is the linter with selectable rule families. Ours include
`S` (security), `ASYNC` (blocking calls in async code), `B` (bugbear), `UP` (modern syntax).
During M0 it flagged an f-string SQL query in a test (`S608`); we switched to a bound
parameter.

**mypy** ≈ `tsc --noEmit --strict`. `strict = true` enables, among others,
`disallow_untyped_defs`, `disallow_any_generics`, `warn_return_any`, and
`warn_unused_ignores`. The **Pydantic plugin** teaches mypy about model constructors.

| TS | Python |
|---|---|
| `tsc --noEmit` | `mypy` |
| `strict: true` | `strict = true` |
| `@ts-expect-error` | `# type: ignore[code]` (always with an error code) |
| `skipLibCheck` / `declare module` | `[[tool.mypy.overrides]] ignore_missing_imports` |
| ESLint + Prettier | `ruff check` + `ruff format` |

**Differences:** TS checks while it compiles; Python has no compile step, so mypy is
*optional* tooling. Nothing stops code with type errors from running, which is why CI enforces it.

**Production mistakes:** running mypy non-strict "for now" (it never gets stricter);
blanket `ignore_missing_imports = true` globally (hides real import mistakes); formatting
debates instead of a formatter in CI.

---

## Bugs caught during M0 (and what they teach)

| Bug | Caught by | Lesson |
|---|---|---|
| Tracebacks in JSON logs included **local variables** | reading test output | log serialisers can leak secrets; set `show_locals=False` |
| `HTTPStatus.UNPROCESSABLE_CONTENT` (3.13+) used on 3.12 | unit test → 500 | pin and test against the real runtime |
| Session leaked on exception via nested async generator | `ResourceWarning` as error | own `async with` directly in `yield` deps |
| Unit tests read developer `.env` | `make test` vs `make test-unit` difference | hermetic tests clear env explicitly |
| `configure_logging` removed pytest's log handlers | test harness errors | libraries shouldn't clobber global logging state |
| f-string SQL in tests | Ruff `S608` | parameterise even "safe" queries |
| Wrong DB password / unknown DB made readiness return **500** instead of 503 | M0 audit probing every failure mode | asyncpg errors raised while *connecting* are not wrapped by SQLAlchemy; catch the driver's base exceptions at health boundaries |
| No explicit guard before the test suite's `DROP DATABASE` | M0 audit | destructive test setup needs explicit, unit-tested preconditions |
