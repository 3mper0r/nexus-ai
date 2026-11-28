# M2 Implementation Report: Grounded Answers with Claude

* Date: 2026-11-27 (implementation); 2026-11-28 (live verification and audit)
* Status: **Accepted 2026-11-28.** The audit was approved; the remaining limitations (§10) are accepted for the MVP.
* Plan: [m2-plan.md](m2-plan.md), approved with amendments (D1 Sonnet 5.5, D7 Markdown, D9 no fallback).
* Results:
  * **231 backend tests passing:** 160 unit, 59 integration, 12 embedding-model.
  * **6 of 6 live API tests passing** against `claude-sonnet-5-5`, plus a live end-to-end run
    through the browser proxy. Total live spend: about **$0.10 billed; at most $0.17**
    counting every cancelled request at its worst-case reservation. The cap was $0.50.
  * Audit (§8a): **9 findings fixed** (2 high, 2 medium, 4 low, 1 test-quality), each with a
    regression test or a live check. The two high-severity issues: deleting conversations reopened the budget, and
    `make test` would have used a real key from `.env`.
  * Ruff and strict mypy (79 files), ESLint, `tsc` and the production build pass.

## 1. Executive summary

`/chat` answers questions about the user's documents in English, German and other
languages:
* NEXUS retrieves passages locally (M1) and sends them to `claude-sonnet-5-5` as
  `search_result` blocks.
* The answer streams to the browser over SSE through a Next.js proxy.
* Every citation is validated against the passages that were sent and shown as a chip
  linking to the document, page and passage.
* Conversations, citations, token usage and cost are persisted.
* A daily budget ($1 default) is enforced with worst-case reservations under a PostgreSQL
  advisory lock, so concurrent requests cannot overspend it.
* Cancellation, partial answers and crash recovery are handled and tested.
* Routine CI never needs an API key or the embedding model.

## 2. Remote CI status for M1

`a839ad4` (M1) has **not been pushed**: GitHub's `main` is still `0016147`, so no remote run
exists to verify. I reproduced both CI jobs locally from a clean clone of `a839ad4` (locked
installs, Node 24.22.2, empty model cache):

| Job | Result |
|---|---|
| Backend | ruff ✓, format ✓, mypy ✓ (57 files), 117 unit ✓, `alembic upgrade head` ✓, 38 integration ✓ |
| Frontend | `npm ci` (0 vulnerabilities) ✓, ESLint ✓, `tsc` ✓, build ✓ |

The real runs (`ci.yml`, and `model.yml`, which the M1 changes trigger) need verifying after
the push.

## 3. Decisions as implemented

| # | Implementation |
|---|---|
| D1 | `claude-sonnet-5-5`, verified in Anthropic's models overview (1M context, 128K output, default effort `high`) and pricing page ($2 / $10; cache write $2.50, read $0.10). Configurable (`LLM_MODEL`), run at `LLM_EFFORT=low` |
| D2 | `ChatAnthropic` behind `AnswerGenerator`. The offline capability spike passed all 8 checks (ADR 0016); the SDK fallback was not needed |
| D3 | `search_result` blocks, one text block per single-page chunk; citations validated and mapped to exact pages (ADR 0017) |
| D4 | `conversations` and `messages` tables (migration `0003`); history replays only completed turns |
| D5 | No rewriting call; the retrieval query is the previous question plus the current one |
| D6 | SSE (`sse-starlette`) → Route Handler proxy (same-origin, JSON, abort-linked) → `fetch` stream |
| D7 | `react-markdown` + `remark-gfm`: headings, lists, tables, code. Raw HTML is never rendered (shown as text), no images, sanitised URLs |
| D8 | Disclosure on `/chat`, in the composer, in the README and in `.env.example` |
| D9 | No fallback. Refusals stored as `refused` with `stop_details.category` and shown as such |
| D10 | `-m llm` (6 tests) and `make test-llm`; excluded from CI and every default make target |

**Additional requirements:**
* Budget $1, output 2,048 tokens and concurrency 4 are the defaults, all configurable.
* Token and cost tracking per answer and per day.
* Race-free budget, tested and mutation-verified.
* Retrieved text is untrusted data, stated in the system prompt and live-tested for injection.
* Invalid citations are dropped, and shown passage text is always our own.
* No passages → no API call; otherwise an explicit "not found" and a `grounded` flag.
* The key never reaches the browser (tested, bundle scan).
* No transaction is held during generation (tested via `pg_stat_activity`).
* Cancellation, partial answers and interrupted recovery are tested.
* Caching layout and usage reporting are contract-tested and **live-verified** (§7a).

