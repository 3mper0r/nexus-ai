# M2 Learning Guide: RAG, Claude, streaming and cost control for a TypeScript engineer

Concise by design. Each topic says what was built, why, how it works, the closest
TypeScript/Node equivalent, and the production mistakes it avoids, with pointers to code.

---

## 1. Anatomy of a RAG request

**Code:** `rag/service.py` (`prepare`, `_generate`)

Retrieval (local embeddings + HNSW, M1), then context assembly (budget, per-document
grouping), then generation (Claude), then citation validation, then persistence. The model
never searches: we decide what it sees, which is why grounding and citations are
controllable. "No passages" is answered without calling the model at all.

**Mistakes avoided:** stuffing everything into the prompt (cost, dilution); letting the model
"know" things the documents don't say; a "not found" path that costs a full model call.

## 2. The Claude Messages API, as used here

**Code:** `llm/claude.py`

* **Content blocks:** a message is a list of typed blocks. We send `search_result` blocks
  (`source`, `title`, `content: [text…]`, `citations.enabled`) plus a `text` question.
* **Citations** come back as `search_result_location` objects on text blocks:
  `search_result_index` + `start_block_index`/`end_block_index` (exclusive) into *our*
  content arrays. One block per chunk means a citation names exact chunks and pages.
* **Stop reasons:** `end_turn`, `max_tokens` (truncated), `refusal` (+ `stop_details.category`).
* **Effort and adaptive thinking:** Sonnet 5.5 decides how much to think; `effort` (`low` here)
  trades depth for tokens. Thinking tokens are billed as output and count against `max_tokens`.
* **API version drift:** SDK 2.x sends the key as `Authorization: Bearer` and version
  `2026-10-21` (which rejects `temperature`/`top_p`/`top_k`). LangChain overrides the version
  header to `2023-06-01`. Our requests never send sampling parameters.

```ts
// TS SDK equivalent of the current turn we build
{ role: "user", content: [
    { type: "search_result", source: "nexus://documents/…", title: "handbook.pdf",
      content: [{ type: "text", text: "passage 1" }], citations: { enabled: true } },
    { type: "text", text: "How long is data kept?" } ] }
```

## 3. LangChain chat models: what the abstraction adds and hides

**Code:** `llm/claude.py`, spike in `m2-plan.md`

`ChatAnthropic.astream(messages)` yields `AIMessageChunk`s. Content blocks pass through as
dicts (with an `index`), and usage arrives once in `usage_metadata`. What it hides, as we found:
* it adds `thinking.display: summarized` when effort is set;
* its `input_tokens` *includes* cached tokens;
* it pins the API version header.

The cure is a small Protocol of our own (`AnswerGenerator`): domain code and tests don't
depend on these details, and swapping in the raw SDK is one class.

## 4. Async generators, cancellation and `aclosing`

**Code:** `rag/service.py` (`stream`, `_generate`), `llm/claude.py` (`stream`)

An async generator suspended at `yield` is only cleaned up when it is **closed**
(`aclose()` → `GeneratorExit`) or garbage-collected. `async for` does *not* close the
iterator when the loop body raises. Two shutdown paths exist:
* **Task cancellation** (client disconnect): `CancelledError` is raised at the innermost
  `await` and unwinds every frame.
* **`aclose()` from outside:** only the outermost generator sees `GeneratorExit`. Inner
  ones stay suspended, and their `finally` blocks never run. `contextlib.aclosing(inner)`
  propagates the close.

Our test for the second path failed with `aclosing` removed: the message stayed `streaming`.

```ts
// JS: async iterators are closed by `return()`; `for await` calls it on break/throw,
// Python's `async for` does not, hence aclosing.
for await (const chunk of stream) { if (done) break; } // calls stream.return()
```

Other details: the final DB write is wrapped in `asyncio.shield` so a second cancellation
cannot abort it; `except (CancelledError, GeneratorExit)` must re-raise.

## 5. Server-Sent Events end to end

**Code:** `api/v1/conversations.py`, `app/api/conversations/[id]/messages/route.ts`,
`lib/sse.ts`, `components/chat/chat-thread.tsx`

* **Format:** lines of `event:` / `data:`, with a blank line between events. Comment lines
  (`: ping`) keep proxies from timing out.
* **POST bodies:** `EventSource` only does GET, so the browser uses `fetch` +
  `ReadableStream` + a small parser that copes with events split across chunks and
  multi-byte characters (`TextDecoder` with `stream: true`).
* **Admission before the first byte:** once the response has started, the HTTP status is
  fixed at 200, so every "no" (budget, busy, no key) must be decided before it. Errors
  after that are `error` events.
