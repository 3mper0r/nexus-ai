# ADR 0011: Local multilingual embeddings (multilingual-e5-small) on CPU

* Status: Accepted (M1, 2026-11-27)

## Context
Retrieval must work for English and German documents and queries, including across
languages, run locally and in CI without GPUs or API keys, and be reproducible.

## Decision
* Model **`intfloat/multilingual-e5-small`**, pinned to revision
  `614241f622f53c4eeff9890bdc4f31cfecc418b3`, loaded with **sentence-transformers 6.3** on
  **CPU-only PyTorch 2.15** (uv explicit index `download.pytorch.org/whl/cpu`; the lockfile
  contains no `nvidia-*` packages).
* Facts from the pinned configuration: 12-layer BERT-style encoder, XLM-RoBERTa tokenizer
  (250k vocabulary, ~100 languages), **384 dimensions**, mean pooling + L2 normalisation,
  **512-token** limit, **MIT** licence. Weights: 471 MB (`model.safetensors`).
* Inputs are prefixed **`query: `** / **`passage: `** as the model card requires. Vectors are
  normalised, so cosine similarity equals the dot product.
* Download is an explicit step (`make models`, only the 9 files the torch backend needs,
  ~490 MB). Runtime loading uses `local_files_only=True`, `trust_remote_code=False`; a
  missing model is a readiness failure (`embedding_model: unavailable`), never a download.
* The model loads in a **background thread after startup** (including the torch import),
  so liveness is immediate; readiness reports `loading` until done.
* Domain code depends on an `Embedder` **Protocol**; tests inject a deterministic
  feature-hashing embedder. Real-model behaviour has its own `model` test marker and CI
  workflow.
* Inference is serialised with a lock: sentence-transformers mutates the fast tokenizer's
  truncation/padding state per call, which is not thread-safe. A separate copy of the
  tokenizer (no truncation, no padding) measures chunk sizes.
* `torch.set_num_threads(EMBEDDING_THREADS=4)` leaves cores for the event loop and DB.

## Measured (8-core Linux, 2026-11-27)

| Metric | Value |
|---|---|
| Model load (warm disk) | ~3 s (+ torch import) |
| API process RSS with model loaded | ~1.1 GB |
| Batch of 16 × ~400-token passages | ~1.8 s |
| Query embedding | ~16 ms |
| Search p50 / p95 (HTTP, ~220 chunks) | 24 ms / 31 ms |
| Search while a document is being embedded | 0.3–1.0 s (waits for the current batch) |
| Bilingual golden set (10 queries, EN/DE) | MRR 1.0, every query's top hit on topic |

## Consequences
* + No API keys, no data leaves the machine, deterministic results, cross-lingual search.
* + Revision pinning and offline loading remove a supply-chain and availability risk.
* − ~1.1 GB RSS per API process; multiple uvicorn workers multiply it (M1 runs one).
* − Search latency degrades while ingestion holds the inference lock (bounded by one batch;
  smaller `EMBEDDING_BATCH_SIZE` trades throughput for latency).
* − e5 similarity scores are compressed (unrelated text still scores ~0.7–0.8), so scores
  are only meaningful for ranking, not as an absolute relevance threshold.
* − The venv grows to ~1.3 GB (torch, transformers, scipy, scikit-learn via
  sentence-transformers).
* Rejected: `bge-small-en-v1.5` (English-only), Hugging Face Inference API (network
  dependency, token management, data leaves the machine), larger e5 variants (3–6× slower
  on CPU for a modest gain), ONNX/`fastembed` (lighter, revisit when containerising in M5).
