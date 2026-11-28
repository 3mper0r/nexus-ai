# M2 Implementation Plan: Grounded Answers with Claude (RAG)

* Date: 2026-11-27
* Status: **Approved 2026-11-27 with amendments** (below). Implementation in progress.

## Approved decisions (supersede §0 where they differ)

| # | Decision as approved |
|---|---|
| D1 | **`claude-sonnet-5-5`** (verified in Anthropic's models overview on 2026-11-27: 1M context, 128K output, default effort `high`; $2 / $10 per MTok, cache write 5 min $2.50, cache read $0.10). Configurable (`LLM_MODEL`); we run it at `LLM_EFFORT=low` |
| D2 | `ChatAnthropic` behind our `AnswerGenerator` protocol, after a capability spike; official SDK if a critical capability is missing |
| D3 | Native `search_result` citations with exact page references |
| D4 | Persistent multi-turn conversations in PostgreSQL |
| D5 | No query-rewriting call |
| D6 | SSE through a Next.js Route Handler proxy, cancellation end to end |
| D7 | **Amended:** Markdown via a lightweight renderer with **raw HTML disabled**; headings, lists, tables, code blocks |
| D8 | Approved for non-sensitive demo documents; **the UI and README disclose** that questions, retrieved passages and relevant history are sent to Anthropic |
| D9 | **Amended: no automatic refusal fallback.** Refusals are stored and shown transparently (`stop_details.category`) |
| D10 | Opt-in live tests (`-m llm`), never in routine CI |

**Additional requirements:**
* Daily budget default **$1** (`LLM_DAILY_BUDGET_USD`) and output limit **2,048 tokens**
  (`LLM_MAX_OUTPUT_TOKENS`), both configurable. At most **4** concurrent generations.
* Token and cost tracking. **Budget enforcement that concurrent requests cannot bypass**:
  each generation reserves its worst-case cost under a PostgreSQL advisory lock before it
  starts, and the reservation is replaced by the actual cost when it ends.
* Retrieved documents are untrusted data, not instructions.
* No fabricated or invalid source references: citations come only from the API's structured
  citation objects and are validated against what was sent.
* Insufficient evidence → explicit "not found", never an invented answer. No retrieval → no API call.
* The API key never reaches the browser. No DB transaction is open during generation.
* Cancellation, partial responses and recovery of interrupted conversations are verified.
* Prompt caching compatibility and actual usage reporting are verified.

**Capability spike (2026-11-27, offline):** `ChatAnthropic` (langchain-anthropic 1.7.13,
anthropic 2.14.0) was driven against Claude's documented SSE wire format through an `httpx2`
mock transport. All eight capabilities hold:
1. `search_result` passthrough;
2. streamed citations merged;
3. effort payload;
4. `cache_control` in the payload and cache usage in `message_delta`;
5. `stop_reason` and refusal `stop_details`;
6. beta headers;
7. typed `anthropic.RateLimitError`;
8. cancellation closes the upstream stream.

Notable findings:
* `anthropic` 2.x uses `httpx2`.
* `ChatAnthropic` injects `thinking: {type: adaptive, display: summarized}` when effort is set,
  so we set `thinking` explicitly.
* `usage_metadata.input_tokens` includes cache reads and writes.
* LangChain sends `anthropic-version: 2023-06-01`.

No Anthropic credentials were available on the development machine, so the **live** half of the
spike (real cache hits, real citations, the golden set) runs when a key is provided (D10).
* Builds on: M1 (ingestion, multilingual retrieval, page citations), commit `a839ad4`.
* Roadmap slot: day 3 (see [M1 plan §0](m1-plan.md)).

API facts in this plan were checked on 2026-11-27 against the current Claude API
documentation and the installed/published packages. Exact versions are locked at
implementation time.

---

## 0. Decisions requiring your approval

| # | Decision | Recommendation | Alternatives |
|---|---|---|---|
| **D1** | Answer model | **`claude-opus-5-5`** at **`effort: "low"`**. Thinking cannot be disabled on Opus 5.5, so effort is the cost/latency control. $4 / $20 per MTok, cache reads $0.20 | `claude-sonnet-5-5` ($2 / $10) for lower cost; `claude-haiku-5-5` ($0.10 / $0.50) for the lowest cost. **This is a cost-vs-quality call for you, not me.** The model is one config value either way |
| **D2** | How LangChain is used | **`langchain-anthropic` `ChatAnthropic`** is the model client, behind our own small `AnswerGenerator` protocol. Step 1 is a **verification spike** against the real API. If any required capability (below) fails, the generation call switches to the official `anthropic` SDK, and LangChain enters with LangGraph in M3 | Use the `anthropic` SDK directly now and defer LangChain to M3. Fewer layers, first-class access to every API feature |
| **D3** | Citation mechanism | **Native `search_result` content blocks** with `citations: {enabled: true}`: one block per retrieved document, one text block per chunk. Claude returns `search_result_location` citations, which we map to exact chunks and pages | Prompt-based `[1]` markers parsed from text. Fragile, unverifiable, and loses the cited text |
| **D4** | Conversations | **Persisted multi-turn conversations** in PostgreSQL (needed for follow-ups, history and cost accounting). Single local user, no auth | Stateless single-question Q&A (simpler, but no follow-ups) |
| **D5** | Follow-up retrieval | **No extra LLM call:** the retrieval query is the current message plus the previous user message, and the history sent to Claude is plain Q&A text only (no old passages) | A query-rewriting call ("condense question") on a cheap model. Better for terse follow-ups, but adds a second model, latency and cost. Revisit with evidence in M4 |
| **D6** | Streaming path | **SSE from FastAPI** (`sse-starlette`) → **Next.js Route Handler proxy** → browser `fetch` stream. The API stays server-side only (ADR 0007), there is no CORS, and abort propagates end to end | Browser calls the API directly with CORS (exposes the API URL; contradicts ADR 0007) |
| **D7** | Answer formatting | **Plain text** answers (paragraphs and simple lists), rendered by a ~40-line component; **no markdown library** | Add `react-markdown` + sanitisation for richer formatting |
| **D8** | Data leaving the machine | **Acknowledge:** retrieved passages, the question and recent history are sent to the Anthropic API. M1 kept everything local. Embeddings and search stay local | Not adopting M2 |
| **D9** | Server-side refusal fallback | **Enable** `fallbacks: "default"` (beta `server-side-fallback-2026-07-01`): if a safety classifier declines on Opus 5.5, the API retries on a fallback model. We record which model answered. *Enabled by default per Anthropic guidance; say no to drop it* | No fallback: show the refusal to the user |
| **D10** | Live API testing budget | An opt-in `llm` test marker that calls the real API, plus one browser E2E. Estimated **< $1 total** per full run at Opus 5.5 low effort. Never in CI | No live tests (wire-format fixtures only) |

**Capabilities the D2 spike must prove with `ChatAnthropic`** (each one a short script, results
recorded in the M2 report):

1. `search_result` blocks in a user message reach the API unchanged.
   *Source check: unknown dict blocks pass through.*
2. Streamed citations arrive intact on text blocks.
   *Source check: `citations_delta` is merged.*
3. `output_config.effort` and adaptive thinking work.
   *Source check: supported; `thinking` is omitted on Opus 5.5.*
4. `cache_control` on system and history blocks, with `cache_read_input_tokens` visible in usage metadata.
5. `stop_reason` (`end_turn`, `max_tokens`, `refusal` + `stop_details`) is accessible.
6. The beta header and `fallbacks` parameter can be sent (`betas=[…]`, `model_kwargs`).
7. Anthropic's typed exceptions (`RateLimitError`, `APIStatusError`, …) surface, so we can map them.
8. Cancelling the async stream closes the HTTP connection, so we stop paying.

If any of 1, 2, 4, 5 or 8 fails, I'll switch the generation call to the `anthropic` SDK and report
the reason before continuing.

---

## 1. Objectives and scope

**Objective:** a user asks a question in English or German in `/chat`. NEXUS retrieves relevant
passages from the M1 index, Claude answers **only from those passages**, the answer streams
token by token, and every claim links to its **source document and page**, with the cited text
shown. Follow-up questions work. Costs are measured, capped and visible.

### In scope
* RAG pipeline: retrieval → context assembly → grounded generation → citation resolution.
* Claude integration through `ChatAnthropic` (D2), adaptive thinking at low effort, refusal handling.
* SSE streaming with a defined event protocol, client abort, and server-side cancellation.
* Conversations and messages persisted with citations, usage, cost and outcome.
* Cost controls: context and history budgets, output cap, prompt caching, concurrency limit,
  daily budget guard, and a usage endpoint.
* Error handling: missing key, rate limits, overload, timeouts, refusals, truncation, disconnects.
* `/chat` UI: conversation list, streaming thread, citations panel, stop button, every state.
* Tests: unit, PostgreSQL integration (fake generator), offline wire-format contract tests,
  opt-in live tests (D10).

### Out of scope
Agents, tool use, LangGraph, MCP (M3) · Langfuse and RAGAS (M4; M2 adds only a small live golden
set) · authentication and multi-user (M5) · query rewriting (D5) · hybrid search and re-ranking ·
markdown rendering (D7) · answer feedback buttons · file or image attachments in chat.

---

## 2. Architecture

```text
Browser /chat ──POST (fetch stream)──► Next.js Route Handler /api/chat/[id]/messages
                                          │  server-only; forwards abort
                                          ▼
FastAPI POST /api/v1/conversations/{id}/messages   (text/event-stream)
  1. admission: LLM configured? daily budget left? stream slot free? (else 503/429 before streaming)
  2. short txn: load conversation + last N turns; insert user message; commit; release connection
  3. retrieval (M1): embed "query: …" (thread) → HNSW top-k (short txn) → group by document
  4. context assembly: token/char budget, order by document then page → search_result blocks
  5. generation: ChatAnthropic.astream(system + history + [search_results…, question])
        ├─ event: meta      {message_id, sources[]}            (before the first token)
        ├─ event: delta     {text}                              (many)
        ├─ event: citation  {segment, source_ref, page, cited_text}
        └─ event: done      {stop_reason, usage, cost_usd, model, ttft_ms, total_ms}
           or event: error  {code, message, retryable}
  6. short txn: insert assistant message (text, citations, usage, cost, status); commit
  on client disconnect: cancel the Claude stream (stop billing); persist status=cancelled
```

### Key decisions (become ADRs 0016–0021)

**ADR 0016: RAG pipeline and the `AnswerGenerator` boundary**
* `rag/` domain package (no HTTP): `retrieval.py` (reuses M1 `search_chunks`), `context.py`
  (assembly), `prompt.py` (system prompt and message building), `citations.py` (mapping),
  `generator.py` (protocol + Claude implementation), `service.py` (orchestration and persistence).
* `AnswerGenerator.stream(request) -> AsyncIterator[GenerationEvent]` emits our own typed events
  (`TextDelta`, `Citation`, `Completed(usage, stop_reason, model)`, `Failed(code)`). One
  implementation wraps `ChatAnthropic`; tests use a scripted fake. This is the one abstraction
  M2 justifies, as `Embedder` was in M1: it keeps the paid API out of every test.

**ADR 0017: Grounding and citations via `search_result` blocks**
* Retrieved chunks are grouped **per document**. Each document becomes one `search_result`:
  * `source` = `nexus://documents/{id}`;
  * `title` = filename;
  * `content` = one text block per chunk, prefixed with `[p. N]`.

  The order is document, then page.
* Each citation's (`search_result_index`, `start_block_index`…`end_block_index`) resolves to
  exact chunk IDs, and therefore pages (M1 chunks are single-page). `cited_text` is kept.
* The system prompt requires answers only from the provided results, an explicit "not found in
  your documents" when they are insufficient, and the user's language (EN/DE).
* **Prompt injection:** document text is untrusted data. The system prompt says so, and M2 has
  no tools, so the worst case is a misleading answer. Citations make every claim checkable.
* **No retrieval, no call:** zero matching chunks (or no documents) returns a fixed message
  without calling Claude, at zero cost.

**ADR 0018: Prompt layout for caching and cost**
* Order: `system` (stable instructions, ≥ 512 tokens so it is cacheable on Opus/Sonnet/Haiku 5.5)
  → history (plain Q&A text from the last `RAG_HISTORY_TURNS`, no old passages) → current user
  message (`search_result` blocks + question).
* **Explicit cache breakpoints:** one on the system prompt and one on the last history message.
  **Not** top-level automatic caching: the request ends in unique retrieved context, so an
  automatic breakpoint would land after it and pay the write premium on every request with no
  reads. The system prompt is byte-stable: no timestamps or IDs, enforced by a unit test.
* Verification: a live test asserts `cache_read_input_tokens > 0` on a second turn; the
  `done` event and stored usage include cache fields.

**ADR 0019: Streaming protocol and cancellation**
* SSE via `sse-starlette` (keep-alive pings, disconnect detection) with named events
  (`meta`, `delta`, `citation`, `done`, `error`); every event carries `message_id`. The schema is
  documented and mirrored in TypeScript types plus runtime guards.
* Admission errors (no key, budget, concurrency) are ordinary JSON errors **before** the stream
  starts (503/429). Errors after the first byte are an `error` event, and the partial answer is
  stored with status `failed`.
* Disconnect → the generator task is cancelled → `ChatAnthropic`'s stream closes the upstream
  connection (spike item 8) → status `cancelled`, usage recorded if known.
* **No DB connection held while streaming:** M0's ADR 0003 guidance. Use short transactions
  before and after; never `DbSessionDep` inside the stream.
* Next.js: a Route Handler proxies the `ReadableStream` with `cache: "no-store"` and forwards
  `request.signal`. The browser uses `fetch` + a small SSE parser (POST bodies rule out
  `EventSource`).

**ADR 0020: Token and cost management**

| Lever | Setting (default) |
|---|---|
| Effort | `LLM_EFFORT=low` (Opus 5.5 default is `medium`; thinking can't be disabled) |
| Output cap | `LLM_MAX_OUTPUT_TOKENS=8000`. A cost cap for short grounded answers; thinking counts against it. `max_tokens` → answer marked truncated |
| Context budget | `RAG_TOP_K=8`, `RAG_CONTEXT_CHAR_BUDGET=24000` (≈ 6–8k tokens) |
| History | `RAG_HISTORY_TURNS=4` (Q&A text only) |
| Caching | system prompt + history prefix (ADR 0018); reads at 0.05× on Opus 5.5 |
| Concurrency | `LLM_MAX_CONCURRENT_STREAMS=4` (429 `llm_busy` + `Retry-After`) |
| Budget guard | `LLM_DAILY_BUDGET_USD=5.00`: sum of stored message costs today (UTC); when exceeded → 429 `llm_budget_exceeded` before calling Claude |
| Accounting | per message: model, `input/output/cache_creation/cache_read` tokens, `cost_usd`, TTFT, total time; `GET /api/v1/usage` |

* The pricing table lives in code (`llm/pricing.py`), keyed by model ID, with the date it was
  checked. Unknown model → cost `null` plus a warning (never a wrong number).
* **Estimate:** ~7k input + ~1–2k output (incl. thinking) per answer → about **$0.03–0.07
  per answer** on Opus 5.5 at low effort; about half on Sonnet 5.5. Measured in the report.

**ADR 0021: Error model**

| Condition | Response |
|---|---|
| `ANTHROPIC_API_KEY` missing | 503 `llm_not_configured`; search keeps working; `/chat` explains how to configure |
| Authentication or permission error | 503 `llm_not_configured`; logged without the key |
| Rate limit (429) or overload (529) / 5xx | SDK retries (`max_retries=2`) before streaming; then 503 `llm_unavailable` + `Retry-After`, or an `error` event if mid-stream |
| Timeout / connection error | `error` event `llm_timeout` / `llm_unavailable` (connect timeout 10 s, first-event timeout 60 s, total 180 s) |
| `stop_reason: refusal` | after fallback (D9), stored as `refused` with `stop_details.category`; UI explains |
| `stop_reason: max_tokens` | answer kept, marked `truncated` |
| 400 (our bug) | `error` event `internal_error`; logged with request ID; never exposes the API message |

Secrets: `ANTHROPIC_API_KEY` is a `SecretStr`, server-only, never logged or returned. Logs carry
IDs, token counts and timings, **not** question or answer text.

---

## 3. Dependencies (verified on PyPI 2026-11-27)

| Package | Latest | Why |
|---|---|---|
| `langchain-anthropic` | 1.7.13 | Approved stack; Claude chat model with `search_result`/citations passthrough and effort support (D2). Pulls in `langchain-core` 1.8.x and `anthropic` |
| `anthropic` | 2.14.0 | Declared directly: typed exceptions for the error model, and the fallback path if the spike fails |
| `sse-starlette` | 3.7.1 | SSE responses with keep-alive pings and disconnect detection |

No new frontend dependencies (D7). No `langchain` meta-package, `langgraph`, vector-store
integrations or LangSmith.

---

## 4. Repository changes

```text
apps/api/
├── migrations/versions/0003_conversations_and_messages.py
├── src/nexus_api/
│   ├── core/config.py           + ANTHROPIC_API_KEY (SecretStr, optional), LLM_*, RAG_* (validated)
│   ├── db/models.py             + Conversation, Message (citations JSONB snapshot, usage, cost, status)
│   ├── llm/
│   │   ├── generator.py         AnswerGenerator protocol, GenerationEvent types
│   │   ├── claude.py            ChatAnthropic implementation (effort, caching, fallbacks, errors)
│   │   └── pricing.py           price table (dated), cost computation
│   ├── rag/
│   │   ├── context.py           grouping, budgets, search_result block building
│   │   ├── prompt.py            stable system prompt, history window, cache breakpoints
│   │   ├── citations.py         search_result_location → chunk/page/cited_text
│   │   └── service.py           orchestration, admission, budget guard, persistence
│   └── api/v1/
│       ├── conversations.py     CRUD + POST …/messages (SSE)
│       └── usage.py             GET /usage, GET /llm/status
└── tests/  unit/ (context, citations, prompt stability, pricing, SSE encoding, errors)
            integration/ (SSE end to end with fake generator, persistence, cancellation, budget)
            contract/ (Claude adapter against recorded wire-format SSE via httpx.MockTransport)
            llm/ (opt-in live tests, marker "llm")
apps/web/src/
├── app/chat/page.tsx, app/chat/[id]/page.tsx       conversation list + thread
├── app/api/chat/[id]/messages/route.ts             streaming proxy (server-only)
├── components/chat/{composer,message,answer-text,citation-marker,sources-panel,conversation-list}.tsx
└── lib/api/{conversations.ts, sse.ts (parser + guards), types.ts (+)}
docs/decisions/0016–0021 · docs/milestones/m2-report.md · README · architecture overview
.env.example (+ANTHROPIC_API_KEY=, LLM_*, RAG_*) · Makefile (+test-llm) · CI: unchanged (no key in CI)
```

**Schema (`0003`):**
* `conversations`: `id`, `title` (first question, truncated), `created_at`, `updated_at`.
* `messages`: `id`, `conversation_id` (FK cascade), `role`, `content`, `status` (CHECK:
  `complete`/`truncated`/`refused`/`failed`/`cancelled`), `citations` JSONB, `retrieved_chunk_ids`
  JSONB, `model`, token fields, `cost_usd` NUMERIC, `stop_reason`, `error_code`, `ttft_ms`,
  `total_ms`, `created_at`.
* Indexed on `(conversation_id, created_at)` and `created_at` (budget sums).

Citations are stored as a **snapshot** (filename, page, cited text, document ID), so history stays
readable after a document is deleted or re-ingested.

---

## 5. Implementation steps

1. **D2 spike** (real API, a few cents): run the 8 capability checks above with `ChatAnthropic`
   on the approved model; record the results; confirm the client choice.
2. **Config and errors:** settings with validation, an optional key, and the new error codes.
3. **Schema:** models plus migration `0003` (reversible, `alembic check` clean).
4. **Context assembly and citations:** pure functions with full unit coverage. Covers the
   budget, grouping, ordering and index mapping, including multi-block citations.
5. **Prompt:** stable system prompt (EN/DE behaviour, grounding, "not found", injection
   guard), history window, cache breakpoints. A unit test asserts byte stability.
6. **Generator:** protocol, scripted fake, `ChatAnthropic` implementation, and the error
   mapping and stop-reason handling.
7. **Contract fixtures:** record 4 real SSE streams once:
   * a cited answer;
   * a "not found" answer;
   * a truncated answer;
   * a German answer.

   Store the redacted raw wire format, then test the adapter offline via `httpx.MockTransport`.
8. **Service:**
   * admission: key, budget, concurrency;
   * short transactions around the stream;
   * cancellation;
   * persistence of every outcome.
9. **HTTP:** conversation endpoints and the SSE endpoint (`sse-starlette`), usage and LLM status.
10. **Frontend:**
    * Route Handler proxy and SSE parser + guards;
    * `/chat` list and thread, composer with stop, streaming answer with citation markers;
    * sources panel (filename, page, cited text, link to `/search?doc=`);
    * states: empty, no documents, not configured, budget exceeded, busy, refused, truncated,
      failed, cancelled;
    * cost and usage footer.
11. **Live golden set (D10):** about 10 EN/DE questions over the M1 demo documents (incl. 2
    unanswerable). Check the cited page is correct, "not found" when appropriate, the answer
    language, and caching. Record cost, TTFT and total time.
12. **Verification:**
    * all suites;
    * headless-Chrome E2E: ask → stream → citation click → follow-up → stop mid-stream;
    * a mobile layout check;
    * kill the API mid-stream.
13. **Docs:** ADRs 0016–0021, overview, README (setup with API key, cost notes), M2 report,
    concise learning notes.
14. **Engineering review**, then commit on approval.

---

## 6. Testing strategy

| Layer | Runs in CI | API key | What it proves |
|---|---|---|---|
| Unit | yes | no | context budgets and ordering, citation index mapping, prompt byte stability, history window, pricing math, SSE encoding, error mapping, stop-reason handling |
| Integration (PostgreSQL, fake generator) | yes | no | SSE end to end; user and assistant messages persisted with citations resolved to real chunk IDs and pages; no-document short-circuit; budget guard (429); concurrency limit; disconnect → upstream cancelled + `cancelled` status; no DB connection held while streaming (`pg_stat_activity`, as in M1); every failure path stored |
| Contract (recorded wire format) | yes | no | our `ChatAnthropic` adapter parses real Claude SSE: citations, usage incl. cache fields, `max_tokens`, mid-stream error |
| Live (`-m llm`, opt-in) | **no** | yes | golden set (correct page cited, grounded "not found", EN/DE), cache read on the second turn, refusal/fallback path observable, measured cost and latency |
| Frontend | yes | no | ESLint, `tsc`, build without backend; SSE parser guards; headless E2E with a real key during verification |

Routine CI never needs an API key or the embedding model. Live tests **fail, not skip**, when
selected without a key (the M0/M1 policy).

---

## 7. Security and performance

**Security**
* **API key:** server-only `SecretStr`; never logged, returned or sent to the browser
  (bundle scan as in M0/M1).
* **Prompt injection:** documents are untrusted data; no tools in M2; citations expose
  provenance; output is rendered as text (React escaping, no `dangerouslySetInnerHTML`).
* **Privacy (D8):** passages and questions go to Anthropic; question and answer text are not
  logged locally. Stored conversations live in the local database only.
* **Abuse and cost:** no auth (accepted for local MVP). The daily budget, concurrency limit,
  output cap and loopback binding bound the damage.
* **Error envelopes:** never forward Anthropic error bodies.

**Performance**
* **Targets** (to be measured; thinking adds latency even at low effort):
  * retrieval < 50 ms;
  * time to first text token p50 < 4 s;
  * typical answer total < 15 s.
* The proxy streams without buffering. Text deltas are batched to the DOM every animation
  frame. `aria-live` announcements are throttled.
* Long streams hold no DB connection; a stream slot is released on completion or cancellation.

---

## 8. Definition of done

- [ ] D1–D10 decided; the D2 spike's 8 checks recorded
- [ ] Questions in EN/DE answered from retrieved passages, with **page-accurate, clickable citations** showing cited text
- [ ] Follow-up questions work using the conversation history; conversations persist and can be deleted
- [ ] Streaming end to end; the stop button cancels upstream generation (verified: no further tokens billed)
- [ ] "Not found in your documents" for unanswerable questions; the no-retrieval short-circuit makes no API call
- [ ] Every outcome stored and shown: complete, truncated, refused, failed, cancelled
- [ ] Cost controls active (effort, caps, budgets, caching, concurrency, daily budget); cache reads verified on turn 2; usage and cost per message and per day visible
- [ ] Error model implemented and tested; the API key never appears in logs, responses or the client bundle
- [ ] Search and M1/M0 functionality unchanged; all existing tests pass
- [ ] Unit, integration and contract tests in CI with no key and no model; live golden set passes (opt-in)
- [ ] `/chat` polished in the dark/cyan identity; accessible (keyboard, focus, `aria-live`); 390 px layouts fit
- [ ] Measured: cost per answer, TTFT, total latency, cache hit rate
- [ ] ADRs 0016–0021, README, overview, M2 report, learning notes
- [ ] Engineering review; commit only on approval

---

## 9. Learning focus for M2 (concise guide after implementation)

1. RAG anatomy: retrieval, context assembly, grounding, citations, evaluation hooks.
2. The Claude Messages API: content blocks, `search_result`, stop reasons, effort and
   adaptive thinking, refusals and fallbacks.
3. LangChain chat models: messages, content blocks, `astream`, what the abstraction adds and hides.
4. Streaming end to end: SSE format, async generators, backpressure, cancellation in Python
   and in the browser (`AbortController`, `ReadableStream`).
5. Token economics: input/output/thinking tokens, prompt caching mechanics and break-even,
   cost attribution.
6. Prompt injection and grounding failures in RAG.
7. Testing LLM features: fakes, recorded wire-format contracts, live golden sets.

Fifteen interview questions with senior-level answers will be written against the
implemented code, as in M0 and M1.
