# NEXUS AI

An AI engineering platform built as a production-quality portfolio project.

> **Status: Milestone 1 (Document ingestion & semantic retrieval).** PDFs are uploaded,
> extracted page by page, chunked, embedded with a local multilingual model and stored in
> PostgreSQL + pgvector. Semantic search returns passages with document and page
> citations, in English, German and ~100 other languages. **No LLM calls yet**: grounded
> answers with Claude arrive in M2.

## What exists today

| Area | Implemented |
|---|---|
| Backend | FastAPI app factory, lifespan-managed resources, Pydantic Settings, structlog, request-ID middleware, error envelope, CORS, body-size limit, `/api/v1/health/{live,ready}`, `/api/v1/documents`, `/api/v1/search` |
| Ingestion | PDF validation (size, magic bytes, encryption, page count), per-page extraction with **pypdf**, page-bounded token-aware chunking, background ingestion with a bounded queue, status polling, SHA-256 deduplication, interruption recovery |
| Embeddings | `intfloat/multilingual-e5-small` (384-d, MIT) via sentence-transformers on CPU-only PyTorch, pinned revision, offline loading |
| Database | PostgreSQL 17 + pgvector 0.8.7 (Docker, `127.0.0.1:5434`), async SQLAlchemy 2.0, Alembic `0001` (pgvector) + `0002` (documents, chunks, HNSW cosine index) |
| Frontend | Next.js 16 App Router, strict TypeScript, Tailwind v4, `/documents` (upload, live status, delete), `/search` (cited results, scope filter), `/status` |
| Quality | Ruff, mypy (strict), pytest (unit · real-DB integration · real-model), ESLint, `tsc`, GitHub Actions (routine CI + separate model workflow) |

## Repository layout

```text
nexus-ai/
├── apps/
│   ├── api/                 # Python backend (uv project, src layout)
│   │   ├── src/nexus_api/   # core/, db/, embeddings/, documents/, api/v1/
│   │   ├── migrations/      # Alembic (async env): 0001 pgvector, 0002 documents + chunks
│   │   └── tests/           # unit/ · integration/ (PostgreSQL) · model/ (real model)
│   └── web/                 # Next.js frontend (src/app, src/components, src/lib)
├── docs/                    # architecture/, decisions/ (ADRs), milestones/, learning/
├── .github/workflows/       # ci.yml (routine), model.yml (real embedding model)
├── compose.yaml             # PostgreSQL + pgvector (Compose project "nexus")
├── Makefile                 # developer command surface (`make help`)
├── .env.example             # backend + Compose configuration template
├── .nvmrc                   # Node 24.22.2
└── .python-version          # Python 3.12.16
```

## Prerequisites

| Tool | Version used | Notes |
|---|---|---|
| Python | 3.12.16 | uv picks it up from `.python-version` |
| uv | 0.13.9 | Python package/project manager |
| Node.js | 24.22.2 | `nvm use` reads `.nvmrc` |
| npm | 11.x | bundled with Node |
| Docker + Compose v2 | 26.x / 2.27+ | for PostgreSQL only |
| GNU Make | any | optional, wraps the commands below |

Resources: ~1.3 GB for the Python environment (CPU-only PyTorch), ~490 MB for the model
cache, ~1.1 GB RAM for the API process once the model is loaded. No GPU needed.

## Quick start

```bash
make setup            # create .env from .env.example, uv sync --locked, npm ci
make db-up            # start PostgreSQL + pgvector on 127.0.0.1:5434 (waits for healthy)
make migrate          # alembic upgrade head (pgvector, documents, chunks, HNSW index)
make models           # one-time: download the pinned embedding model (~490 MB)
make api              # http://127.0.0.1:8000  (docs: /docs)
make web              # http://localhost:3000  (in another terminal)
```

`/status` shows the API, the database and the embedding model. The model loads in the
background a few seconds after the API starts (readiness says `loading` until then).

## Demo (about 3 minutes)

