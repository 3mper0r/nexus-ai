"""``intfloat/multilingual-e5-small`` via sentence-transformers, CPU only.

Model facts (from the pinned revision's configuration files):

* 384-dimensional output, mean pooling, L2-normalised (``2_Normalize`` module).
* XLM-RoBERTa tokenizer (250k vocabulary, ~100 languages incl. English and German).
* At most 512 tokens per input; longer inputs are silently truncated, which is why
  chunk sizes are measured with this tokenizer (see ``token_spans``).
* Asymmetric retrieval: inputs must be prefixed with ``"query: "`` or
  ``"passage: "``. The model was trained that way; omitting the prefixes measurably
  degrades retrieval.
* Licence: MIT.

Loading is offline-only (``local_files_only=True``): the model is fetched once with
``make models`` (``python -m nexus_api.embeddings.download``), so a running API never
reaches out to the Hugging Face Hub and a missing model is an explicit readiness
failure rather than a slow download on the first request.
"""

from __future__ import annotations

import threading
from collections.abc import Sequence

import numpy as np
import structlog
import torch
from sentence_transformers import SentenceTransformer
from tokenizers import Tokenizer

from nexus_api.core.config import Settings
from nexus_api.embeddings.base import EMBEDDING_DIMENSION, FloatMatrix, TokenSpan

logger = structlog.stdlib.get_logger(__name__)

QUERY_PREFIX = "query: "
PASSAGE_PREFIX = "passage: "

# Files needed to run the model with the torch backend. Everything else in the
# repository (ONNX/OpenVINO exports, pytorch_model.bin, evaluation results) is
# skipped, saving ~1.3 GB.
MODEL_FILES = [
    "config.json",
    "modules.json",
    "sentence_bert_config.json",
    "1_Pooling/config.json",
    "model.safetensors",
    "tokenizer.json",
    "tokenizer_config.json",
    "special_tokens_map.json",
    "sentencepiece.bpe.model",
]


class E5Embedder:
    def __init__(self, model: SentenceTransformer, model_id: str) -> None:
        dimension = model.get_embedding_dimension()
        if dimension != EMBEDDING_DIMENSION:
            raise ValueError(
                f"model dimension {dimension} != schema dimension {EMBEDDING_DIMENSION}"
            )
        self._model = model
        self._model_id = model_id
        # sentence-transformers mutates the fast tokenizer's truncation/padding state
        # on every call, so concurrent encode() calls from several threads can fail
        # ("Already borrowed"). Inference is serialised; see ADR 0011 for the
        # latency trade-off this implies while a document is being embedded.
        self._lock = threading.Lock()
        # A private copy of the tokenizer for chunk measurement: no truncation, no
        # padding, and no shared mutable state with the model's own tokenizer.
        backend: Tokenizer = model.tokenizer.backend_tokenizer
        self._span_tokenizer = Tokenizer.from_str(backend.to_str())
        self._span_tokenizer.no_truncation()
        self._span_tokenizer.no_padding()

    @property
    def model_id(self) -> str:
        return self._model_id

    @property
    def dimension(self) -> int:
        return EMBEDDING_DIMENSION

    def token_spans(self, text: str) -> list[TokenSpan]:
        encoding = self._span_tokenizer.encode(text, add_special_tokens=False)
        return [(start, end) for start, end in encoding.offsets if end > start]

    def embed_passages(self, texts: Sequence[str]) -> FloatMatrix:
        return self._encode([PASSAGE_PREFIX + text for text in texts])

    def embed_query(self, text: str) -> FloatMatrix:
        vector: FloatMatrix = self._encode([QUERY_PREFIX + text])[0]
        return vector

    def _encode(self, inputs: list[str]) -> FloatMatrix:
        with self._lock, torch.inference_mode():
            vectors = self._model.encode(
                inputs,
                batch_size=max(len(inputs), 1),
                normalize_embeddings=True,
                convert_to_numpy=True,
                show_progress_bar=False,
            )
        assert isinstance(vectors, np.ndarray)  # noqa: S101 - convert_to_numpy=True
        result: FloatMatrix = vectors.astype(np.float32, copy=False)
        return result


def model_id_for(settings: Settings) -> str:
    return f"{settings.embedding_model_name}@{settings.embedding_model_revision}"


def load_e5_embedder(settings: Settings) -> E5Embedder:
    """Load the pinned model from the local cache. Blocking; call via a thread."""
    torch.set_num_threads(settings.embedding_threads)
    model = SentenceTransformer(
        settings.embedding_model_name,
        revision=settings.embedding_model_revision,
        cache_folder=str(settings.embedding_cache_dir) if settings.embedding_cache_dir else None,
        local_files_only=True,
        trust_remote_code=False,
        device="cpu",
    )
    model.eval()
    embedder = E5Embedder(model, model_id_for(settings))
    logger.info(
        "embedding_model_loaded",
        model=embedder.model_id,
        max_seq_length=model.max_seq_length,
        threads=settings.embedding_threads,
    )
    return embedder
