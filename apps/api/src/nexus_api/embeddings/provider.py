"""Owns the embedder's lifecycle: loading in the background, readiness, access.

Loading the model takes a few seconds and ~0.5 GB of RAM. It happens in a
background thread after startup, so liveness is immediate, and readiness reports
``loading`` until it completes (or ``unavailable`` if it failed, e.g. the model
has not been downloaded).
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Literal

import structlog

from nexus_api.core.errors import AppError
from nexus_api.embeddings.base import Embedder

logger = structlog.stdlib.get_logger(__name__)

EmbedderState = Literal["loading", "ready", "failed"]


class EmbeddingModelUnavailableError(AppError):
    status_code = 503
    code = "embedding_model_unavailable"


class EmbedderProvider:
    def __init__(self, loader: Callable[[], Embedder]) -> None:
        self._loader = loader
        self._embedder: Embedder | None = None
        self._state: EmbedderState = "loading"
        self._task: asyncio.Task[None] | None = None

    @classmethod
    def ready(cls, embedder: Embedder) -> EmbedderProvider:
        provider = cls(lambda: embedder)
        provider._embedder = embedder
        provider._state = "ready"
        return provider

    @property
    def state(self) -> EmbedderState:
        return self._state

    def start_loading(self) -> None:
        if self._state == "loading" and self._task is None:
            self._task = asyncio.create_task(self._load(), name="embedding-model-load")

    async def _load(self) -> None:
        try:
            embedder = await asyncio.to_thread(self._loader)
        except Exception as exc:
            # Logged without traceback locals; the most common cause is a model
            # that was never downloaded (`make models`).
            logger.error(
                "embedding_model_load_failed", error_type=type(exc).__name__, error=str(exc)
            )
            self._state = "failed"
            return
        self._embedder = embedder
        self._state = "ready"

    def get(self) -> Embedder:
        if self._embedder is None:
            message = (
                "The embedding model is still loading."
                if self._state == "loading"
                else "The embedding model is not available."
            )
            raise EmbeddingModelUnavailableError(message, headers={"Retry-After": "5"})
        return self._embedder

    async def shutdown(self) -> None:
        if self._task is not None and not self._task.done():
            # The loader thread itself cannot be interrupted; this only stops waiting.
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
