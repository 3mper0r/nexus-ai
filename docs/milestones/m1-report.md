# M1 Implementation Report: Document Ingestion & Semantic Retrieval

* Date: 2026-11-27
* Status: **Accepted (2026-11-27)** after implementation and engineering audit. Committed
  locally; push pending (manual).
* Plan: [m1-plan.md](m1-plan.md) (approved 2026-11-27 with D2 → pypdf, D3 → multilingual-e5-small)
* Final local results after the audit: **167 backend tests passing** (117 unit, 38 integration,
  12 real-model); Ruff, strict mypy (57 files), ESLint, `tsc` and the production build pass.

## 1. Executive summary

M1 turns the M0 foundation into a working retrieval system without any LLM calls:

* PDFs are uploaded through `/documents` (Server Action → API), validated synchronously, and
  ingested in the background: per-page extraction with **pypdf**, page-bounded chunking in
  model tokens, embeddings from **multilingual-e5-small** (local, CPU, pinned revision),
  stored in PostgreSQL with an **HNSW cosine index**.
* Ingestion is a bounded in-process queue with states persisted in PostgreSQL, SHA-256
  deduplication, a predictable retry rule, and explicit handling of graceful shutdown and
  crashes. No Redis.
* `/search` returns ranked passages with **single-page citations**, in English, German and
  across languages.
* Verified on real-world PDFs, in a real browser, and under SIGTERM/SIGKILL.

## 2. Approved decisions and how they were implemented

| Decision | Implementation | ADR |
|---|---|---|
| D1 scope | Upload, extraction, chunking, embeddings, vector storage, semantic search, page citations, `/documents`, `/search`. No Claude/answers | – |
| D2 pypdf | pypdf 7.2.0 (BSD-3-Clause); quality measured on 6 real PDFs; limitations documented; no OCR | [0010](../decisions/0010-pdf-extraction-with-pypdf.md) |
| D3 multilingual-e5-small | Revision `614241f6…`, `query:`/`passage:` prefixes, normalised 384-d vectors, 400-token chunks ≤ 512 with prefix, MIT licence, resource figures measured | [0011](../decisions/0011-local-multilingual-embeddings.md) |
| D4 background processing | 202 + polling, bounded queue (8) + concurrency (1), states in PostgreSQL, startup recovery, graceful interruption, documented limits | [0012](../decisions/0012-in-process-background-ingestion.md) |

Further ADRs: [0009](../decisions/0009-document-and-chunk-data-model.md) data model & dedupe ·
[0013](../decisions/0013-page-bounded-token-aware-chunking.md) chunking ·
[0014](../decisions/0014-vector-search-hnsw-cosine.md) vector search ·
[0015](../decisions/0015-upload-limits-and-validation.md) upload limits & validation.

## 3. Implemented functionality

| Requirement | Implementation | Verified by |
|---|---|---|
| Upload size limit | `BodySizeLimitMiddleware` (declared and streamed) + exact file check | unit: 3 tests (413 by header, by stream, by file) |
| File validation | `%PDF-` magic in first KiB, structure, encryption (empty-password allowed), page cap | unit: 11 tests on generated PDFs; live: renamed text file 415, password PDF 422 |
| Safe filenames | display-only, path parts/control chars stripped, NFC, 255 cap | unit: 10 cases; integration: `../../science notes.pdf` → `science notes.pdf` |
| Deduplication | `sha256 UNIQUE`, `ON CONFLICT DO NOTHING`, conditional reset of failed docs | integration: idempotent re-upload (no re-embedding), 5 concurrent identical uploads → 1 row; live UI |
| Retry behaviour | re-upload of `failed` → `pending`, `attempts + 1` | integration: scanned PDF twice, embedding failure → success on retry; live: attempt 3 after crash |
| Background ingestion | `IngestionRunner`: semaphore, task references, per-page/per-batch threads | integration + live (117 pages in ~24 s; liveness ≤ 2.7 ms throughout) |
| Short transactions | separate status transactions; atomic final write with `FOR UPDATE` | integration: pool checked-out = 0 and no `idle in transaction` mid-embedding (fails when a transaction is held) |
| Failures | `no_extractable_text`, `extraction_failed`, `embedding_failed`, `interrupted`, `internal_error` | integration: one test per path; nothing written on failure |
| Interrupted jobs | graceful: marked on shutdown; crash: recovered at startup before accepting uploads | integration: both; live: SIGTERM and SIGKILL |
| Queue bound | 503 `ingestion_queue_full` + `Retry-After` | integration |
| Embeddings | e5 prefixes, normalisation, revision pin, offline load, background load | model tests (7) |
| Chunking | page-bounded, model-token sized, boundary-aware, overlap | unit invariants (14); model: all chunks ≤ 512 tokens with prefix |
| Search | HNSW cosine, `ef_search`, iterative scan, model filter, document filter | integration (5 tests incl. a mutation-verified post-filter test, `EXPLAIN`); model: bilingual golden set, cross-lingual, e2e page citations |
| Readiness | database + embedding model (`loading` / `unavailable`) | unit + live |
| Frontend | `/documents` (drag & drop upload, live status, failure reasons, delete), `/search` (scope, top-k, citations, similarity), `/status` (3 probes) | lint/tsc/build; headless Chrome end to end (§7) |

