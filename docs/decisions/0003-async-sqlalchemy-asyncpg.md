# ADR 0003: Async SQLAlchemy 2.0 + asyncpg, explicit session lifecycle

* Status: Accepted (M0, 2026-11-24)

## Context
The API will serve concurrent, I/O-heavy workloads (LLM calls, SSE streams,
vector queries). Blocking DB drivers would stall the event loop.

## Decision
* SQLAlchemy 2.0 asyncio extension with the **asyncpg** driver.
* One `AsyncEngine` per process, created in the FastAPI lifespan and disposed on
  shutdown. Engine creation is lazy (no connection), so the API can start while the
  DB is down and report this via readiness.
* Pool: `pool_size=5`, `max_overflow=5`, `pool_pre_ping=True`, `pool_recycle=1800`,
  asyncpg connect timeout, `application_name=nexus-api`.
* One `AsyncSession` per request (`DbSessionDep`), `expire_on_commit=False`,
  `autoflush=False`. **Commits are explicit**; uncommitted work is rolled back on close,
  including when the request raises.
* The session dependency opens the session directly with `async with` (not via a nested
  async generator), so cleanup is deterministic on exceptions.

## Consequences
* + Non-blocking DB access; connection usage is observable in `pg_stat_activity`.
* + Explicit commits make transaction boundaries visible in code review.
* − Async ORM forbids implicit lazy loading; relationships must be loaded eagerly
  (`selectinload`) in later milestones.
* − asyncpg uses server-side prepared statements; PgBouncer in transaction mode would need
  `statement_cache_size=0`. Not relevant locally; noted for deployment.
* Verified in the M0 audit (each covered by an integration test): connections are released
  on success, on exceptions, on task cancellation (asyncpg also cancels the server-side
  query), and immediately on `commit()`, even while the request continues.
* **Guidance for streaming (SSE) endpoints:** with FastAPI 0.165, teardown of `yield`
  dependencies runs *after* a streaming response finishes, so an open transaction would
  hold its pooled connection for the whole stream. Commit (or never open a transaction)
  before returning a `StreamingResponse`, or open a short-lived session from the
  sessionmaker inside the stream instead of using `DbSessionDep`. The same applies to
  slow LLM calls inside ordinary requests: commit first, then call the model.
* Rejected: psycopg 3 async (viable; asyncpg chosen for performance and maturity with
  SQLAlchemy async), auto-commit-per-request dependency (hides transaction boundaries,
  and commit timing relative to the response is subtle with yield dependencies).
