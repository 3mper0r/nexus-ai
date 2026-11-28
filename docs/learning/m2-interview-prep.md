# M2 Technical interview preparation

Fifteen questions grounded in the NEXUS AI M2 implementation (grounded answers with
Claude). Senior-level model answers plus common incorrect answers. ★ marks questions aimed
at engineers moving into AI engineering.

---

### 1. ★ Walk through a grounded-answer request end to end. Where can it go wrong?

**Model answer:**
1. Admission: key configured, recovery done, a generation slot free.
2. Short transaction: conversation and history.
3. Embed the query locally, HNSW top-k, assemble a budgeted per-document context.
4. If nothing was retrieved, return a fixed answer without calling the model.
5. Otherwise, under an advisory lock, check the budget with a worst-case reservation and
   insert the user and assistant rows.
6. Stream from Claude with no DB connection held, validating citations as they arrive.
7. Record the outcome in one short transaction.

Failure points: retrieval misses (the answer says "not found"), the model ignoring the
sources (citations and the `grounded` flag expose it), upstream errors (typed codes,
partial text kept), a client disconnect (upstream closed, `cancelled`), a crash
(`interrupted` at startup).

**Common wrong answers:** "Embed, call the LLM, return the text." No admission, no
persistence, no failure modes.

---

### 2. ★ How do you make citations trustworthy instead of decorative?

**Model answer:** Use the API's structured citation objects, not markers the model types.
Send one `search_result` per document with one text block per chunk, so a citation's
`(result index, block range)` maps to exact chunks and pages. Validate the indices against
what was sent, and drop anything out of range. Display **our** stored passage, not the
model's `cited_text`. Flag answers without valid citations. Store a snapshot so history
survives document deletion.

**Common wrong answers:** "Ask the model to add [1] references." "Trust `cited_text`."

---

### 3. Why must the "budget check" be done under a lock, and why a reservation rather than the final cost?

**Model answer:** "Read today's spend, then insert" is a check-then-act race. Concurrent
requests all read the same total and all pass. `pg_advisory_xact_lock` serialises the
critical section across connections and processes. The cost is unknown until the stream
ends, so we reserve the worst case (all input uncached, full output limit) and replace it
with the actual cost afterwards. Cancelled, failed and crashed generations keep their
reservation. Our test: six concurrent admissions with a budget for two → exactly two;
without the lock, six. Keep spend in an append-only ledger, not in deletable rows: our
first version summed `messages`, so deleting a conversation refunded its spend.

**Common wrong answers:** "Check the provider's usage dashboard." "A Python lock is enough"
(only within one process).

---

### 4. Why can't you hold the request's database session while streaming an LLM answer?

**Model answer:** A stream lasts seconds to minutes. An open transaction holds a pooled
connection (the pool is small), shows as `idle in transaction`, holds locks and snapshots,
and blocks vacuum. With FastAPI `yield` dependencies, the request session lives until the
stream ends. Pattern: short transaction before, generate without a connection, short
transaction after. We assert zero checked-out connections and no `idle in transaction`
backend mid-stream.

**Common wrong answers:** "Async means it doesn't block anything."

---

### 5. Explain how a "Stop" click in the browser stops token generation at Anthropic.

**Model answer:**
1. `AbortController.abort()` aborts the browser's `fetch`.
2. The Next.js Route Handler's `request.signal` aborts its upstream `fetch` to the API.
3. uvicorn reports `http.disconnect`, and sse-starlette cancels the task running our
   generator.
4. `CancelledError` unwinds through `ChatAnthropic.astream`, and the SDK closes the HTTP
   response to Anthropic, so generation stops.
5. We persist the partial text as `cancelled` in a shielded write.

The other path, `aclose()` on our generator, needs `contextlib.aclosing` on the nested
generators.

**Common wrong answers:** "Ignore the rest of the stream on the client" (you keep paying).

---

### 6. What does `contextlib.aclosing` fix that `async for` doesn't?

**Model answer:** A suspended async generator only runs its `finally` when closed or
collected. `async for` doesn't call `aclose()` when it exits because of an exception or a
`GeneratorExit` from an outer generator. If the outer generator is closed, the inner ones
stay suspended, and their cleanup (record the outcome, close upstream) never runs.
`aclosing(gen)` guarantees `aclose()`. JavaScript's `for await` calls `return()` on break,
so TS developers often miss this.

**Common wrong answers:** "Python closes generators automatically when the loop ends."

---

### 7. ★ How does prompt caching work, and how did you lay out the prompt for it?

**Model answer:** Caching is a prefix match up to a `cache_control` breakpoint (minimum
prefix 512 tokens on current models). Writes cost 1.25× input, reads 0.05× on Sonnet 5.5,
with a 5-minute TTL refreshed on reads. Our layout: stable system prompt (breakpoint) →
history (breakpoint on the last turn) → unique retrieved context + question (never cached).
Automatic caching would put the breakpoint after the unique tail and pay a write every
request with zero reads. The system prompt is constant, with its hash pinned. Usage fields
(`cache_read_input_tokens`) are the only proof.

