# M0 Technical interview preparation

Fifteen questions grounded in the NEXUS AI M0 implementation. Each has a model
answer and the incorrect answers interviewers commonly hear. ★ marks questions aimed
at senior engineers moving into AI engineering.

---

### 1. Python type hints are "not enforced". Then how does FastAPI validate `limit: int`?

**Model answer:** Python keeps annotations at runtime (`__annotations__`, or via
`typing.get_type_hints`). FastAPI inspects handler signatures at startup and builds
Pydantic validators from them. Validation happens because the *framework* reads the
hints, not because the interpreter enforces them. Calling the same function directly
with `"abc"` would not raise. mypy checks statically; Pydantic validates at runtime at
the boundary.

**Common wrong answers:** "Python 3 enforces type hints." "They're erased like
TypeScript." (They aren't erased, and that's exactly what makes FastAPI possible.)

---

### 2. What's the difference between `async def` and `def` endpoints in FastAPI, and what happens if you call a blocking library inside `async def`?

**Model answer:** `async def` handlers run on the event loop. `def` handlers are run in
a threadpool so they can't block the loop. A blocking call (sync DB driver, `requests`,
`time.sleep`, CPU-heavy parsing) inside `async def` blocks the single loop thread, which
stalls **every** concurrent request on that worker, and latency spikes globally. Fixes: use
async libraries, `await asyncio.to_thread(fn)`, or make the handler `def`.

**Common wrong answers:** "`async` makes code run in parallel." "Async is always faster."

---

### 3. Why does the app use a factory (`create_app`) and a lifespan instead of module-level globals?

**Model answer:** importing a module executes it. Globals like
`engine = create_async_engine(...)` at import time read env, create pools, and make tests
share state. The factory lets tests build isolated apps with explicit `Settings`, and the
lifespan ties resource creation and cleanup (`await engine.dispose()`) to server startup/
shutdown, which uvicorn triggers via the ASGI lifespan protocol, including on SIGTERM.

**Common wrong answers:** "`@app.on_event('startup')`" (deprecated in favour of lifespan),
or "the garbage collector closes connections".

---

### 4. Liveness vs readiness: why does `/health/live` not check the database?

**Model answer:** liveness answers "should the orchestrator restart this process?";
readiness answers "should it receive traffic?". If liveness checked the DB, a database
outage would restart every healthy API pod in a loop (a restart storm) without fixing
anything. Readiness returns 503 so the load balancer stops routing traffic until the DB
recovers. Our readiness check is also bounded (`asyncio.timeout`), so a hung DB can't hang
the probe.

**Common wrong answers:** "One `/health` endpoint is enough." "Return 200 with
`status: down`." Orchestrators look at the status code.

---

### 5. Walk through the lifecycle of an `AsyncSession` in this API. When is data committed?

**Model answer:** `get_db_session` opens a session per request with `async with`. The first
statement checks out a pooled connection and begins a transaction implicitly. Data is
committed **only** when code calls `await session.commit()`. When the dependency exits,
the session closes, so any uncommitted transaction is rolled back and the connection returns to
the pool. If the endpoint raises, the exception propagates into the dependency, the
`async with` exits, and the work is rolled back. The integration tests verify all three paths.

**Common wrong answers:** "Each `execute` auto-commits." "The session is closed by
garbage collection." "One global session is fine."

---

### 6. ★ You'll later call an LLM that takes 20 seconds inside a request that also writes to the DB. What can go wrong with sessions?

**Model answer:** holding a transaction (and a pooled connection) open for the whole LLM
call. With `pool_size + max_overflow = 10`, ten concurrent slow requests exhaust the pool,
and other requests (even readiness) queue or time out. Long open transactions also hold locks
and block vacuum. Pattern: read what you need, commit/close, call the LLM *without* a
session, then open a short transaction to persist results (with idempotency to handle
retries).