## 4. Capability spike (summary; details in ADR 0016)

Offline, against Claude's documented SSE wire format through an `httpx2` mock transport:
1. `search_result` passthrough ✓
2. streamed citations ✓
3. effort and thinking payload ✓
4. `cache_control` and cumulative cache usage ✓
5. `stop_reason` / refusal `stop_details` ✓
6. beta headers ✓
7. typed errors ✓
8. cancellation closes the HTTP response ✓

Findings handled in code:
* `anthropic` 2.x uses `httpx2` and a bearer token.
* LangChain injects `thinking.display: summarized`.
* `input_tokens` includes cached tokens.
* Errors inside a started stream are `APIStatusError` with HTTP 200, mapped by their error type.

## 5. Repository changes

**Backend, new:**
* `llm/{generator,claude,pricing}.py`
* `rag/{context,citations,prompt,repository,service,errors}.py`
* `api/v1/conversations.py`
* migrations `0003_conversations_and_messages.py` and `0004_llm_usage_ledger.py` (audit)
* tests:
  * `unit/{test_rag_units,test_claude_adapter,test_chat_api}.py`
  * `integration/test_chat.py`
  * `llm/test_live_claude.py`
  * `e2e/demo_app.py`

**Backend, modified:**
* `core/config.py`: LLM/RAG settings, optional `SecretStr` key, price-entry validation.
* `db/models.py`: `Conversation`, `Message`, statuses, `LlmUsage` (audit).
* `main.py`: `ChatService` lifecycle, `answer_generator` injection.
* `api/deps.py` and `api/v1/router.py`.
* `pyproject.toml` and `uv.lock`: `langchain-anthropic` 1.7.13, `anthropic` 2.14.0,
  `sse-starlette` 3.7.1; the `llm` marker.
* Tests: `conftest.py` (wait for chat recovery; strips `ANTHROPIC_API_KEY` from routine
  runs), `fakes.py` (`ScriptedGenerator`), `integration/test_database.py` (head `0004`,
  pool count).

**Frontend, new:**
* `app/chat/{layout,page,actions}.tsx|ts` and `app/chat/[id]/page.tsx`
* `app/api/conversations/[id]/messages/route.ts`
* `components/chat/{chat-thread,answer-markdown,conversation-nav,conversation-buttons}.tsx`
* `lib/api/conversations.ts` and `lib/sse.ts`

**Frontend, modified:**
* `lib/api/types.ts`: chat contracts and stream-event guards.
* `components/app-shell.tsx`: Chat nav; pathname read inside `<Suspense>`.
* `package.json` and lockfile: `react-markdown` 10.1.0, `remark-gfm` 4.0.1.

**Other:**
* `Makefile`: `test-llm`; `not llm` added to the markers.
* `ci.yml` and `model.yml`: markers.
* `.env.example`.
* Docs: README, architecture overview, ADRs 0016–0021, the M2 plan (approved decisions and
  spike), this report, and the learning materials.

## 6. Test results (final run 2026-11-28, after the audit fixes)

| Check | Result |
|---|---|
| `ruff check`, `ruff format --check`, `mypy` (strict) | pass (79 files) |
| Unit (`not integration and not model and not llm`) | **160 passed** |
| Integration (`integration and not model and not llm`) | **59 passed** |
| Embedding model (`model and not llm`) | **12 passed** |
| Live API (`make test-llm`, real key) | **6 passed** (17 s) |
| Live API (`-m llm`) without a key | 6 errors: **fails, never skips**, as required |
| `make test` **with a real key in `.env`** | 219 passed; the key is not visible to any routine test |
| Routine CI simulation (`env -i`, no `.env`, no key, empty `HF_HOME`) | 160 + 59 passed; nothing written to the cache |
| `alembic downgrade 0003` / `upgrade head` (backfill) / `check`; `uv lock --check` | pass |
| ESLint, `next typegen && tsc`, `next build` (backend stopped, Node 24.22.2) | pass |
| Client bundle scan (API URL, `ANTHROPIC`, `sk-ant`) | 0 matches |
| `npm audit --omit=dev` | 0 vulnerabilities |

**New M2 tests (selection):**
* **Wire-format contract** (13): request layout with breakpoints, `search_result` shape,
  no sampling parameters; translation of text, citations, usage and stop reasons; 6 HTTP
  error mappings; a mid-stream error; upstream closed on cancel.
