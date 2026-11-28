# ADR 0018: Prompt layout for prompt caching

* Status: Accepted (M2, 2026-11-27)

## Decision
* Prefix order: `system` → history (Q&A text of the last `RAG_HISTORY_TURNS`=4 completed
  turns, no old passages) → current user turn (`search_result` blocks + question).
* **Explicit breakpoints**: `cache_control` on the system block and on the last history
  message. No top-level automatic caching: the request always ends in unique retrieved
  context, so an automatic breakpoint would pay the cache-write premium every request with
  no reads.
* `SYSTEM_PROMPT` is a constant (~3,100 characters, above the 512-token minimum cacheable
  prefix of current models) with no dates, ids or settings; a unit test pins its SHA-256.
* Usage, including `cache_creation` and `cache_read` tokens, is stored per answer and shown
  in the UI.

## Consequences
* + Turn 2+ of a conversation, and every new conversation, read the system prompt from
  cache ($0.10/MTok instead of $2.00 on Sonnet 5.5); later turns also read the history.
* − Cache hits are verified offline only in the request shape; the live test
  (`-m llm`, `test_second_request_reads_the_cached_system_prompt`) asserts
  `cache_read > 0` against the real API and has not run yet (no key on the dev machine).
