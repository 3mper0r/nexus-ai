"""Token-aware, page-aware chunking.

Chunks are sized in the embedding model's own tokens: a size limit counted in
characters (or in another model's tokens) can silently exceed the model's
512-token window, and everything past the window is never embedded.

Algorithm, applied to each page independently:

1. Take a window of at most ``max_tokens`` tokens. Unless it is the end of the
   page, move the cut back to the best boundary in the second half of the
   window: paragraph break > sentence end > word boundary.
2. Start the next window up to ``overlap_tokens`` tokens before the cut, aligned
   to the start of a word, so text cut at a boundary keeps some context.

**Chunks never cross a page boundary**, so every search hit cites exactly one
page. The trade-off: a sentence that continues on the next page is split between
two chunks, and very short pages produce small chunks. (An earlier version let
chunks span pages; a real-model test showed hits citing "pp. 3-4" for text that
was entirely on page 4.)

Pure functions, no I/O: deterministic and unit-testable with a fake tokenizer.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from nexus_api.embeddings.base import TokenSpan

_SENTENCE_END = (".", "!", "?", ":", ";", "。")


class Tokenizer(Protocol):
    def token_spans(self, text: str) -> list[TokenSpan]: ...


@dataclass(frozen=True, slots=True)
class TextChunk:
    ordinal: int
    text: str
    page_start: int
    page_end: int
    token_count: int


def chunk_pages(
    pages: Sequence[str],
    tokenizer: Tokenizer,
    *,
    max_tokens: int,
    overlap_tokens: int,
) -> list[TextChunk]:
    """Split page texts (index 0 = page 1) into overlapping chunks."""
    if max_tokens < 2 or not 0 <= overlap_tokens < max_tokens // 2:
        raise ValueError("require max_tokens >= 2 and 0 <= overlap_tokens < max_tokens // 2")

    chunks: list[TextChunk] = []
    for page_number, page_text in enumerate(pages, start=1):
        for text, token_count in _split(page_text, tokenizer, max_tokens, overlap_tokens):
            chunks.append(
                TextChunk(
                    ordinal=len(chunks),
                    text=text,
                    page_start=page_number,
                    page_end=page_number,
                    token_count=token_count,
                )
            )
    return chunks


def _split(
    text: str, tokenizer: Tokenizer, max_tokens: int, overlap_tokens: int
) -> list[tuple[str, int]]:
    """Windows over one page's tokens: ``(chunk_text, token_count)`` pairs."""
    spans = tokenizer.token_spans(text)
    total = len(spans)
    pieces: list[tuple[str, int]] = []
    start = 0
    while start < total:
        end = min(start + max_tokens, total)
        if end < total:
            end = _best_cut(text, spans, lowest=start + max_tokens // 2, highest=end)
        pieces.append((text[spans[start][0] : spans[end - 1][1]], end - start))
        if end == total:
            break
        start = _overlap_start(text, spans, earliest=max(end - overlap_tokens, start + 1), cut=end)
    return pieces


def _gap_before(text: str, spans: Sequence[TokenSpan], k: int) -> str:
    """Text between token ``k - 1`` and token ``k`` (whitespace, if any)."""
    return text[spans[k - 1][1] : spans[k][0]]


def _boundary_score(text: str, spans: Sequence[TokenSpan], k: int) -> int:
    """How good it is to cut between token ``k - 1`` and token ``k``."""
    gap = _gap_before(text, spans, k)
    if "\n\n" in gap:
        return 3
    if gap and text[spans[k - 1][1] - 1] in _SENTENCE_END:
        return 2
    return 1 if gap else 0  # 0 = the cut would split a word into sub-word pieces


def _best_cut(text: str, spans: Sequence[TokenSpan], *, lowest: int, highest: int) -> int:
    best, best_score = highest, -1
    for k in range(highest, lowest, -1):  # prefer the latest cut among equal scores
        score = _boundary_score(text, spans, k)
        if score > best_score:
            best, best_score = k, score
            if score == 3:
                break
    return best


def _overlap_start(text: str, spans: Sequence[TokenSpan], *, earliest: int, cut: int) -> int:
    """First token at or after ``earliest`` that begins a word (fallback: ``earliest``)."""
    for k in range(earliest, cut + 1):
        if k == cut or _gap_before(text, spans, k):
            return k
    return earliest
