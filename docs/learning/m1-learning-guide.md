# M1 Learning Guide: Ingestion, embeddings and vector search for a TypeScript engineer

Concise by design: each topic names what was built, why, how it works, the closest
TypeScript/Node equivalent, and the production mistakes it avoids. Every topic points at
real M1 code.

---

## 1. File uploads in FastAPI

**Code:** `api/v1/documents.py`, `core/body_limit.py`, `documents/service.py`

`file: Annotated[UploadFile, File()]` needs `python-multipart`. Starlette parses the
**whole** multipart body before your endpoint runs, spooling to a temporary file above
1 MB. So a size check in the endpoint is too late: the bytes are already on disk.
`BodySizeLimitMiddleware` counts bytes in ASGI `receive()` instead.

```ts
// Next.js Route Handler: also buffers the whole body before you see it
const file = (await req.formData()).get("file") as File;
```
```python
async def upload_document(file: Annotated[UploadFile, File()]) -> DocumentOut:
    data = await file.read()          # bounded only because the middleware bounded the stream
```

**Mistakes avoided:** trusting `Content-Length` (absent with chunked encoding) or the
client's `Content-Type` (we check `%PDF-` bytes); using the filename as a path.
**Gotcha found:** FastAPI catches *any* exception raised while reading the body and answers
400. The middleware therefore suppresses that response and sends its own 413.

## 2. CPU-bound work, the GIL and `asyncio.to_thread`

**Code:** `documents/ingestion.py` (`_extract`, `_embed`), `api/v1/search.py`

One event loop thread serves every request. PDF parsing and inference are CPU-bound; run on
the loop, they freeze every request (we measured a 4.6 s freeze when `import torch` ran in
the lifespan). `asyncio.to_thread(fn, *args)` runs `fn` in a worker thread. Python's **GIL**
lets one thread execute Python bytecode at a time, but native code (PyTorch kernels,
tokenizers in Rust) releases it, so threads give real parallelism there. Pure-Python CPU
work (pypdf) still competes for the GIL; it only stops blocking the loop between bytecode
slices.

```ts
const vectors = await runInWorker("embed", texts);   // worker_threads: separate heap, messages
```
```python
vectors = await asyncio.to_thread(embedder.embed_passages, batch)  # shared memory, GIL rules
```

**Design detail:** work is split per page / per batch, because a running thread cannot be
cancelled; cancellation only takes effect between `await`s. **Measured:** liveness stayed
≤ 2.7 ms during a 117-page ingestion.

## 3. Background tasks in an ASGI process

**Code:** `documents/ingestion.py` (`IngestionRunner`)

`asyncio.create_task()` is the "fire and forget" primitive, like an un-awaited promise, with
two Python-specific traps: the loop keeps only a **weak reference** (an unreferenced task can
be garbage-collected mid-flight), and an exception in a task nobody awaits is only logged at
GC time. The runner stores tasks in a dict, bounds admission with a counter, limits
parallelism with `asyncio.Semaphore`, and cancels + awaits everything on shutdown.

```ts
const limit = pLimit(1); void limit(() => ingest(doc));        // p-limit
```
```python
task = asyncio.create_task(self._run(doc_id, data))
self._tasks[doc_id] = task                                      # strong reference
task.add_done_callback(lambda _: self._tasks.pop(doc_id, None))
async with self._semaphore: ...                                 # concurrency limit
```

**Why not FastAPI `BackgroundTasks`?** No bound, no status, no shutdown or recovery story.
**Not durable:** see ADR 0012 for the crash semantics and the `SKIP LOCKED` upgrade path.

## 4. Short transactions around slow work

**Code:** `documents/repository.py` (`mark_processing`, `save_ready`, `mark_failed`)

Read → work with **no connection** → write. Each ingestion step opens its own session from
the sessionmaker (`async with sessionmaker() as s, s.begin():`) rather than holding the
request's session. The final write is atomic and guarded:
`SELECT … FOR UPDATE WHERE status='processing'`, so a concurrent delete or reset wins cleanly.
**Verified:** an integration test asserts zero checked-out connections and no
`idle in transaction` backends while embedding.

## 5. PDF text extraction with pypdf

**Code:** `documents/pdf.py`

A PDF page is a stream of drawing operators that place glyphs at coordinates; "text" is
reconstructed from fonts' Unicode maps and positions. Hence: no guaranteed reading order,
math fonts without Unicode maps produce garbage, scanned pages have no text at all (that
needs OCR). Normalisation decisions: NFKC (ligatures `ﬁ` → `fi`), strip NUL (PostgreSQL
rejects it), re-join hyphenation only between lower-case letters (keeps
`Datenschutz-Grundverordnung`).

**Licensing lesson:** PyMuPDF is AGPL. In a network service, AGPL obligations reach the whole
service; pypdf (BSD) avoids that (ADR 0010).

## 6. Tokenization and chunking

**Code:** `documents/chunking.py`, `embeddings/e5.py` (`token_spans`)

Transformers see **tokens** (sub-words), not characters. e5 uses XLM-RoBERTa's
SentencePiece vocabulary: `Grundverordnung` → `Grund`, `ver`, `ordnung`. German produces
more tokens per word than English. The model **silently truncates** after 512 tokens, so
chunks are measured with *its* tokenizer (offset spans from a private copy without
truncation) and capped at 400 + prefix. Chunks never cross pages, so citations are exact.

```ts
function* chunks(page: string): Generator<Chunk> { /* … */ }    // same idea as Python generators
```

