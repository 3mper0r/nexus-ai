# ADR 0014: Vector search with HNSW, cosine distance and iterative scans

* Status: Accepted (M1, 2026-11-27)

## Decision
* Index `ix_chunks_embedding_hnsw`: `USING hnsw (embedding vector_cosine_ops)
  WITH (m = 16, ef_construction = 64)` (pgvector defaults, suitable below ~1M rows). The
  query uses the matching `<=>` operator; `score = 1 − cosine distance`.
* Per-query settings via `set_config(…, is_local => true)` inside the search transaction,
  so they never leak into pooled connections: `hnsw.ef_search = SEARCH_EF_SEARCH (64)` and
  `hnsw.iterative_scan = relaxed_order`; results are re-sorted in Python because relaxed
  order may be slightly out of order.
* Search joins `documents` and filters `embedding_model = <active model>`; optional
  `document_ids` filter; `top_k ≤ SEARCH_TOP_K_MAX (20)`.
* The query embedding runs in a worker thread.

## Why iterative scans
When most rows belong to another model (e.g. after a model change), the planner scans the
HNSW index and filters afterwards. With `iterative_scan = off` and `ef_search = 10`, a
query for 10 results returned **5**; with `relaxed_order` it returned 10. An integration test
reproduces this and fails if the setting is removed. A selective `document_ids` filter is
planned on the `(document_id, ordinal)` btree with an exact sort instead, which is correct
and fast at this scale.

## Consequences
* + Sub-30 ms p95 search at current scale; exact filtering semantics for document scopes.
* − Approximate: recall depends on `ef_search`; not measured against exact search at scale
  yet (M4 evaluation).
* − No hybrid (BM25 + vector) search or re-ranking; exact-term queries (IDs, codes) can rank
  poorly.
* Rejected: IVFFlat (needs data before building lists, degrades as data drifts), exact
  search only (fine now, linear cost later), inner-product opclass (identical ranking for
  normalised vectors; cosine reads more clearly).