**Common wrong answers:** "Cache the retrieved documents." "Just set `cache_control` at the
top level."

---

### 8. ★ How do you defend a RAG system against prompt injection in documents?

**Model answer:**
* Retrieved text goes in data blocks (`search_result`), never in the system prompt.
* The system prompt says results are untrusted data whose instructions must be ignored.
* No tools are exposed (no actions to hijack).
* Citations make every claim verifiable.
* Output is rendered safely: no raw HTML, no images, sanitised links, nothing executed.

Residual risk: a misleading answer. Hence live tests with hostile passages, and showing
sources prominently.

**Common wrong answers:** "Filter the word 'ignore'." "The model is aligned, so it won't
follow them."

---

### 9. How do you test an LLM feature without making CI flaky or expensive?

**Model answer:** Put a Protocol in front of the model.
* **Scripted fakes** test everything around it (citations, failures, cancellation, budget,
  persistence) deterministically.
* **Contract tests** run the real adapter against the provider's documented wire format
  through a mock transport. That caught the HTTP-200 mid-stream error and the bearer-token
  change.
* **Live tests** are opt-in, small and cheap, and fail rather than skip without a key.
* **Mutation checks** prove the key tests can fail.
* SSE tests run against a real server, because in-process transports buffer.

**Common wrong answers:** "Mock `invoke` to return a string." "Run the real API in CI on
every PR."

---

### 10. ★ The model's stream fails halfway. What does the user see, what is stored, what is billed?

**Model answer:** The user sees the partial text and an error event with a stable code and
a retryable flag; the API's error body is never forwarded. Stored: the partial content,
status `failed`, `error_code`, cost `NULL`. Billed: whatever Anthropic generated. We don't
know the usage, so the worst-case reservation keeps counting against the budget. Mapping:
errors inside a started stream arrive as `APIStatusError` with HTTP 200, so classify by
error type (`overloaded_error` is retryable).

**Common wrong answers:** "Retry automatically mid-stream" (duplicated text, double cost).

---

### 11. ★ Why did you disable automatic refusal fallback, and how are refusals handled?

**Model answer:** A fallback silently switches models. That changes quality and cost, and
can mask a policy decision; for a grounded-answer product, transparency matters more. A
refusal arrives as HTTP 200 with `stop_reason: refusal` and `stop_details.category`. We store
`refused` with the category and show it. The alternative (server-side `fallbacks`) is a
product decision, recorded in the plan.

**Common wrong answers:** "Refusals raise an exception." "Retry with a different prompt."

---

### 12. Why put the browser → API stream through a Next.js Route Handler, and what security does it need?

**Model answer:** The API stays private (no CORS, no exposed URL or key), and the abort
signal links both hops. The Route Handler lacks Server Actions' built-in CSRF check, and
a cross-site page could POST `text/plain` without a preflight and spend budget. So we
require `application/json` and a same-origin `Origin` header, and validate the body. The
handler passes the API's JSON admission errors through unchanged and streams the SSE body
without buffering (`no-transform`, `X-Accel-Buffering: no`).

**Common wrong answers:** "CORS protects it" (CORS doesn't stop simple requests from being
sent).

---

### 13. What changes when you switch models (e.g. Sonnet → Opus or a new version)?

**Model answer:**
* Pricing: the config is rejected without a price entry, which keeps the budget sound.
* Behaviour: default effort, thinking rules, refusals and tokenizer can differ. Re-run the
  live golden set.
* API surface: e.g. sampling parameters rejected, forced `tool_choice` removed.
* Caching: caches are per model, so the first requests write again.

The embedding model is independent: it is local and only affects retrieval.

**Common wrong answers:** "Change the model string, done."

---

### 14. ★ What is the cost per answer, and what levers reduce it?

**Model answer:** Measured on Sonnet 5.5 ($2 in / $10 out per MTok) at effort `low`:
$0.0014–0.0075 per answer, typically $0.003–0.004 once the 1,685-token system prefix is
cached; time to first token 0.7–1.0 s. We reserve about $0.03 worst case. The levers, roughly in
order:
1. Caching the stable prefix.
2. Context budget and top-k.
3. History window, with no old passages replayed.
4. Effort (`low`).
5. Output cap.
6. No call when nothing was retrieved.
7. The model choice, which is a product decision.

Measure per *completed* answer, not per request.

**Common wrong answers:** "Use the cheapest model" (without measuring quality).

---

### 15. ★ Users say answers are wrong but well-cited. How do you investigate?

**Model answer:**
1. Open the cited passages: is the claim actually in them?
2. If yes, the retrieval or document is the problem (stale or contradictory content,
   extraction errors such as M1's math).
3. If no, the model over-generalised: tighten the prompt, raise effort, or check the
   history (follow-ups can carry earlier claims; our prompt says facts come only from
   current results).

Then build a golden set and measure groundedness and faithfulness (RAGAS in M4) before
changing models.

**Common wrong answers:** "Lower the temperature" (not available on current models, and not
the cause).