* **Integration over a real uvicorn server** (17):
  * cited streaming answer persisted;
  * no-context short-circuit;
  * invalid citations never shown;
  * history without markers;
  * refused and truncated outcomes;
  * mid-stream failure with partial text;
  * client disconnect, and the `aclose()` path;
  * no DB connection during generation;
  * no key → 503 while search works;
  * concurrency 429, same-conversation 409, budget 429;
  * concurrent budget race;
  * crash recovery;
  * conversation lifecycle.
* **Unit:** context budgets, citation validation (6 invalid shapes), pinned system-prompt
  hash, pricing and reservations, config, secret not leaked, question validation.

**Mutation checks:**

| Mutation | Effect on the test |
|---|---|
| Advisory lock removed | race test fails: 6 of 6 admitted |
| `aclosing` removed | `aclose` test fails: message stuck in `streaming` |
| Response guard (`abandon`) removed (audit) | never-started test fails: slot not released |
| `aclosing` removed in the route (audit) | send-failure test fails: message stuck in `streaming` |

## 7. Live verification (browser and protocol)

The real API, real PostgreSQL, the real embedding model and the production web build were
used, with a **scripted answer generator** (`tests/e2e/demo_app.py`; no key available):

| Scenario | Result |
|---|---|
| `curl` through the proxy | `meta`, 67 `delta`, 2 `citation`, `done`; citations resolved to real chunk IDs and pages; cost from usage incl. cache |
| Cross-origin POST / `text/plain` body to the proxy | 403 `forbidden_origin` / 415 |
| Headless Chrome | new conversation → streamed answer with heading, table, list and code block; Stop shown while streaming; citation chip → source anchor → passage; follow-up; Stop mid-answer → "Stopped. The partial answer is kept."; reload shows the persisted history with citations; sidebar titles |
| Injected `<script>` in model output | no script element, no `alert`, shown as text |
| Mobile 390 px | `/chat` and `/chat/[id]` fit the viewport |

## 7a. Live verification against the real API (2026-11-28)

**Safety first:**
* `.env` is git-ignored (`.gitignore:2`), not tracked, and absent from all history.
* The key was never printed: only its length and prefix shape were checked.
* The key appears 0 times in the API and web logs and in the client bundle.

**Model availability:** `models.retrieve("claude-sonnet-5-5")` succeeded, so the ID is
accepted by the account's key.

**Live tests (`make test-llm`, final run):**

| Test | Outcome | Input uncached / cache write / cache read / output | Cost | TTFT | Total |
|---|---|---|---|---|---|
| German answer cites passage 0 | ✓ "28 Urlaubstage", citation index correct | 303 / 0 / 1,685 / 212 | $0.0029 | 0.95 s | 2.09 s |
| Turn 2, history breakpoint (1st) | ✓ | 300 / 37 / 1,685 / 154 | $0.0024 | 0.83 s | 1.72 s |
| Turn 2, history breakpoint (2nd) | ✓ cache read of system + history (1,722) | 295 / 0 / 1,722 / 204 | $0.0028 | 0.68 s | 1.88 s |
| Not found | ✓ "I couldn't find this in your documents…" (no number) | 296 / 0 / 1,685 / 87 | $0.0016 | 0.78 s | 1.67 s |
| Prompt injection | ✓ "opens at 8 am", and says it ignored the embedded instruction | 200 / 0 / 1,685 / 81 | $0.0014 | 0.78 s | 1.36 s |
| Cancellation | ✓ stream closed within 1 ms of `cancel()` | unknown (not reported) | ≤ $0.0157 (reservation) | 0.75 s | — |

The first request read the cache written minutes earlier by the end-to-end run: the system
prompt prefix (1,685 tokens incl. framing) is shared across processes, as designed.

**End to end through the Next.js proxy** (real retrieval over `Mitarbeiterhandbuch.pdf`,
5 single-page chunks):

| Step | Result |
|---|---|
| Proxy guards | `Origin: null` 403, no Origin 403, cross-origin 403, `text/plain` 415 |
| Q1 "vacation days and customer records" | `meta`, 23 `delta`, 2 `citation`, `done`. Citations → **page 2** (leave) and **page 1** (records), the correct chunks. `grounded: true`. 782 / 1,685 write / 0 / 173 tokens, **$0.0075**, TTFT 1.01 s, total 2.07 s |
| Q2 follow-up "password rules" | 1 citation → **page 3**. Cache read 1,685 + write 161 (history). **$0.0042**, TTFT 0.93 s, total 2.23 s |
| Q3 cancelled after the first delta | stored `cancelled`, partial text "Here is a section" kept, cost NULL, reservation $0.0286 kept in the budget |
| `/llm/status` | spent $0.0404 = 0.0075 + 0.0042 + 0.0286 reservation |
| API log | `answer_completed` with citations and cost; `dropped_citations=0`; no warnings |

