"""Model prices and cost arithmetic.

Prices are USD per million tokens, Claude API first-party rates, as published on
Anthropic's pricing page. **Checked: 2026-11-27.** A model must be listed here to be
configured (``Settings`` rejects unknown models), because the daily budget guard
needs a price to reserve against.
"""

from __future__ import annotations

from dataclasses import dataclass

PRICES_CHECKED_ON = "2026-11-27"


@dataclass(frozen=True, slots=True)
class ModelPrice:
    input: float
    output: float
    cache_write_5m: float
    cache_read: float


PRICES: dict[str, ModelPrice] = {
    # Sonnet 5.5: cache reads at 0.05x base input (pricing page, footnote 2).
    "claude-sonnet-5-5": ModelPrice(input=2.00, output=10.00, cache_write_5m=2.50, cache_read=0.10),
    "claude-opus-5-5": ModelPrice(input=4.00, output=20.00, cache_write_5m=5.00, cache_read=0.20),
}


@dataclass(frozen=True, slots=True)
class TokenUsage:
    """Token counts as billed. ``input_uncached`` excludes cache reads and writes."""

    input_uncached: int = 0
    cache_creation: int = 0
    cache_read: int = 0
    output: int = 0

    @property
    def input_total(self) -> int:
        return self.input_uncached + self.cache_creation + self.cache_read


def cost_usd(model: str, usage: TokenUsage) -> float:
    price = PRICES[model]
    total = (
        usage.input_uncached * price.input
        + usage.cache_creation * price.cache_write_5m
        + usage.cache_read * price.cache_read
        + usage.output * price.output
    ) / 1_000_000
    return round(total, 6)


# Conservative token estimate for budget reservations: ~2 characters per token
# over-estimates English (~4) and German (~3) text, so a reservation is an upper bound.
_CHARS_PER_TOKEN_CONSERVATIVE = 2
_REQUEST_OVERHEAD_TOKENS = 500  # message framing, search_result wrappers


def reservation_usd(model: str, *, prompt_chars: int, max_output_tokens: int) -> float:
    """Worst-case cost of one generation: every input token uncached and the full
    output limit used (thinking tokens count against it)."""
    price = PRICES[model]
    input_tokens = prompt_chars // _CHARS_PER_TOKEN_CONSERVATIVE + _REQUEST_OVERHEAD_TOKENS
    worst = input_tokens * max(price.input, price.cache_write_5m) + max_output_tokens * price.output
    return round(worst / 1_000_000, 6)
