# ADR 0013: Page-bounded, token-aware chunking

* Status: Accepted (M1, 2026-11-27)

## Context
Chunks are what gets embedded and cited. Too large, and the model truncates silently at
512 tokens; too small, and passages lose context. Every hit must cite the right page.

## Decision
* Sizes are counted in the **embedding model's own tokens** (a private copy of its fast
  tokenizer, without truncation). Default 400 tokens, overlap 60. Validation enforces
  `CHUNK_TOKENS ≤ 496` (512 minus room for `passage: ` and special tokens) and
  overlap < half the chunk size. A real-model test asserts every chunk re-tokenises to
  ≤ 512 tokens with the prefix.
* Each window is cut at the best boundary in its second half:
  paragraph break > sentence end > word boundary; the next window starts up to `overlap`
  tokens earlier, aligned to a word start.
* **Chunks never cross a page boundary**, so every citation is a single page.
* Our own ~100-line pure-function implementation; invariants are unit-tested (no empty
  chunks, token limit, contiguous coverage, bounded overlap, page ranges, determinism).

## Consequences
* + No silent truncation; exact, single-page citations.
* + Deterministic and fast to test with a fake tokenizer.
* − A sentence continuing on the next page is split into two chunks; very short pages give
  small chunks.
* The first implementation allowed cross-page chunks. A real-model end-to-end test showed
  a hit citing "pp. 3–4" for text that was entirely on page 4 (the cut search only covers
  the second half of each window, so an early page break was never chosen). Page-bounded
  chunking replaced it.
* Rejected: `langchain-text-splitters` (counts characters or tiktoken tokens by default,
  no page tracking; LangChain arrives in M3 when it is needed), fixed character windows.
