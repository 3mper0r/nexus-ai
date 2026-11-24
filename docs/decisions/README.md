# Architecture decision records

Format: context → decision → consequences (incl. trade-offs and alternatives rejected).
Status values: Proposed · Accepted · Superseded.

| # | Title | Status |
|---|---|---|
| [0001](0001-monorepo-layout.md) | Monorepo with independent app toolchains | Accepted |
| [0002](0002-uv-and-python-quality-tooling.md) | uv, Ruff and strict mypy for Python | Accepted |
| [0003](0003-async-sqlalchemy-asyncpg.md) | Async SQLAlchemy 2.0 + asyncpg, explicit session lifecycle | Accepted |
| [0004](0004-pgvector-managed-by-alembic.md) | pgvector on PostgreSQL 17, extension managed by Alembic | Accepted |
| [0005](0005-health-probe-semantics.md) | Separate liveness and readiness probes | Accepted |
| [0006](0006-request-correlation-and-error-envelope.md) | Request correlation and a single error envelope | Accepted |
| [0007](0007-frontend-server-side-api-access.md) | Server-side API access from Next.js with Cache Components | Accepted |
| [0008](0008-integration-test-database-policy.md) | Integration tests: real database, throwaway schema, fail-not-skip | Accepted |
