# M1 Technical interview preparation

Fifteen questions grounded in the NEXUS AI M1 implementation (ingestion, embeddings,
vector search). Each has a senior-level model answer and the incorrect answers
interviewers commonly hear. ★ marks questions aimed at engineers moving into AI
engineering.

---

### 1. ★ Why must chunk sizes be measured with the embedding model's own tokenizer?

**Model answer:** The model has a hard context window (512 tokens for multilingual-e5-small)
and sentence-transformers **truncates silently**: everything past the window is never
embedded, so that text is unsearchable while the stored chunk claims to contain it.
Character counts and other tokenizers (tiktoken) miscount, and German or code produce many
more tokens per character than English prose. We count with offsets from a copy of the
model's fast tokenizer, cap chunks at 400 tokens to leave room for the `passage: ` prefix and
special tokens, and a real-model test asserts every chunk re-tokenises to ≤ 512.

**Common wrong answers:** "4 characters ≈ 1 token." "The model handles long input."
"Use tiktoken" (that is OpenAI's tokenizer, not this model's).

---

### 2. ★ Cosine similarity, inner product, L2 distance: which do you use, and when are they equivalent?

**Model answer:** For **unit-normalised** vectors, inner product = cosine similarity and
squared L2 = 2 − 2·cos, so all three rank identically. Use the metric the model was trained
for (e5: cosine on normalised vectors), and make the index **operator class match the query
operator** (`vector_cosine_ops` with `<=>`); a mismatch makes PostgreSQL skip the index
silently. Scores are for ranking: e5 similarities are compressed (unrelated text ≈ 0.7–0.8),
so a fixed threshold like 0.8 is meaningless without calibration.

**Common wrong answers:** "Cosine is always better." "A score of 0.8 means 80 % relevant."

---

### 3. Explain HNSW and its parameters. Why HNSW rather than IVFFlat?

**Model answer:** HNSW is a multi-layer proximity graph: search enters at a sparse top layer
and greedily descends. `m` is links per node (memory, recall), `ef_construction` the build-time
candidate list (build time, graph quality), `ef_search` the query-time candidate list (recall
vs latency). IVFFlat clusters vectors into lists using k-means on **existing** data, so it
needs data before building and degrades as the distribution drifts; HNSW has no training
step and better recall/latency, at the cost of build time and memory. We use pgvector
defaults (`m=16`, `ef_construction=64`) and set `ef_search=64` per transaction.

**Common wrong answers:** "HNSW is exact." "More `m` is always better."

---

### 4. ★ A filtered vector query asks for 10 results and returns 5. What happened?

**Model answer:** Approximate indexes **post-filter**: the HNSW scan produces `ef_search`
candidates by distance, and the `WHERE` clause removes non-matching rows afterwards. If most
candidates are filtered out, fewer than `LIMIT` rows remain. We reproduced it: most chunks
belonged to another embedding model, `ef_search=10`, iterative scan off → 5 rows. Fixes:
pgvector ≥ 0.8 `hnsw.iterative_scan = relaxed_order` (keeps scanning; re-sort because order is
relaxed), a larger `ef_search`, partial indexes per tenant/model, or letting the planner use a
selective btree and sort exactly. For our `document_ids` filter the planner already chooses
the `(document_id, ordinal)` btree, which is exact.

**Common wrong answers:** "There weren't enough matching rows." "Add an index on the filter
column" (it helps only if the planner then avoids the HNSW scan).

---

### 5. Why does calling the embedding model inside `async def` without `to_thread` cause an incident, and why do threads help despite the GIL?

**Model answer:** The event loop is single-threaded; a 2-second inference call blocks **every**
request on that worker, including health checks, so orchestrators may kill the pod.
`asyncio.to_thread` moves it to a worker thread. Threads help because PyTorch kernels and the
Rust tokenizers **release the GIL**; pure-Python CPU work would still contend for it and
belongs in processes. We measured liveness ≤ 2.7 ms during a 117-page ingestion, and caught a
4.6 s startup freeze caused by `import torch` running on the loop.

**Common wrong answers:** "Async makes it non-blocking." "The GIL means threads never help."

---

### 6. What is wrong with FastAPI `BackgroundTasks` as an ingestion queue? What did you build, and what would you change at scale?

**Model answer:** `BackgroundTasks` runs after the response, in-process, with no bound, no
status, no retry and no shutdown or crash story. We built a runner with **admission control**
(capacity 8 → 503 + `Retry-After`), a semaphore (concurrency 1), task references, states in
PostgreSQL, graceful interruption, and startup recovery that runs *before* uploads are
accepted. It is still not durable (bytes in memory) and single-process. At scale: a
PostgreSQL job table claimed with `SELECT … FOR UPDATE SKIP LOCKED` and leases/heartbeats,
stored originals, separate worker processes with hard timeouts, retries with backoff and
idempotent handlers. That needs no Redis.

**Common wrong answers:** "Use Celery" (without explaining which failure mode it fixes).
"`asyncio.create_task` is enough."

---

### 7. How do you make ingestion idempotent and safe under concurrency?

**Model answer:** Identity is the **content hash** (`sha256 UNIQUE`), not the filename.
Concurrent identical uploads: `INSERT … ON CONFLICT (sha256) DO NOTHING RETURNING`, and the
loser re-reads the winner (a test fires 5 concurrent uploads and expects one row and one 202).
Retries: a conditional `UPDATE … WHERE status = 'failed'` resets the record, so two retries
can't both win. Completion: chunks + `ready` in **one transaction** behind `SELECT … FOR UPDATE`
on a still-`processing` row, so a concurrent delete wins cleanly and a failure leaves no
partial chunks.

**Common wrong answers:** "Check if it exists, then insert" (a race). "Dedupe by filename."

---

