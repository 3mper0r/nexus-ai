# ADR 0017: Grounding and native `search_result` citations

* Status: Accepted (M2, 2026-11-27)

## Decision
* Retrieved chunks are sent as **`search_result` content blocks** in the current user
  message, one block per document (`source = nexus://documents/{id}`, `title` = filename),
  **one text block per chunk**, `citations: {enabled: true}`. Chunks are single-page
  (ADR 0013), so a citation's `(search_result_index, start_block_index, end_block_index)`
  maps to exact chunks and pages.
* **Validation:** a citation is accepted only if the indices point at passages we sent;
  otherwise it is dropped and logged. The text shown to users is always **our stored
  passage**, never the model-echoed `cited_text`. Repeated citations reuse their number.
* Markers `[n](#cite-n)` are inserted by the API after the cited text block, streamed as
  ordinary text and stored in the answer; they are stripped before an answer is replayed as
  history.
* **Insufficient evidence:** no retrieved passage → fixed bilingual "not found" answer, no
  API call, cost 0 (`no_context`). Otherwise the system prompt requires answers only from the
  results and an explicit "couldn't find this in your documents"; an answer without valid
  citations is flagged `grounded: false` and the UI warns.
* **Prompt injection:** the system prompt states that search results are untrusted data and
  that instructions inside them must never be followed; M2 has no tools, so the worst case is
  a misleading answer, which citations make checkable. Retrieved text is never placed in the
  system prompt.

## Consequences
* + Citations cannot reference anything we did not send; displayed evidence is authoritative.
* − Whole-chunk granularity (a citation covers a ~400-token passage).
* − A marker the model places inside a raw HTML line is shown as text (see ADR 0021).
* Rejected: `[1]` markers parsed from model prose (unverifiable), `document` blocks with
  `char_location` (no chunk/page mapping).