## 4. Repository changes

**New (backend):** `core/body_limit.py`, `db/models.py`, `embeddings/{base,e5,provider,download}.py`,
`documents/{pdf,chunking,repository,ingestion,service,errors}.py`, `api/v1/{documents,search}.py`,
`migrations/versions/0002_documents_and_chunks.py`; tests `fakes.py`, `unit/{test_chunking,test_pdf,test_documents_api}.py`,
`integration/{test_documents,test_real_model_ingestion,test_audit_regressions}.py`, `model/{corpus,test_e5_embedder}.py`.

**Modified (backend):** `main.py` (lifespan: provider, runner, shutdown order; body limit), `core/config.py`
(+15 validated settings), `core/errors.py` (`AppError.headers` for `Retry-After`), `api/deps.py`,
`api/v1/{health,router}.py`, `migrations/env.py` (imports models), `pyproject.toml`, `uv.lock`;
tests `conftest.py` (model cache isolation, startup waits, `e5_embedder`), `unit/{test_app,test_config}.py`,
`integration/test_database.py` (fake embedder, head `0002`, wait for recovery).

**New (frontend):** `app/documents/{page,actions}.tsx|ts`, `app/search/page.tsx`,
`components/documents/{upload-form,document-library,auto-refresh,delete-button}.tsx`,
`components/search/result-card.tsx`, `lib/api/{documents,search}.ts`, `lib/format.ts`.
**Modified:** `lib/api/{client,types}.ts` (`apiRequest`: JSON/multipart/204, error codes),
`app/{page,status/page}.tsx`, `components/{app-shell,nav-link}.tsx` (nav, mobile fit), `next.config.ts`.

**Infrastructure/docs:** `.github/workflows/model.yml` (new), `ci.yml` (markers), `Makefile`
(`models`, `test-model`, markers), `.env.example`, README, architecture overview, ADRs 0009–0015,
this report, M1 learning guide and interview prep.

**Outside the repository:** model cache `~/.cache/huggingface/hub/models--intfloat--multilingual-e5-small`
(~490 MB). Dev database migrated to `0002`; it now contains the demo documents from §7.

## 5. Dependencies (resolved, locked)

**Added, direct:** pypdf 7.2.0 (BSD-3-Clause) · sentence-transformers 6.3.0 (Apache-2.0) ·
torch 2.15.0+cpu (BSD-style; CPU index) · pgvector 0.5.1 (MIT) · python-multipart 0.0.34
(Apache-2.0) · numpy 2.5.5 (BSD).
**Notable transitive:** transformers 5.24.1 · tokenizers 0.23.3 · huggingface-hub 2.5.0 ·
safetensors 0.8.0 · scipy 1.18.1 · scikit-learn 1.9.1. `uv.lock`: 83 packages, **no
`nvidia-*`/CUDA packages**. Venv ≈ 1.3 GB.
**Model:** intfloat/multilingual-e5-small @ `614241f622f53c4eeff9890bdc4f31cfecc418b3` (MIT).
**Frontend:** no new packages. **CI:** `actions/cache@v5` (node24) in `model.yml`.
**Not added (deliberately):** PyMuPDF, LangChain/LangGraph, anthropic, Redis/arq, Celery.

## 6. Test and quality results (executed locally, 2026-11-27)

