# ADR 0006: Request correlation and a single error envelope

* Status: Accepted (M0, 2026-11-24)

## Decision
* A **pure ASGI** `RequestIdMiddleware`:
  * accepts incoming `X-Request-ID` only if it matches `^[A-Za-z0-9._-]{8,128}$`
    (prevents log injection/unbounded values), otherwise generates a UUID4 hex;
  * binds it to structlog context vars (every log line in the request carries it),
    `request.state.request_id`, and the `X-Request-ID` response header;
  * logs one `request_completed` event (method, path, status, duration);
  * converts unhandled exceptions into a generic 500 envelope and logs the traceback.
* All errors use `{"error": {"code", "message", "request_id", "details"}}`.
* JSON logs render tracebacks **without local variables** (`show_locals=False`).
* `CORSMiddleware` wraps the request-ID middleware so error responses keep CORS headers.

## Consequences
* + Users can quote a request ID; operators find all related log lines.
* + Frontend → API correlation: the web server sends `web-<uuid>` IDs that appear in API logs.
* + No stack traces, exception messages or validation inputs leak to clients (tested).
* − A pure ASGI middleware is more verbose than `@app.middleware("http")`, but doesn't
  buffer streaming responses (needed for SSE) or break context propagation.
* Rejected: `BaseHTTPMiddleware` (streaming/contextvar caveats); `asgi-correlation-id`
  package (small enough to own; fewer dependencies).