* **Abort chain:** browser `AbortController` → Route Handler `request.signal` passed to the
  upstream `fetch` → FastAPI sees the disconnect → task cancelled → Anthropic stream closed.
* **Rendering:** batch deltas per animation frame. Announce start and finish in an
  `aria-live` region, not every token.

## 6. Transactions around slow work, and race-free budgets

**Code:** `rag/repository.py` (`admit_generation`, `finalize_generation`)

* **No transaction during generation:** reserve → commit → stream with no connection →
  record in a new short transaction. Verified with `pg_stat_activity`.
* **Check-then-act races:** "sum today's spend, then insert" is racy. Two requests both read
  $0.90, both insert. `pg_advisory_xact_lock(key)` serialises the critical section across
  connections *and* processes, and is released automatically at commit or rollback.
* **Reservations:** the worst-case cost is recorded before the call, so in-flight and crashed
  generations count. It is replaced by the actual cost afterwards, and kept when the usage is
  unknown.
* **Ledger:** spend lives in its own table (`llm_usage`) without a foreign key. If it were
  summed from `messages`, deleting a conversation would refund its spend, a hole the
  M2 audit found.

```ts
// Node analogue: a mutex (async-mutex) only protects one process; the DB lock protects all.
```

## 7. Token economics and prompt caching

**Code:** `llm/pricing.py`, `llm/claude.py` (`build_messages`), `rag/prompt.py`

* **Billed categories:** uncached input, cache writes (1.25× input), cache reads (0.05× on
  Sonnet 5.5), and output (including thinking).
* **Caching is a prefix match.** Stable content goes first, with an explicit breakpoint at
  the end of the stable part. Our request ends in unique retrieved context, so automatic
  caching would only ever *write*.
* **Silent invalidators:** a timestamp or ID in the system prompt makes every request a
  miss. A unit test pins the prompt's hash.
* **Measure, don't assume:** usage fields are the only ground truth. The UI shows the cached
  share per answer.

## 8. Grounding, citations and prompt injection

**Code:** `rag/citations.py`, `rag/prompt.py`

* Trust structure, not prose: citations are API objects validated against what we sent,
  and the displayed text is ours. A model that writes "[3]" in prose creates nothing.
* Untrusted retrieved text belongs in data blocks, never in the system prompt. Tell the
  model it is data. Keep tools out until you need them (no tools = small blast radius).
* "No citations" on a complete answer → flag it (`grounded: false`); don't hide it.

## 9. Testing LLM features

**Code:** `tests/fakes.py` (`ScriptedGenerator`), `tests/unit/test_claude_adapter.py`,
`tests/integration/test_chat.py`, `tests/llm/`

| Layer | Purpose |
|---|---|
| Scripted fake | deterministic behaviour of everything around the model (citations, failures, cancellation, budget) |
| Wire-format contract | the real adapter against the provider's documented stream format, offline (`httpx2.MockTransport`) |
| Live, opt-in | what only the real service can show (cache hits, real citations, injection resistance), never in CI, fails rather than skips without a key |
| Mutation checks | prove the important tests can fail |

Run SSE tests against a **real server** (uvicorn on an ephemeral port): in-process ASGI
transports buffer responses and cannot simulate a disconnect.

## 10. Safe Markdown in React

**Code:** `components/chat/answer-markdown.tsx`

`react-markdown` builds React elements, so there is no `innerHTML`. Its defaults sanitise
URLs. We also:
* turn raw HTML into text rather than dropping it, because dropping a CommonMark HTML block
  removes ordinary text on the same lines;
* disable images, so the browser never fetches a model-chosen URL;
* let only validated `#cite-n` links become citation chips.

## Bugs caught during M2 (and what they teach)

| Bug | Lesson |
|---|---|
| Mid-stream error as HTTP 200 `APIStatusError` | Classify errors by type, not only by status |
| Inner generators not closed on `aclose()` | `async for` does not close iterators; use `aclosing` |
| Tautological hash test | A pinned value must be a literal |
| `/llm/status` 500 without DB | Status endpoints should degrade, not fail |
| `usePathname` outside Suspense on a dynamic route | Request-time data needs a Suspense boundary under Cache Components |
| `skipHtml` dropped legit text | Know your parser's block rules before choosing "drop" over "escape" |
| Deleting conversations reset the budget (audit) | Money is an append-only ledger, never derived from deletable rows |
| `make test` would have used a real key from `.env` (audit) | Hermetic tests must *remove* secrets, not merely not need them |
| An SSE generator that never started leaked its slot (audit) | An unstarted generator runs no `finally`; clean up in the response's `__call__` |
| Log line after a shielded write never ran (audit) | anyio re-delivers cancellation at every `await`: log first |