| Check | Result |
|---|---|
| `uv lock` / `uv sync --locked` | pass (CPU-only torch resolved) |
| `ruff check .` / `ruff format --check .` | pass |
| `mypy` (strict, src + tests + migrations) | **pass, 57 files** |
| `pytest -m "not integration and not model"` | **117 passed** (after audit; 112 before) |
| `pytest -m "integration and not model"` (DB up) | **38 passed** (after audit; 31 before) |
| `pytest -m model` (model cached, DB up) | **12 passed** |
| `alembic upgrade` / `downgrade 0001` / `upgrade`; `alembic check` | pass; "No new upgrade operations detected" |
| ESLint `--max-warnings=0`, `next typegen && tsc --noEmit` | pass |
| `next build` with backend stopped | pass; `/documents`, `/search`, `/status` partial prerender |
| Client bundle scan for `API_BASE_URL` / API URL | 0 matches in `.next/static` |
| `make check` (routine CI equivalent) | pass |

**Mutation checks** (to prove key tests can fail): disabling `hnsw.iterative_scan` makes the
post-filter search test fail (5 of 10 results); holding a transaction during embedding makes
the connection test fail (1 checked-out connection).

## 7. Live verification (real processes, 2026-11-27)

Real-world PDFs from the [py-pdf sample corpus](https://github.com/py-pdf/sample-files)
(CC-BY-SA-4.0); the official GDPR PDFs were not retrievable (EUR-Lex bot challenge).

| Scenario | Observed |
|---|---|
| API start | startup 14 ms; liveness immediate; readiness `loading` → `ready` after the model load |
| Upload GeoTopo (117 pp., 1.8 MB, German LaTeX) | 202 in 95 ms; `ready` with 199 passages after ~24 s |
| During that ingestion | max liveness latency **2.7 ms** (event loop never blocked); search 0.3–1.0 s (inference lock) |
| Duplicate upload | 200, existing `ready` record, nothing re-processed |
| LibreOffice password PDF / renamed text file | 422 `pdf_encrypted` / 415 `unsupported_media_type` |
| LibreOffice, Google Docs, pdfLaTeX, two-column LaTeX, image PDF | all `ready`; quality per ADR 0010 |
| "Was ist ein topologischer Raum?" | top hit **p. 6** = "Definition 1: Ein topologischer Raum ist ein Paar (X; T) …" |
| "What is a metric space?" (EN → DE corpus) | top hit p. 11, section "Metrische Räume" |
| Search latency (200 requests, ~220 chunks) | **p50 24 ms, p95 31 ms**, max 41 ms |
| API RSS with model loaded | ~1.1 GB |
| SIGTERM during ingestion | shutdown in ~0.1 s; document `failed/interrupted` |
| SIGKILL during ingestion → restart | row left `processing`; startup recovery `count=1` → `failed/interrupted`; re-upload → `ready`, attempt 3 |
| Headless Chrome: upload via form | "Uploaded…", live polling to READY, duplicate and encrypted messages shown in the form |
| Headless Chrome: search | German query → p. 5 of the handbook; English query → p. 1 (cross-lingual) |
| Mobile 390 px | all four pages fit the viewport (found and fixed: nav overflow, pre-existing `/status` card overflow) |

## 8. Exact local startup and demonstration

```bash
make setup && make db-up && make migrate
make models                     # once, ~490 MB
make api                        # terminal 1 → http://127.0.0.1:8000/docs
make web                        # terminal 2 → http://localhost:3000
```

Demo script: README § "Demo". Real-model verification: `make test-model`.

## 9. Defects found and fixed during M1

| Finding | Fix |
|---|---|
| Upload hit the database before checking ingestion admission → 500 when the DB was down | admission check first; unit test (503 `ingestion_not_ready`) |
| FastAPI turns exceptions raised while reading the body into 400, hiding the 413 | middleware suppresses that response and sends 413 itself; streamed-body test |
| `import torch` in the lifespan blocked the event loop ~4.6 s at startup | import moved into the loader thread; startup now 14 ms |
| Chunks spanning pages produced imprecise citations ("pp. 3–4" for page-4 text) | page-bounded chunking (ADR 0013); real-model e2e asserts exact pages |
| A real-model citation test was vacuous (one 400-token chunk covered all 5 pages) | test uses small chunks and asserts the exact page |
| Iterative-scan test passed with the feature disabled (planner used the btree) | rewritten around the real post-filter case; mutation-verified |
| `ix_chunks_document_id` duplicated the `(document_id, ordinal)` unique index | removed from model and migration |
| Mobile: 4-item nav overflowed at 390 px; `/status` cards overflowed (M0 bug) | responsive nav; `min-w-0` on grid items |

## 9a. Engineering audit (2026-11-27)

Independent review of the code against six areas (upload security, data integrity,
concurrency/lifecycle, search correctness, frontend, test quality). Every suspected defect was
first reproduced by a failing test or measurement, then fixed.

### Confirmed defects (all fixed, regression-tested)

| # | Severity | Finding | Evidence | Fix |
|---|---|---|---|---|
| A1 | **High** | **Text-expansion bomb.** A 90 KiB PDF whose 500 pages share one compressed text stream passed all validation and extracted to 5,000,000 characters per page (≈ 2.5 GB of text, ≈ 1.5 M chunks): memory exhaustion and the only ingestion worker blocked for days. pypdf's 75 MB stream cap does not prevent it. | measured 2.2 s / page, 5 M chars each; regression test timed out before the fix | `MAX_PAGE_CHARS` (100 k, on **raw** text) and `MAX_DOCUMENT_CHARS` (5 M), checked page by page → `failed/text_limit_exceeded`. Live: the same file now fails after **2.8 s** |
| A1b | High | **Bypass of the first A1 fix**: the cap was first applied after normalisation, and a page of 5 M spaces normalises to "" | measured: 5,000,000 raw → 0 chars, 1.8 s / page | cap moved to raw extraction (`extract_page_text(max_chars=…)`) |
| A2 | Medium | **Uploads accepted while the model was loading or missing**, then failed as `internal_error` (every upload in the first seconds after start; *all* uploads if `make models` was never run) | regression test: 202 instead of 503 | admission requires a loaded model: **503 `embedding_model_unavailable` + `Retry-After: 5`**, nothing written; ingestion maps a missing model to `embedding_failed` defensively. Verified live |
| A3 | Medium | **Deduplication ignored the embedding model.** After a model change, re-uploading a document answered "already in the library" while it stayed invisible to search forever | regression test: 200 instead of 202 | `ready` documents of another model are re-ingested (`attempts + 1`); the reset deletes old chunks and clears `chunk_count`/`embedding_model` in the same transaction |
| A4 | Low | Filenames kept Unicode format characters (U+202E RIGHT-TO-LEFT OVERRIDE can display `…fdp.exe` as `…exe.pdf`; zero-width and line separators) | unit cases | strip categories Cc, Cf, Cs, Co, Zl, Zp |
| A5 | Low | SHA-256 of up to 20 MiB computed on the event loop | code review | hashing moved into the validation worker thread |
| A6 | Low | A database error while claiming a document (`pending → processing`) escaped the background task unlogged ("Task exception was never retrieved") | code review | caught and logged with the document id; the row is recovered at the next startup (documented) |
| A7 | Low | Keyboard focus on the upload control was invisible (the real input is `sr-only`) | code review | focus ring on the drop zone via `has-[:focus-visible]` |
| A8 | Low | The UI had no message for the new failure code | – | message added |

### Theoretical risks (not fixed; no defect reproduced)

| # | Severity | Risk | Mitigation / owner |
|---|---|---|---|
| T1 | Medium | Concurrent uploads are each buffered (≤ 20 MiB) and parsed **before** queue admission; many simultaneous uploads could use substantial memory/CPU. Bounded per request, not globally | reverse-proxy or `uvicorn --limit-concurrency`, plus auth/rate limiting (M5) |
| T2 | Low | One hostile page can still take seconds to parse (≤ 75 MB stream; ~2 s measured for 5 M chars); threads cannot be killed | now bounded to the first offending page by A1; process isolation with timeouts later |
| T3 | Low | Queries longer than 512 model tokens are truncated silently (1,000-character cap makes this rare; possible with CJK/symbol-heavy text) | acceptable; validate token length if it matters |
| T4 | Low | Shutdown while the DB is down waits up to the connect timeout per in-flight document when marking it interrupted | rows are recovered at the next start anyway |
| T5 | Low | Citations use physical 1-based page indices; printed page labels can differ (GeoTopo prints "9" on physical page 12) | consistent with PDF viewers' page field; documented |
| T6 | Low | A crafted Server Action body > 21 MB is rejected by Next.js with an unhandled client error (no error boundary); the UI disables upload above 20 MB | add `error.tsx` with M2's UI work |

### Verified as correct (independent checks)

* **Size limits:** declared and streamed bodies → 413; exact file check; nothing spooled beyond the limit.
* **Malformed/encrypted PDFs:** garbage, truncated, zero-page, password-protected → specific 4xx; parser exceptions never leak.
* **Error disclosure:** 500s are generic; tracebacks only in logs.
* **Transactions:** atomic `ready`; failure writes no chunks; `FOR UPDATE` guard against concurrent delete/reset.
* **Concurrent duplicates:** one row. A mutation check (removing `ON CONFLICT`) makes the test fail.
* **Concurrent retries:** 4 simultaneous re-uploads of a failed document → exactly one 202 (new test).
* **Shutdown:** queued *and* running documents → `failed/interrupted` (new test). Crash recovery runs before admission.
* **Responsiveness:** liveness ≤ 2.7 ms during ingestion. No connection is held during inference (mutation-verified).
* **Thread safety:** inference is serialised by a lock. The span tokenizer is a private, never-mutated copy (safe for concurrent `encode`).
* **Search correctness:**
  * `query:`/`passage:` prefixes; unit-norm 384-d vectors; dimension check at load.
  * Chunks ≤ 512 tokens with the prefix.
  * `vector_cosine_ops` ↔ `<=>`; `score = 1 − distance`.
  * Model filter; iterative scan (mutation-verified); single-page citations; cross-lingual retrieval.
* **Frontend:**
  * Loading skeletons, empty and processing states, failure reasons and API error notices.
  * Polling stops when idle, pauses in hidden tabs and is cleaned up on unmount.
  * Every response passes a runtime guard; 390 px layouts fit.
* **Routine CI is model-free:** with an empty `HF_HOME` and `HF_HUB_OFFLINE=1`, unit (117) and integration (38) tests pass and nothing is written to the cache. `-m model` **fails** (does not skip) without the model.
* **M0 intact:** all M0 tests (probes, config, errors, request IDs, session lifecycle, integration safety) pass unchanged, apart from the expected readiness payload extension.

### Accepted limitations (per review decision)
Single-process API · model memory (~1.1 GB RSS) · search latency during ingestion ·
non-durable in-memory processing with documented recovery · no OCR · no authentication.

## 10. Known limitations

* **Ingestion durability:** in-memory bytes, single process, no automatic retries, no
  per-document timeout (ADR 0012; accepted). Path: PostgreSQL job table with `SKIP LOCKED`.
* **Upload concurrency** is not bounded globally before admission (audit T1).
* **Extraction:** no OCR; math and figure text degrade; headers/footers kept; complex
  layouts may interleave (ADR 0010).
* **Retrieval:** similarity ranking only (no hybrid, no re-ranker, no threshold); approximate
  recall not yet measured against exact search; golden set is small (10 + 4 queries).
* **Latency coupling:** searches wait for the current embedding batch while ingesting.
* **Security:** no authentication or rate limiting (M5); loopback binding is the boundary.
* **Resources:** ~1.1 GB RSS per API process; ~1.3 GB venv; CI backend install grows
  accordingly.
* The web UI's 20 MiB constant mirrors the API default and must be changed together.
* The dev database now holds demo documents; `make db-reset` clears it.

## 11. Remote CI

* Commit `0016147` (setup-uv v10.4.0 pin + docs) was pushed manually. **Run #2 passed**
  (backend 39 s, frontend 25 s, total 44 s) with no annotations: the Node.js 20 deprecation
  warning is resolved.
* `model.yml` has not run on GitHub (it will on dispatch, weekly, or relevant pushes).
* The M1 commit is local; its CI run (and the first `model.yml` run, triggered by the
  `embeddings/`, `documents/` and `uv.lock` changes) will follow the manual push.

## 12. Definition of done

- [x] D1–D4 implemented as approved (D2 pypdf, D3 multilingual-e5-small)
- [x] Dependencies locked; CPU-only torch verified (no `nvidia-*`)
- [x] Model revision pinned; `make models`; offline load; readiness 503 without the model
- [x] Migration `0002` reversible; `alembic check` clean; constraints and HNSW verified
- [x] Upload limits, validation, safe filenames, specific error responses
- [x] Atomic ingestion; no connection held during extraction/embedding (verified)
- [x] Bounded queue + concurrency; graceful and crash interruption handled (verified live)
- [x] Deduplication and predictable retries
- [x] Search with accurate document and page references (exact-page e2e test)
- [x] `/documents` and `/search` functional in a real browser; dark/cyan identity kept; build without backend
- [x] Unit, integration and model suites pass; routine CI needs no model download
- [x] Performance and resource figures measured
- [x] ADRs, architecture, README, report, learning guide, interview prep
- [x] Remote CI for `0016147` (run #2, success, no annotations)
- [ ] Remote CI for the M1 commit (after the manual push)
- [x] Engineering audit: 9 confirmed defects fixed with regression tests (A1–A8, incl. A1b); 6 theoretical risks documented
- [x] Audit approved; committed locally (not pushed)
