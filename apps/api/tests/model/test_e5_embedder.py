"""Real-model checks for intfloat/multilingual-e5-small (marker ``model``).

These load the pinned model from the local cache, so they are excluded from the
routine suites and run explicitly with ``make test-model``.
"""

from __future__ import annotations

import numpy as np
import pytest

from nexus_api.documents.chunking import chunk_pages
from nexus_api.embeddings.base import Embedder
from nexus_api.embeddings.e5 import PASSAGE_PREFIX, E5Embedder
from tests.model.corpus import CROSS_LINGUAL, PASSAGES, QUERIES

pytestmark = pytest.mark.model


def test_vectors_have_schema_dimension_and_unit_length(e5_embedder: Embedder) -> None:
    passages = e5_embedder.embed_passages(["Ein kurzer Satz.", "A short sentence."])
    query = e5_embedder.embed_query("short sentence")

    assert passages.shape == (2, 384)
    assert passages.dtype == np.float32
    assert query.shape == (384,)
    np.testing.assert_allclose(np.linalg.norm(passages, axis=1), 1.0, atol=1e-5)
    np.testing.assert_allclose(np.linalg.norm(query), 1.0, atol=1e-5)


def test_is_deterministic(e5_embedder: Embedder) -> None:
    first = e5_embedder.embed_passages(["Deterministic output, please."])
    second = e5_embedder.embed_passages(["Deterministic output, please."])
    np.testing.assert_allclose(first, second, atol=1e-6)


def test_query_and_passage_prefixes_are_applied(e5_embedder: Embedder) -> None:
    text = "How long are invoices archived?"
    as_query = e5_embedder.embed_query(text)
    as_passage = e5_embedder.embed_passages([text])[0]

    similarity = float(as_query @ as_passage)
    assert similarity < 0.999, "query and passage inputs must be embedded differently"


def test_model_id_pins_the_revision(e5_embedder: Embedder) -> None:
    assert e5_embedder.model_id == (
        "intfloat/multilingual-e5-small@614241f622f53c4eeff9890bdc4f31cfecc418b3"
    )


def _rank(e5_embedder: Embedder, query: str, keys: list[str]) -> list[str]:
    matrix = e5_embedder.embed_passages([PASSAGES[key] for key in keys])
    scores = matrix @ e5_embedder.embed_query(query)
    return [keys[i] for i in np.argsort(-scores)]


def test_bilingual_golden_set_recall_at_2(e5_embedder: Embedder) -> None:
    """Each topic has an English and a German passage: both should rank on top."""
    keys = list(PASSAGES)
    misses = []
    reciprocal_ranks = []
    for query, topic in QUERIES:
        ranking = _rank(e5_embedder, query, keys)
        topic_ranks = [i for i, key in enumerate(ranking) if key.startswith(f"{topic}-")]
        reciprocal_ranks.append(1 / (topic_ranks[0] + 1))
        if topic_ranks[0] >= 2:
            misses.append((query, ranking[:3]))

    assert not misses, misses
    assert sum(reciprocal_ranks) / len(reciprocal_ranks) == 1.0, "MRR: top hit always on topic"


def test_cross_lingual_retrieval(e5_embedder: Embedder) -> None:
    """Query in one language, the only relevant passage in the other."""
    for query, target in CROSS_LINGUAL:
        language = target.split("-")[1]
        other_language_keys = [k for k in PASSAGES if k.endswith(f"-{language}")]
        ranking = _rank(e5_embedder, query, other_language_keys)
        assert ranking[0] == target, (query, ranking[:3])


def test_real_tokenizer_chunks_fit_the_model_window(e5_embedder: Embedder) -> None:
    """Chunks of 400 tokens stay within 512 after the prefix and special tokens."""
    assert isinstance(e5_embedder, E5Embedder)
    german = " ".join(PASSAGES[k] for k in PASSAGES if k.endswith("-de")) * 12
    english = " ".join(PASSAGES[k] for k in PASSAGES if k.endswith("-en")) * 12
    chunks = chunk_pages([german, english], e5_embedder, max_tokens=400, overlap_tokens=60)

    assert len(chunks) >= 4
    tokenizer = e5_embedder._model.tokenizer
    for chunk in chunks:
        assert chunk.token_count <= 400
        ids = tokenizer(PASSAGE_PREFIX + chunk.text, truncation=False)["input_ids"]
        assert len(ids) <= 512, (len(ids), chunk.ordinal)
