# ADR 0021: Chat UI, safe Markdown and the error model

* Status: Accepted (M2, 2026-11-27)

## Decision
* `/chat` (conversation list, model and budget panel, privacy disclosure) and
  `/chat/[id]` (thread, composer with Stop, streamed answer, citation chips, expandable
  sources with page and passage, usage/cost footer). Text deltas are batched per
  animation frame; a polite live region announces start/finish rather than every token.
* **Markdown** via `react-markdown` 10 + `remark-gfm` 4 (headings, lists, tables, code):
  renders React elements (no `dangerouslySetInnerHTML`); **raw HTML is never rendered**
  (turned into escaped text; shown rather than dropped because dropping a CommonMark HTML
  block also removed the ordinary text on the same lines, found in browser testing);
  images are not rendered (no fetches of model-chosen URLs); default URL sanitiser;
  external links `noopener noreferrer nofollow`; only validated `#cite-n` links become chips.
* **Error model:** stable codes (`llm_not_configured`, `llm_busy`, `llm_budget_exceeded`,
  `conversation_busy`, `chat_not_ready`, `llm_rate_limited` + `Retry-After`,
  `llm_unavailable`, `llm_timeout`, `llm_request_failed`, `internal_error`). Anthropic error
  bodies are never forwarded. Refusals are stored as `refused` with `stop_details.category`
  and shown as such; **no automatic fallback** (D9). `max_tokens` → `truncated`.
* The API key is a server-only `SecretStr`: absent from logs, responses, OpenAPI and the
  client bundle (tested).
