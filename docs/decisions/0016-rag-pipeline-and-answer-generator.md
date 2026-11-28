# ADR 0016: RAG pipeline and the `AnswerGenerator` boundary (LangChain `ChatAnthropic`)

* Status: Accepted (M2, 2026-11-27)

## Context
M2 adds grounded answers with Claude. The approved stack names LangChain; the paid API
must stay out of routine tests; and the generation call needs search-result citations,
usage including cache counters, stop reasons and reliable cancellation.

## Decision
* Domain code (`rag/`) depends on a small `AnswerGenerator` Protocol
  (`llm/generator.py`) that yields our own typed events: `TextDelta`, `CitationRef`,
  `Completed(usage, stop_reason, stop_category, model)`, or raises `GenerationError`.
* The production implementation (`llm/claude.py`) wraps **`langchain-anthropic`
  `ChatAnthropic`** 1.7.13 (anthropic SDK 2.14.0) with `claude-sonnet-5-5`
  (configurable), `reasoning_effort="low"`, explicit `thinking={"type": "adaptive"}`,
  `max_tokens = LLM_MAX_OUTPUT_TOKENS` (2,048), SDK retries 2, timeout 120 s.
* Pipeline: admission → retrieval (M1 HNSW search, `RAG_TOP_K=8`) → context assembly
  (`RAG_CONTEXT_CHAR_BUDGET`, grouped per document, ordered by position) → generation →
  citation validation → persistence.

## Capability spike (before implementation)
`ChatAnthropic` was driven against Claude's documented SSE wire format through an `httpx2`
mock transport (no API key was available). All eight required capabilities held:
`search_result` passthrough · streamed `citations_delta` merged into text blocks · effort
payload · `cache_control` in the payload and cumulative cache usage in `message_delta` ·
`stop_reason`/refusal `stop_details` · beta headers · typed `anthropic.*Error` · cancellation
closes the HTTP response. Findings that shaped the code: the SDK 2.x uses `httpx2` (test
transports must too) and sends the key as `Authorization: Bearer`; LangChain adds
`thinking.display: summarized` when effort is set (we set `thinking` explicitly);
`usage_metadata.input_tokens` *includes* cache reads/writes (we split it back out);
LangChain sends `anthropic-version: 2023-06-01`; a mid-stream error arrives as
`APIStatusError` with HTTP 200, so errors are mapped by their body type too.

## Consequences
* + Approved stack kept; LangChain is ready for LangGraph in M3.
* + The fake generator makes every routine test free and deterministic; contract tests
  run the real adapter against wire-format fixtures offline.
* − One more layer: LangChain can lag API features (we found and worked around two
  behaviours above). The fallback is the official SDK behind the same Protocol.
* Rejected: calling the SDK directly now (would defer the approved LangChain integration
  without a capability reason), LangChain chains/retrievers (no value for one call).
