# ADR 0004: pgvector on PostgreSQL 17, extension managed by Alembic

* Status: Accepted (M0, 2026-11-24)

## Context
Retrieval (later milestones) needs vector similarity search. Options: a separate vector
DB, or pgvector inside PostgreSQL.

## Decision
* Image `pgvector/pgvector:0.8.7-pg17-bookworm` (explicit pgvector + PG major + distro).
* Published on `127.0.0.1:5434` only; Compose project `nexus`; named volume `nexus_pgdata`.
* `CREATE EXTENSION IF NOT EXISTS vector` lives in Alembic revision `0001`, **not** in a
  Docker init script.

## Consequences
* + One datastore for relational data and embeddings: transactional consistency,
  joins/filters with vector search, one backup story.
* + The extension is versioned with the schema and applied identically in local, CI
  (service container) and any managed PostgreSQL that offers pgvector (where init
  scripts don't exist).
* + Migration reversibility is tested (downgrade removes, upgrade restores).
* − Creating extensions needs sufficient privileges; managed providers may require an
  allow-list. Documented for deployment.
* − At very large scale a dedicated vector store may outperform pgvector; HNSW indexes in
  pgvector 0.8 cover the expected portfolio scale.
* Rejected: Docker `/docker-entrypoint-initdb.d` script (runs only on an empty volume,
  invisible to migrations, absent in CI/production); separate vector DB (operational overhead).