**Common wrong answers:** "Increase the pool size" as the only fix (that moves the
bottleneck onto the DB's `max_connections`).

---

### 7. What does Alembic's `alembic_version` table do, and why is the pgvector extension created in a migration instead of a Docker init script?

**Model answer:** `alembic_version` stores the current revision, so `upgrade head` knows which
`upgrade()` functions still need to run. Docker `initdb` scripts run only when the data volume is
empty, exist only in that image, and don't run in CI service containers or managed Postgres.
Putting `CREATE EXTENSION IF NOT EXISTS vector` in revision `0001` makes it versioned,
reversible, and applied identically everywhere migrations run.

**Common wrong answers:** "Autogenerate handles extensions" (it doesn't). "Init scripts
run on every start" (only on first initialisation).

---

### 8. What are the risks of running `alembic upgrade head` automatically on application startup?

**Model answer:** with multiple replicas, several processes race to migrate. Startup
gets coupled to schema changes, and a long migration can fail health checks and trigger
restarts mid-migration. Also, the app's runtime DB role ideally shouldn't have DDL
privileges. Prefer a single migration step in the deploy pipeline (or a one-off job),
with backward-compatible expand/contract migrations so old and new code can run during rollout.

**Common wrong answers:** "Alembic locks, so it's safe" (it doesn't take a cross-process
lock by default).

---

### 9. Inside Docker Compose, the API container should reach Postgres. Which host and port, and why not `localhost:5434`?

**Model answer:** `postgres:5432`, using the service name via Compose's DNS on the project
network and the **container** port. `localhost` inside a container is the container
itself; `5434` is only the **host-published** port. Our `127.0.0.1:5434:5432` mapping
serves host processes (dev API, tests), and binding to 127.0.0.1 keeps the DB off the LAN.

**Common wrong answers:** "`localhost:5434`." "`host.docker.internal`" (works on some
platforms, but it's a workaround, not the design).

---

### 10. How do you make a database integration test suite trustworthy?

**Model answer:** use a real database of the same major version and extensions; isolate
it (we create and migrate a throwaway `<db>_test` per session); test migrations from an
empty DB, including downgrade; clean up what tests create; make the suite **fail** (not
skip) when the DB is unavailable so CI can't go falsely green; keep unit tests hermetic
(we clear env vars) and separately selectable with markers.

**Common wrong answers:** "Mock the session." "SQLite in memory" (different SQL dialect, no pgvector).
"Skip if the DB isn't there."

---

### 11. Explain pytest fixture scopes and a pitfall with async fixtures.

**Model answer:** scopes (`function`, `class`, `module`, `package`, `session`) set the
fixture's lifetime and caching. `yield` provides teardown. With pytest-asyncio, async
fixtures and tests must share a compatible event loop: a session-scoped async resource
(engine/pool) used from function-scoped loops raises "attached to a different loop".
We configure the session loop scope explicitly. Separately, sync tests are needed where code
calls `asyncio.run()` (Alembic's env), because that fails inside a running loop.

**Common wrong answers:** "Fixtures run in definition order." "Session scope means once
per file."

---

### 12. Why a pure ASGI middleware for request IDs instead of `@app.middleware("http")`?

**Model answer:** `@app.middleware("http")` uses `BaseHTTPMiddleware`, which wraps the
response in an extra task and stream. That has historically interfered with streaming
responses (SSE, which we need later), background tasks, and context-variable propagation.
A pure ASGI middleware wraps `send` to add headers without buffering, sees every
request (including 404/405), and can serve as the last-resort error boundary. We also
validate incoming IDs (charset and length) to prevent log injection.

**Common wrong answers:** "They're equivalent." "Just generate a new ID per log line."

---

### 13. What must an API never put in an error response, and how did this project enforce it?

**Model answer:** stack traces, exception messages, SQL, file paths, hostnames,
credentials, and echoed user input (which may itself be a secret). Enforcement: a single
error envelope; unhandled exceptions become a generic `internal_error` with the request ID
(logged with traceback server-side); validation details exclude Pydantic's `input`; JSON logs
render tracebacks **without locals**. Tests assert that a secret in an exception message
doesn't appear in the response but does appear in logs.

**Common wrong answers:** "Return `str(exc)`; it helps debugging." "`debug=True` is fine
in staging."

---

### 14. ★ How does Pydantic Settings differ from reading `os.environ`, and what's special about `SecretStr`?

**Model answer:** `Settings` declares every configuration value with a type, default,
and constraints, validates them all at startup (fail fast with a clear error), coerces strings
from env into typed values, and supports cross-field rules (JSON logs required in
production). `SecretStr` masks the value in `repr`/`str`, so logging the settings object or
an exception containing it doesn't leak the secret. You must call `get_secret_value()` explicitly.
For AI systems this extends to API keys (Anthropic, Langfuse): validated at boot, never logged.

**Common wrong answers:** "`os.getenv` with defaults is the same." "Masking isn't
needed if logs are private."

---

### 15. ★ The Next.js status page must show live backend health, build without a backend, and never expose the internal API URL. How?

**Model answer:** render it as a Server Component. With Cache Components the page
prerenders a static shell at build time; the dynamic panel is wrapped in `<Suspense>` and
calls `await connection()`, so it renders at request time only. It fetches with
`cache: "no-store"` and `AbortSignal.timeout`. Configuration uses non-`NEXT_PUBLIC_`
variables read lazily inside the request, and modules import `server-only`, so client
imports fail the build. The client returns a discriminated `ApiResult` validated by runtime
type guards, so timeouts or outages render as UI states.

**Common wrong answers:** "`useEffect` + `fetch` in the browser" (exposes the URL and
needs CORS). "`NEXT_PUBLIC_API_URL`" (inlined into the bundle). "`getStaticProps`" (wrong
router, and build-time data).
