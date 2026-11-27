"""The embedding contract used by ingestion and search.

Domain code depends on this Protocol, not on sentence-transformers. That keeps a
~470 MB model and the PyTorch runtime out of every unit and integration test:
tests inject a small deterministic implementation (``tests/fakes.py``).
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

import numpy as np
from numpy.typing import NDArray

# Fixed by the database schema (``vector(384)``). Changing models with a different
# dimension requires a migration and re-embedding every chunk.
EMBEDDING_DIMENSION = 384

TokenSpan = tuple[int, int]
FloatMatrix = NDArray[np.float32]


class Embedder(Protocol):
    """Turns text into unit-length vectors in one shared vector space."""

    @property
    def model_id(self) -> str:
        """Stable identifier of the vector space, e.g. ``name@revision``."""
        ...

    @property
    def dimension(self) -> int: ...

    def token_spans(self, text: str) -> list[TokenSpan]:
        """Character spans of the model's tokens (no special tokens, no truncation).

        Chunk sizes are measured with this, so they are counted in exactly the
        units the model truncates on.
        """
        ...

    def embed_passages(self, texts: Sequence[str]) -> FloatMatrix:
        """Embed document passages. Returns shape ``(len(texts), dimension)``."""
        ...

    def embed_query(self, text: str) -> FloatMatrix:
        """Embed a search query. Returns shape ``(dimension,)``."""
        ...