## 7. Embeddings and similarity

**Code:** `embeddings/base.py`, `embeddings/e5.py`

An embedding maps text to a point in a 384-dimensional space where nearby means similar
meaning. e5 is trained **asymmetrically**: queries are prefixed `query: `, documents
`passage: `. Vectors are L2-normalised, so cosine similarity = dot product, and
`1 − cosine distance` is the score. Scores are **compressed** (unrelated text ≈ 0.7–0.8), so
use them for ranking, not as an absolute threshold. Vectors from different models are
incomparable, so every document records `embedding_model` and search filters on it.

## 8. The Hugging Face stack and reproducibility

**Code:** `embeddings/e5.py`, `embeddings/download.py`, `pyproject.toml`

* **Hub revisions** are git commits; `main` moves. Pin a 40-char hash (validated in
  `Settings`).
* `local_files_only=True` at runtime, explicit `make models` download, `HF_HOME` cache layout
  `models--org--name/snapshots/<rev>/`.
* `trust_remote_code=False`: otherwise loading a model can execute arbitrary Python from the
  repository. Prefer `safetensors` (pickle-free) weights.
* **PyTorch wheels:** the default Linux wheel bundles CUDA (~3 GB). A uv **explicit index**
  pins `torch` to `download.pytorch.org/whl/cpu`; `uv.lock` records it.

```toml
[[tool.uv.index]]
name = "pytorch-cpu"
url = "https://download.pytorch.org/whl/cpu"
explicit = true            # only packages that opt in use this index
[tool.uv.sources]
torch = { index = "pytorch-cpu" }
```

The npm analogue is a scoped registry in `.npmrc` (`@scope:registry=…`), with the lockfile
recording resolved URLs.

## 9. pgvector: HNSW, operators and filtering

**Code:** `migrations/versions/0002_documents_and_chunks.py`, `documents/repository.py` (`search_chunks`)

* `vector(384)` column; operators `<=>` cosine, `<#>` negative inner product, `<->` L2. The
  index opclass **must match** the operator (`vector_cosine_ops` ↔ `<=>`), or the planner
  silently does a sequential scan.
* **HNSW** is a layered proximity graph: `m` = links per node, `ef_construction` = build-time
  candidate list, `ef_search` = query-time candidate list (recall ↔ latency).
* **Filtering is post-filtering** on the index scan: with `ef_search = 10` and most rows
  filtered out, you get fewer than `LIMIT` rows. pgvector 0.8 `iterative_scan` keeps
  scanning. We measured 5/10 rows without it, 10/10 with it.
* `set_config('hnsw.ef_search', '64', true)` = `SET LOCAL`, but parameterisable: scoped to
  the transaction, safe with connection pools.

## 10. Alembic for constraints and vector indexes

**Code:** `0002_documents_and_chunks.py`, `migrations/env.py`

`status` is `VARCHAR + CHECK` mapped to a `StrEnum`, not a PG `ENUM` (adding/removing enum
values is awkward and not cleanly reversible). The HNSW index is built inside the migration
transaction because the table is new; on a large live table you would use
`CREATE INDEX CONCURRENTLY`, which **cannot run inside a transaction**. `alembic check`
confirms the models and migrations agree. `env.py` must import the models, or autogenerate
sees an empty schema.

## 11. `typing.Protocol` and deterministic test doubles

**Code:** `embeddings/base.py`, `tests/fakes.py`, `documents/chunking.py` (`Tokenizer`)

`Protocol` is structural typing, like a TypeScript `interface`: `HashEmbedder` satisfies
`Embedder` by shape, with no inheritance. mypy checks it statically; at runtime nothing is
enforced unless you add `@runtime_checkable`.

```ts
interface Embedder { readonly dimension: number; embedQuery(text: string): number[] }
```
```python
class Embedder(Protocol):
    @property
    def dimension(self) -> int: ...
    def embed_query(self, text: str) -> FloatMatrix: ...
```

The fake hashes words into dimensions, so ranking tests are meaningful and deterministic
without a 470 MB model. Real semantics live in `-m model` tests. Fakes (working
implementations) beat mocks (call recorders) here, because the tests assert outcomes.

## 12. Next.js Server Actions for uploads

**Code:** `app/documents/actions.ts`, `components/documents/upload-form.tsx`, `next.config.ts`

`useActionState(uploadAction, initial)` gives `[state, formAction, pending]`. The action
runs on the server, forwards the `File` as `FormData` to the API, and calls `refresh()` so
the library re-renders in the same response. The default action body limit is **1 MB**, so
`serverActions.bodySizeLimit` is set to 21 MB. Status polling is a client
`router.refresh()` every 2 s while anything is active. `Date.now()` in a Server Component is
flagged as impure by the React compiler lint, so timestamps are formatted absolutely.

## Bugs caught during M1 (and what they teach)

| Bug | Lesson |
|---|---|
| `import torch` inside the lifespan froze startup for 4.6 s | Imports execute code; heavy imports belong off the event loop |
| FastAPI turned the 413 into a 400 | Know which layer converts exceptions; test the streamed path, not just `Content-Length` |
| Cross-page chunks cited "pp. 3–4" for page-4 text | Validate the user-facing claim (the citation) end to end with the real model |
| A citation test passed vacuously | Check that a test *can* fail (mutation or inspection) |
| The iterative-scan test didn't exercise iterative scans | `EXPLAIN` before trusting a performance/correctness test |
| Admission checked after a DB read → 500 when the DB was down | Order validation from cheapest/most local to most remote |