**Spend for all live verification:**

| Item | Billed (measured) | Worst case counted |
|---|---|---|
| 4 full `make test-llm` runs (first, diagnostic, accidental, final; see below) | ≈ $0.075 incl. cancelled streams | ≈ $0.13 |
| One single-test rerun | ≈ $0.003 | ≈ $0.003 |
| End-to-end run | ≈ $0.015 incl. cancelled request | $0.040 |
| **Total** | **≈ $0.09–0.10** | **≈ $0.17** (cap $0.50) |

Two of the full runs were avoidable: one to read a summary I had not saved, and one from a
stray command. Output is now always written to a file, and the live tests carry a ledger
and a hard cap (`LLM_TEST_BUDGET_USD`, default $0.50) that fails the remaining tests rather
than overspend.

## 8. Defects found and fixed during M2

| Finding | Fix |
|---|---|
| A mid-stream error arrives as HTTP 200 `APIStatusError` and was mapped non-retryable | mapped by error type (`overloaded_error`/`api_error` → `llm_unavailable`) |
| Nested async generators were not closed on `aclose()`: the message stayed `streaming` | `contextlib.aclosing` at both levels; `GeneratorExit` handled; test added and mutation-verified |
| The system-prompt hash test was tautological | literal hash pinned |
| `/llm/status` returned 500 without a database | degrades to "spend unknown" |
| `usePathname()` in the app shell broke prerendering of `/chat/[id]` | primary nav inside `<Suspense>` with a static fallback |
| `skipHtml` dropped ordinary text sharing a line with raw HTML (and a citation chip) | raw HTML rendered as escaped text |
| The concurrent-budget test conflated `llm_busy` with budget rejections | replaced by a precise admission race test |
| Two rows inserted in one transaction share `now()` | ordered by role on ties; comment corrected |

## 8a. Independent engineering audit (2026-11-28)

Scope: request lifecycle and SSE response, budget ledger, transactions, cancellation,
citations, prompt and caching, the Claude adapter, the proxy, test isolation.

| # | Severity | Finding | Fix | Regression test |
|---|---|---|---|---|
| A1 | **High** (requirement: budget not trivially bypassed) | Spend was summed over `messages`. Deleting a conversation deleted its spend, so the daily budget could be reset by deleting conversations. | `llm_usage` ledger (migration `0004`, backfilled), keyed by message id, **no foreign key**; admission writes the reservation, finalisation the actual cost; the budget sums the ledger | `test_deleting_conversations_does_not_reopen_the_budget` |
| A2 | **High** (requirement: CI independent of external APIs) | `make test` loads `.env`. With a real key there, routine tests that build the app without a fake (e.g. "no key → 503") got the **real** Claude client, would have called the paid API and failed. | `tests/conftest.py` removes `ANTHROPIC_API_KEY` from the environment before any fixture; only `-m llm` gets it back via a fixture | `test_routine_tests_never_see_the_api_key`; `make test` with the real key: 219 passed |
| A3 | Medium | An admitted answer whose SSE stream never started (client gone before the first byte, or `http.response.start` failed) ran no cleanup: an unstarted generator has no `finally`. The slot leaked and the row stayed `streaming`, so the conversation answered 409 until a restart. (Listed as "theoretical" in the first report; reproduced.) | `_AnswerEventSourceResponse` calls `ChatService.abandon()` after the response; it marks the row `cancelled` with cost 0 (Claude never called) and frees the slot; idempotent with `stream()` | `test_response_that_never_starts_frees_the_slot_and_the_conversation` (mutation-verified) |
| A4 | Medium | When a send fails mid-stream, sse-starlette `aclose()`s the route's iterator, but the route did not close `chat.stream`; cleanup depended on garbage collection. | `aclosing(chat.stream(...))` in the route | `test_send_failure_mid_stream_closes_the_answer_through_the_route` (mutation-verified) |
| A5 | Low | The no-context path skipped the "one answer at a time per conversation" check, so a turn could interleave with a streaming answer. | Same advisory lock and busy check as admission | `test_question_without_passages_waits_for_a_streaming_answer` |
| A6 | Low | `ClaudeAnswerGenerator` let `ChatAnthropic` create a connection pool closed only by `__del__`: an unclosed TLS socket to the API at shutdown (`ResourceWarning`, seen in the live run). | The adapter owns an `anthropic.DefaultAsyncHttpxClient` and closes it in `aclose()`, called at app shutdown | live suite: no `ResourceWarning` |
| A7 | Low | The proxy called `new URL(origin)` unguarded; `Origin: null` (sandboxed or privacy contexts) crashed the handler with 500 instead of 403. | `URL.canParse` guard | live proxy check: 403 |
| A8 | Low (observability) | `answer_cancelled` was never logged. Under anyio's cancel scopes, cancellation is re-delivered at every `await`, so code after the shielded write never ran (the write itself completes). | Log before the shielded write; comment explains why | covered by live E2E log review |
| A9 | Test quality | The live "not found" test matched a short English phrase list and failed once on valid wording. | Wider list of negations (EN/DE) plus the "no invented amount" checks; the ledger keeps an answer excerpt for diagnosis | 2 live passes |

