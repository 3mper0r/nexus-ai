# ADR 0005: Separate liveness and readiness probes

* Status: Accepted (M0, 2026-11-24)

## Decision
* `GET /api/v1/health/live`: no dependency checks, always 200 while the process serves.
* `GET /api/v1/health/ready`: `SELECT 1` bounded by `DB_HEALTH_TIMEOUT_SECONDS`
  (`asyncio.timeout`); 200 `ready` or **503** `not_ready` with `error: timeout | unavailable`.
* Readiness responses are a *health report*, not an error envelope; the request ID is
  carried in the `X-Request-ID` header. Failure reasons are coarse (no hostnames,
  driver messages or credentials); details go to logs.
* Every database failure maps to 503, including connect-time rejections (wrong password,
  unknown database, too many connections). asyncpg raises those unwrapped by SQLAlchemy, so
  `db/health.py` lists asyncpg's exception bases explicitly (M0 audit defect, regression-tested).
* The timeout covers pool acquisition, connection establishment and query execution;
  repeated failures and timeouts never leave pool connections checked out (tested).
* Probes are under the versioned prefix for consistency.

## Consequences
* + A DB outage removes instances from load balancing (readiness) without causing
  restart storms (liveness).
* + Bounded check: a hung database cannot hang the probe.
* − Each readiness call costs a pooled connection checkout; acceptable at probe frequency.
* − Probe requests are logged at INFO like all requests; may be noisy under an orchestrator.
* Rejected: a single `/health` that checks the DB (conflates "restart me" with
  "don't route to me").
