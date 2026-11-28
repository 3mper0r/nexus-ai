# ADR 0019: SSE streaming, cancellation and transaction boundaries

* Status: Accepted (M2, 2026-11-27)

## Decision
* `POST /api/v1/conversations/{id}/messages` returns `text/event-stream`
  (`sse-starlette`, 15 s keep-alive pings) with events `meta`, `delta`, `citation`,
  `done`, `error`. **Admission happens before the response starts**: missing key (503),
  chat starting (503), concurrency (429 `llm_busy`), conversation busy (409), budget
  (429 `llm_budget_exceeded`), validation (422) are JSON errors and store nothing.
* Two phases in `ChatService`: `prepare` (short transactions: lookup, history, retrieval,
  reservation) and `stream` (**no database connection**, verified with `pg_stat_activity`
  while a generation is held), then one short transaction to record the outcome.
* **Cancellation:** a client disconnect cancels the task; `contextlib.aclosing` around the
  nested generators also handles the `aclose()` path, so the upstream HTTP response is
  closed (no further tokens) and the partial text is stored as `cancelled`. The final write
  is shielded from cancellation. Both paths are tested, and mutation-checked.
* **Response guard (audit, 2026-11-28):** the response, not the endpoint, runs the
  generator. If the client is gone before the first byte, the generator never starts and
  so runs no `finally`. `_AnswerEventSourceResponse` therefore calls `ChatService.abandon()`
  when it ends: a never-started answer is stored `cancelled` with cost 0 and its slot is
  freed. The route also wraps `chat.stream` in `aclosing`, because sse-starlette
  `aclose()`s the route's iterator when a send fails. Both are tested and mutation-checked.
* **anyio cancellation is level-triggered:** under sse-starlette's cancel scope,
  cancellation is re-delivered at every `await`. Code after the shielded write never runs,
  so logging happens before it; the write completes in its own task.
* **Recovery:** at startup, `streaming` rows from a crashed process become `interrupted`
  (their reservation keeps counting); the conversation can continue.
* **Next.js proxy** (`app/api/conversations/[id]/messages/route.ts`): the browser never
  sees the API URL; the upstream request uses the browser request's abort signal; it
  requires a same-origin `Origin` header and a JSON body (Route Handlers lack Server
  Actions' CSRF check) and passes admission errors through unchanged.

## Consequences
* − Partial text of a crash (not a cancel) is lost; only the reservation remains.