Reviewed and found sound:
* the advisory-lock admission;
* no connection held during generation;
* citation index validation (all real citations resolved, 0 dropped);
* the caching layout (system and history reads confirmed live);
* error mapping;
* the key never reaches logs, responses or the bundle;
* the Markdown renderer settings.

## 9. Cost and performance (measured 2026-11-28)

* **Actual cost per answer:** **$0.0014–0.0075**. First answer in a fresh cache window
  $0.0075 (cache write); typical follow-up **$0.003–0.004**. That is about 4–5× cheaper
  than the earlier $0.01–0.02 estimate, because the retrieved context for the demo
  documents is small and effort `low` keeps output at 80–220 tokens.
* **Reservation per question:** $0.029–0.031 (2,048-token output reserved), so the budget
  admits about 32 questions per day while real spend would allow 130–300. This is the
  accepted pessimism; see §10.
* **Latency (real model):** TTFT **0.68–1.01 s** (median ≈ 0.83 s); total **1.4–2.5 s** for
  answers of 80–270 output tokens. Retrieval ~25 ms (M1).
* **Caching:** 1,685-token system prefix read at $0.10/MTok on every request after the
  first in a 5-minute window (saves ≈ $0.003 per request); the history breakpoint adds a
  read of 37–161 further tokens on follow-ups.

## 10. Remaining limitations and open items

* **Cancelled and failed requests keep their full reservation** (≈ $0.03), because the
  API reports no usage for a closed stream; actual billed cost is much lower. Accepted
  pessimism; an M3+ option is to book the `message_start` input usage plus an estimate of
  streamed output.
* Costs are computed from public prices; there is no reconciliation with Anthropic's
  usage API (needs an admin key).
* Data is sent to Anthropic (disclosed). No query rewriting.
* A crash (unlike a cancel) loses the partial text; the row is marked `interrupted` at the
  next start. A shielded final write still in flight at shutdown has the same outcome.
* Not re-run in a real browser against the real model; the browser walkthrough used the
  scripted generator. The live E2E used the same proxy and SSE path with an HTTP client.
* Real SSE responses were not recorded as contract fixtures; the live tests cover the same
  shapes.
* A citation marker placed inside a raw HTML line renders as text (the source is still
  listed).
* The conversation list sits above the thread on small screens (no collapsible drawer).
* M1 remote CI verified on 2026-11-28 after the push: `CI` run #3 and `Embedding model` run #1 for `a839ad4` both succeeded.

## 11. Definition of done

- [x] Decisions D1–D10 implemented as approved; capability spike recorded
- [x] EN/DE questions answered from retrieved passages with validated, page-accurate, clickable citations
- [x] Follow-ups use history; conversations persist and can be deleted
- [x] Streaming end to end; Stop cancels the upstream request (tested at adapter, service and browser level)
- [x] "Not found" behaviour; no-retrieval short-circuit makes no API call
- [x] Every outcome stored and shown: complete, no_context, truncated, refused, failed, cancelled, interrupted
- [x] Budget $1, output 2,048, concurrency 4, configurable; race-free reservations; usage and cost per answer and per day
- [x] Error model implemented and tested; key never in logs, responses or bundle
- [x] M0/M1 functionality unchanged (all earlier tests pass)
- [x] Routine CI independent of external APIs and the model (simulated)
- [x] `/chat` in the dark/cyan identity; keyboard and live-region support; 390 px layouts fit
- [x] Live API checks (6/6), live E2E through the proxy, cost and TTFT measured (§7a, §9)
- [x] Independent audit: 9 findings fixed (2 high, 2 medium, 4 low, 1 test-quality), each with
      a regression test or live check (§8a)
- [x] ADRs 0016–0021, README, overview, report, learning guide, interview prep
- [x] Approval of the audit results (2026-11-28); committed
