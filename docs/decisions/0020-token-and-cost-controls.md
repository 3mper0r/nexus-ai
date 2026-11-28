# ADR 0020: Token and cost controls with race-free budget enforcement

* Status: Accepted (M2, 2026-11-27)

## Decision
| Control | Default (setting) |
|---|---|
| Model / effort | `claude-sonnet-5-5` / `low` (`LLM_MODEL`, `LLM_EFFORT`) |
| Output limit | 2,048 tokens (`LLM_MAX_OUTPUT_TOKENS`; thinking counts against it) |
| Daily budget | $1.00 (`LLM_DAILY_BUDGET_USD`, UTC day) |
| Concurrency | 4 generations (`LLM_MAX_CONCURRENT_GENERATIONS`), non-blocking → 429 |
| Context / history | `RAG_TOP_K=8`, `RAG_CONTEXT_CHAR_BUDGET=24000`, `RAG_HISTORY_TURNS=4` |

* **Prices** (`llm/pricing.py`, checked 2026-11-27 on Anthropic's pricing page): Sonnet 5.5
  $2.00 in / $10.00 out / $2.50 cache write (5 min) / $0.10 cache read per MTok. A model
  without a price entry is rejected at startup.
* **Reservation-based budget:** before generating, the worst-case cost (prompt characters /
  2 as tokens, all uncached, plus the full output limit) is reserved by inserting the
  assistant row with `reserved_cost_usd`, **inside a transaction holding
  `pg_advisory_xact_lock`** that first sums today's `COALESCE(cost_usd, reserved_cost_usd)`.
  Concurrent requests serialise on the lock and cannot all pass the check; this works
  across processes and restarts. When the answer completes, the actual cost replaces the
  reservation; a cancelled, failed or interrupted answer keeps its reservation
  (conservative); an answer abandoned before its stream started costs 0 (Claude was never
  called).
* **Ledger, not messages (audit, 2026-11-28):** reservations and costs live in
  `llm_usage` (migration `0004`), one row per admitted generation, keyed by the assistant
  message id **without a foreign key**. The first version summed `messages`, so deleting a
  conversation deleted its spend and reopened the budget. Tested by
  `test_deleting_conversations_does_not_reopen_the_budget`. Test: 6 concurrent admissions against a budget for 2 → exactly 2;
  without the lock, all 6 (mutation-verified).
* `GET /api/v1/llm/status` reports model, budget, spend today and active generations
  (degrades to "spend unknown" without a database).

## Consequences
* + The budget cannot be overspent by concurrency; spending is visible per message and per day.
* − Reservations are pessimistic: with a small budget, fewer questions are admitted than
  would fit. Measured reservations: $0.029–0.031 per question (≈ 32 questions per $1 day);
  measured live cost: $0.0014–0.0075 per answer (2026-11-28).
* − Usage and cost columns on `messages` duplicate the ledger for display; the ledger is
  authoritative for the budget.
* − Costs are estimates from public prices; they are not reconciled with Anthropic billing.