### 8. Why not keep the database session open while embedding a 300-page PDF?

**Model answer:** The pool is small (5 + 5 overflow); a held connection during minutes of CPU
work starves requests. An open transaction is `idle in transaction` on the server: it holds
locks and snapshots, blocks vacuum from removing dead tuples, and can hit
`idle_in_transaction_session_timeout`. Pattern: short transaction to mark `processing`,
work with no connection, short transaction to write results. We assert zero checked-out
connections and no `idle in transaction` backend mid-embedding, and a mutation (holding a
transaction) makes that test fail.

**Common wrong answers:** "Connections are cheap." "Async sessions don't hold connections."

---

### 9. How do you accept untrusted PDF uploads safely?

**Model answer:** Bound bytes **while streaming** (Starlette spools the whole multipart body
before the endpoint runs, so we count in ASGI `receive`); check magic bytes, not the client's
`Content-Type`; parse defensively and map *any* parser exception to `pdf_invalid`; reject
encryption and excessive page counts; extract text only (no rendering, JavaScript or
embedded files); treat filenames as display strings, never paths; bound the queue. Residual
risk: a pathological file can occupy the worker thread, because threads can't be killed. The
fix is process isolation with a hard timeout. Licence is part of supply-chain safety too:
PyMuPDF's AGPL led us to pypdf.

**Common wrong answers:** "Check the file extension." "Use `Content-Length`."

---

### 10. ★ You switch embedding models. What breaks, and how do you migrate without downtime?

**Model answer:** Vectors from different models live in unrelated spaces: comparing them gives
garbage, and the dimension may change (`vector(384)` → `vector(768)`). We record
`embedding_model` (`name@revision`) per document and search only the active model's chunks,
so mixed states are safe but incomplete. Migration: add a new column or table + index for the
new model, backfill by re-embedding in the background, dual-write new uploads, switch reads
when coverage reaches 100 %, then drop the old column. Re-evaluate retrieval quality before
the switch.

**Common wrong answers:** "Just reindex." "Normalise both and compare."

---

### 11. ★ Why pin a Hugging Face model by commit hash and load it offline? What does `trust_remote_code` risk?

**Model answer:** A model repository is a git repo; `main` can change weights, tokenizer or
pooling config without notice, so results and stored vectors drift. Pinning a commit (validated
as 40 hex chars) makes it reproducible. Offline loading (`local_files_only=True`, explicit
`make models`) means production never depends on the Hub at request time, and a missing model
is a **readiness** failure, not a hang. `trust_remote_code=True` executes Python from the
repository at load time: arbitrary code execution. Prefer `safetensors` over pickle-based
`.bin` weights for the same reason.

**Common wrong answers:** "Version tags are enough." "Downloads are cached anyway."

---

### 12. How do you test code that depends on a 470 MB neural model without slow or flaky CI?

**Model answer:** Depend on a `Protocol`, not the library. Unit and integration tests inject a
deterministic **fake** (feature hashing of words), so ranking, filtering, persistence and
concurrency are tested exactly. Test **invariants** (chunk limits, coverage, page ranges)
rather than snapshots. Keep real-model tests behind a marker (`-m model`) with their own
workflow (weekly, on relevant changes, manual), and make them **fail, not skip**, when the model
is missing. Add a small golden set (MRR, recall@k) and an end-to-end test that asserts the
user-visible claim: the exact cited page.

**Common wrong answers:** "Mock `encode()` to return zeros" (ranking becomes untestable).
"Download the model in every CI run."

---

### 13. A test streams a 400 KB chunked body against a 4 KiB file limit (68 KiB body allowance) and gets 400, not 413. Why?

**Model answer:** The middleware raised an exception from `receive()` when the limit was crossed,
but FastAPI's request-body reader catches **any** exception while parsing and answers
"400 There was an error parsing the body", so our exception never reached the middleware. Fix:
the middleware records that the limit was exceeded, suppresses the app's response, and sends a
413 itself. Lessons: know which layer converts exceptions into responses, and test the streamed
path, not just the `Content-Length` fast path.

**Common wrong answers:** "Set `max_part_size`" (applies to form fields, not files).
"Check the size in the endpoint" (already spooled to disk).

---

### 14. Why does M1 run a single uvicorn worker? What is the scaling path?

**Model answer:** Each process holds its own model (~1.1 GB RSS measured) and its own
in-memory queue; with several workers, startup recovery in one process would mark another's
in-flight documents as interrupted, and the queue bound would be per process. Scaling path:
API replicas that only enqueue and search, a durable job table, separate ingestion workers
(CPU or GPU, possibly a dedicated embedding service with batching), and search queries routed
to an embedding endpoint or a smaller ONNX model in the API. The inference lock also couples
search latency to ingestion (0.3–1.0 s during a batch), which disappears once ingestion runs
elsewhere.

**Common wrong answers:** "Add `--workers 4`." "Python can't scale."

---

### 15. ★ Users say search returns plausible but wrong passages. How do you diagnose it?

**Model answer:** Measure before tuning: build a golden set (query → relevant page) and track
recall@k and MRR (we start with a bilingual EN/DE set, MRR 1.0). Then isolate layers:
**extraction** (look at stored chunk text: math, headers, wrong reading order; e.g. our LaTeX
math extracted as `kx1 x2k`), **chunking** (too large dilutes, too small loses context; page
cuts), **embedding usage** (missing `query:`/`passage:` prefixes, truncation, normalisation),
**index** (compare HNSW to exact search, raise `ef_search`, check filters/iterative scan), and
**ranking limits** (exact terms and IDs need hybrid BM25 + vector; ambiguous queries need a
cross-encoder re-ranker). Only then change models. M4's RAGAS evaluation formalises this.

**Common wrong answers:** "Use a bigger model." "Increase `top_k`."
