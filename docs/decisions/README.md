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
| [0009](0009-document-and-chunk-data-model.md) | Document and chunk data model, content-hash deduplication | Accepted |
| [0010](0010-pdf-extraction-with-pypdf.md) | PDF extraction with pypdf instead of PyMuPDF | Accepted |
| [0011](0011-local-multilingual-embeddings.md) | Local multilingual embeddings (multilingual-e5-small) on CPU | Accepted |
| [0012](0012-in-process-background-ingestion.md) | Bounded in-process background ingestion with explicit recovery | Accepted |
| [0013](0013-page-bounded-token-aware-chunking.md) | Page-bounded, token-aware chunking | Accepted |
| [0014](0014-vector-search-hnsw-cosine.md) | Vector search with HNSW, cosine distance and iterative scans | Accepted |
| [0015](0015-upload-limits-and-validation.md) | Upload path, size limits and validation | Accepted |
