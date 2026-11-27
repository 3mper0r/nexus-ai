# ADR 0009: Document and chunk data model, content-hash deduplication

* Status: Accepted (M1, 2026-11-27)

## Context
M1 introduces the first domain tables. They must support asynchronous ingestion with
visible progress, safe retries, accurate page citations, and later re-embedding with a
different model, without speculative columns.

## Decision
* `documents`: `id`, display `filename`, `sha256` (**UNIQUE**), `size_bytes`, `page_count`,
  `status` (`pending → processing → ready | failed`), `error_code`, `attempts`,
  `embedding_model`, `chunk_count`, `created_at`, `updated_at`.
* `chunks`: `document_id` (FK, `ON DELETE CASCADE`), `ordinal`, `page_start`, `page_end`,
  `text`, `token_count`, `embedding vector(384)`. `UNIQUE (document_id, ordinal)`.
* `status` is **VARCHAR + CHECK** mapped to a Python `StrEnum`, not a PostgreSQL ENUM.
* **Deduplication by SHA-256 of the bytes.** Re-uploading a `pending`/`processing` document,
  or a `ready` one embedded by the active model, returns the existing record (200).
  Re-uploading a `failed` one, or a `ready` one embedded by a **different** model (invisible
  to search), resets it to `pending` with `attempts + 1` and deletes its old chunks in the
  same transaction (202). The model rule was added in the M1 audit. Concurrent identical uploads are resolved by
  `INSERT … ON CONFLICT (sha256) DO NOTHING`, and conditional `UPDATE … WHERE status = 'failed'`
  makes concurrent retries race-free.
* `embedding_model` (`name@revision`) is recorded per document; search only compares
  chunks of the active model.
* **Original files are not stored.** Citations need page numbers only.
* No separate index on `chunks.document_id`: the unique `(document_id, ordinal)` index
  serves filters, joins and the cascade (found while profiling plans; it was dropped
  before the migration was finalised).

## Consequences
* + Idempotent uploads; predictable retry rule ("upload the same file again").
* + A model change can be detected per document and re-embedded later without mixing
  vector spaces.
* − Without stored originals, an interrupted or failed document cannot be re-processed
  server-side; the user re-uploads (the hash makes that a retry of the same record).
* − `vector(384)` fixes the dimension: switching to a model with another dimension needs
  a migration and full re-embedding.
* Rejected: PostgreSQL ENUM (`ALTER TYPE … ADD VALUE` restrictions, no clean removal);
  dedupe by filename (unrelated files share names; identical files get renamed);
  storing PDFs as `bytea` (bloats the table and backups for a feature M1 does not need).
