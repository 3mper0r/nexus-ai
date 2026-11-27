"""Chunker invariants, checked with the deterministic word tokenizer."""

from __future__ import annotations

import pytest

from nexus_api.documents.chunking import TextChunk, chunk_pages
from tests.fakes import HashEmbedder, paragraph

TOKENIZER = HashEmbedder()


def _chunk(pages: list[str], max_tokens: int = 40, overlap: int = 8) -> list[TextChunk]:
    return chunk_pages(pages, TOKENIZER, max_tokens=max_tokens, overlap_tokens=overlap)


def _token_ids(text: str) -> list[str]:
    return [text[a:b] for a, b in TOKENIZER.token_spans(text)]


@pytest.mark.parametrize(("max_tokens", "overlap"), [(40, 0), (40, 8), (64, 16), (100, 30)])
def test_invariants_hold(max_tokens: int, overlap: int) -> None:
    pages = [paragraph(150, "alpha"), "", paragraph(90, "beta"), paragraph(400, "gamma")]
    chunks = _chunk(pages, max_tokens, overlap)

    assert chunks, "non-empty input must produce chunks"
    assert [c.ordinal for c in chunks] == list(range(len(chunks)))
    for chunk in chunks:
        assert chunk.text.strip(), "no empty chunks"
        assert 1 <= chunk.token_count <= max_tokens
        assert chunk.token_count == len(TOKENIZER.token_spans(chunk.text))
        assert 1 <= chunk.page_start == chunk.page_end <= len(pages)
    starts = [c.page_start for c in chunks]
    assert starts == sorted(starts), "page ranges are monotonic"


def test_every_token_is_covered_and_overlap_is_bounded() -> None:
    pages = [paragraph(300, "delta")]
    chunks = _chunk(pages, max_tokens=50, overlap=10)
    full = _token_ids(pages[0])

    # Each chunk is a contiguous run of document tokens; locate it and check that
    # consecutive runs touch or overlap by at most overlap_tokens.
    covered_until = 0
    for chunk in chunks:
        tokens = _token_ids(chunk.text)
        # Find this chunk's start at or after (covered_until - overlap).
        start = next(
            i
            for i in range(max(0, covered_until - 10), len(full))
            if full[i : i + len(tokens)] == tokens
        )
        assert start <= covered_until, "no gap between consecutive chunks"
        assert covered_until - start <= 10, "overlap never exceeds overlap_tokens"
        covered_until = start + len(tokens)
    assert covered_until == len(full), "the last chunk reaches the end of the document"


def test_pages_map_to_citations() -> None:
    pages = ["first page words " * 5, "", "third page words " * 5]
    chunks = _chunk(pages, max_tokens=12, overlap=0)

    # 15 tokens on page 1, none on page 2, 15 on page 3; windows of 12 tokens.
    # Chunks never cross a page break, so each cites exactly one page.
    assert [(c.page_start, c.page_end, c.token_count) for c in chunks] == [
        (1, 1, 12),
        (1, 1, 3),
        (3, 3, 12),
        (3, 3, 3),
    ]


def test_prefers_paragraph_boundaries() -> None:
    first = " ".join(["one"] * 25) + "."
    second = " ".join(["two"] * 25) + "."
    chunks = _chunk([first + "\n\n" + second], max_tokens=40, overlap=0)

    assert chunks[0].text == first, "cut exactly at the paragraph break"
    assert chunks[1].text == second


def test_prefers_sentence_end_over_word_boundary() -> None:
    text = " ".join(["word"] * 24) + ". " + " ".join(["more"] * 30)
    chunks = _chunk([text], max_tokens=40, overlap=0)

    assert chunks[0].text.endswith("."), chunks[0].text


def test_unbreakable_text_is_cut_at_the_token_limit() -> None:
    # A single run of punctuation tokens with no whitespace (no boundary anywhere).
    text = "!" * 100
    chunks = _chunk([text], max_tokens=40, overlap=0)

    assert [c.token_count for c in chunks] == [40, 40, 20]


def test_empty_input_produces_no_chunks() -> None:
    assert _chunk([]) == []
    assert _chunk(["", "", ""]) == []


def test_is_deterministic() -> None:
    pages = [paragraph(500, "eps"), paragraph(200, "zeta")]
    assert _chunk(pages) == _chunk(pages)


@pytest.mark.parametrize(("max_tokens", "overlap"), [(1, 0), (40, 20), (40, -1)])
def test_rejects_invalid_parameters(max_tokens: int, overlap: int) -> None:
    with pytest.raises(ValueError, match="max_tokens"):
        _chunk(["text"], max_tokens, overlap)