1. Open <http://localhost:3000/documents> and upload a text-based PDF (up to 20 MB). Any
   English or German document works. Public test PDFs are available in the
   [py-pdf sample corpus](https://github.com/py-pdf/sample-files) (CC-BY-SA-4.0), e.g.
   `009-pdflatex-geotopo/GeoTopo-komprimiert.pdf` (117 pages, German).
2. The upload answers immediately; the row moves `pending → processing → ready` while the
   page refreshes itself (~25 s for 117 pages on 8 cores).
3. Upload the **same file again**: it is recognised by content hash and not re-processed.
   Upload a password-protected PDF: it is rejected with a specific message.
4. Open <http://localhost:3000/search> and ask, for the GeoTopo example:
   * `Was ist ein topologischer Raum?` → top hit on **p. 6** ("Definition 1: Ein
     topologischer Raum ist …")
   * `What is a metric space?` → German passages on metric spaces (**cross-lingual**)
   * Use **Scope** to search a single document. Each result shows its page citation and
     cosine similarity.
5. Delete a document from the library: its passages disappear from search.

The same flow via the API:

```bash
curl -F "file=@GeoTopo-komprimiert.pdf;type=application/pdf" http://127.0.0.1:8000/api/v1/documents
curl http://127.0.0.1:8000/api/v1/documents/<id>                    # poll until "ready"
curl -H 'Content-Type: application/json' -d '{"query":"Was ist ein topologischer Raum?","top_k":3}' \
     http://127.0.0.1:8000/api/v1/search
```

## Configuration

Backend and Docker Compose share one file: **`.env`** at the repository root
(copy of [`.env.example`](.env.example), git-ignored). The backend reads **only**
process environment variables; the Makefile injects `.env` with `uv run --env-file`.

| Variable | Default | Purpose |
|---|---|---|
| `APP_ENV` | `local` | `local` \| `test` \| `production` (production disables `/docs` and requires JSON logs) |
| `LOG_LEVEL` / `LOG_FORMAT` | `INFO` / `console` | Root log level; `console` (dev) or `json` |
| `API_HOST` / `API_PORT` | `127.0.0.1` / `8000` | Used by the `nexus-api` entry point |
| `CORS_ORIGINS` | `http://localhost:3000` | Comma-separated explicit origins; `*` is rejected |
| `POSTGRES_HOST` / `POSTGRES_PORT` | `127.0.0.1` / `5434` | Host-side address of the database |
| `POSTGRES_USER` / `POSTGRES_DB` | `nexus` / `nexus` | Also used by Compose to initialise the DB |
| `POSTGRES_PASSWORD` | **required** | No default, by design |
| `DB_POOL_SIZE` / `DB_MAX_OVERFLOW` | `5` / `5` | SQLAlchemy pool sizing |
| `DB_CONNECT_TIMEOUT_SECONDS` / `DB_HEALTH_TIMEOUT_SECONDS` | `5` / `2` | Connect timeout; readiness bound |
| `MAX_UPLOAD_BYTES` / `MAX_PDF_PAGES` | `20971520` / `500` | Upload limits (20 MiB) |
| `MAX_PAGE_CHARS` / `MAX_DOCUMENT_CHARS` | `100000` / `5000000` | Extracted-text caps (raw text per page, total per document); exceeding them fails the document with `text_limit_exceeded` |
| `EMBEDDING_MODEL_NAME` | `intfloat/multilingual-e5-small` | Hugging Face model id |
| `EMBEDDING_MODEL_REVISION` | `614241f6…cc418b3` | **Commit hash** (validated); never a branch |
| `EMBEDDING_CACHE_DIR` | unset | Model cache; unset = Hugging Face default (`$HF_HOME/hub`) |
| `EMBEDDING_BATCH_SIZE` / `EMBEDDING_THREADS` | `16` / `4` | Inference batch size; PyTorch threads |
| `CHUNK_TOKENS` / `CHUNK_OVERLAP_TOKENS` | `400` / `60` | In model tokens; `CHUNK_TOKENS ≤ 496` |
| `INGEST_MAX_CONCURRENCY` / `INGEST_QUEUE_CAPACITY` | `1` / `8` | Parallel ingestions; accepted-but-unfinished limit |
| `SEARCH_TOP_K_MAX` / `SEARCH_EF_SEARCH` | `20` / `64` | Result cap; HNSW candidate list size |

Frontend (optional `apps/web/.env.local`, see [`apps/web/.env.example`](apps/web/.env.example)):

| Variable | Default | Purpose |
|---|---|---|
| `API_BASE_URL` | `http://127.0.0.1:8000` | **Server-only.** API base URL as seen from the Next.js server |
| `API_TIMEOUT_MS` | `3000` | **Server-only.** Default request timeout (uploads use 30 s, search 10 s) |

## Commands

Run `make help` for the full list.

| Command | What it does |
|---|---|
| `make db-up` / `make db-down` | Start (wait for healthy) / stop PostgreSQL. Data persists in volume `nexus_pgdata` |
| `make db-status`, `make db-logs`, `make db-psql` | Inspect the database container |
| `make db-reset` | **Destructive.** Deletes `nexus_pgdata` (asks for confirmation), recreates, migrates |
| `make migrate` / `make migrate-down` / `make migrate-status` | Upgrade to head / revert one / show state |
| `make models` | Download the pinned embedding model into the Hugging Face cache |
| `make api` / `make web` | Run backend (reload) / frontend (dev) |
| `make lint`, `make typecheck`, `make format` | Ruff + ESLint, mypy + tsc, Ruff auto-format |
| `make test-unit` | Backend unit tests: **no database, no model** |
| `make test-integration` | Integration tests against real PostgreSQL (fails, never skips, if the DB is down) |
| `make test-model` | Real embedding model tests incl. end-to-end ingestion (needs `make models` + `make db-up`) |
| `make test` | Unit + integration (no model) |
| `make check` | Everything routine CI runs (requires `make db-up`) |

## Testing strategy

* **Unit** (`tests/unit`, 117 tests): hermetic. Real ASGI app in-process, database on a closed
  port, a deterministic feature-hashing embedder instead of the neural model, PDFs generated
  in-test (no binary fixtures). Covers chunking invariants, PDF validation/normalisation,
  filename sanitising, size limits (declared and streamed), error contracts, configuration.
* **Integration** (`-m integration`, 38 tests): real PostgreSQL in a throwaway
  `<db>_test` database. Covers ingestion end to end, deduplication incl. concurrent
  duplicates, failure paths, graceful and crash interruption, the bounded queue, "no
  connection held while embedding", cascade delete, keyset pagination, HNSW usage, filtered
  and post-filtered search, schema constraints, and the audit regressions (text-expansion
  bombs, uploads while the model loads, re-ingestion after a model change, concurrent
  retries, shutdown with queued documents).
* **Model** (`-m model`, 12 tests): the real pinned model. Dimension/normalisation,
  prefixes, a bilingual EN/DE golden set (MRR 1.0), cross-lingual retrieval, real-tokenizer
  chunk limits, and end-to-end upload → search with exact page citations.
* Routine CI never downloads the model; `.github/workflows/model.yml` verifies it separately.

## Documentation

* [Architecture overview](docs/architecture/overview.md) · [ADRs](docs/decisions/)
* [M1 implementation report](docs/milestones/m1-report.md) · [M1 plan](docs/milestones/m1-plan.md) · [M0 report](docs/milestones/m0-report.md)
* Learning: [M1 guide](docs/learning/m1-learning-guide.md) · [M1 interview prep](docs/learning/m1-interview-prep.md) · [M0 guide](docs/learning/m0-learning-guide.md) · [M0 interview prep](docs/learning/m0-interview-prep.md)

## Known limitations (M1)

* No authentication/authorization or rate limiting (M5). The API binds to loopback.
* In-process ingestion is not durable: a crash fails in-flight documents (re-upload retries
  them), and only one API process is supported (ADR 0012).
* pypdf extraction: no OCR (scanned PDFs fail with `no_extractable_text`), math symbols and
  figure labels extract poorly, running headers/footers stay in passages (ADR 0010).
* Search ranks by similarity only: no hybrid keyword search, no re-ranking, no relevance
  threshold (e5 scores are compressed). Search slows to ~0.3–1 s while a document is being
  embedded (shared inference lock).
* No Dockerfiles for the API or web app; only PostgreSQL is containerised.
