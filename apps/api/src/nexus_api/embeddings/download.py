"""Download the pinned embedding model into the local Hugging Face cache.

Usage: ``make models`` (runs ``python -m nexus_api.embeddings.download``).
This is the only code path that contacts the Hugging Face Hub.
"""

from __future__ import annotations

from huggingface_hub import snapshot_download
from pydantic import SecretStr

from nexus_api.core.config import Settings
from nexus_api.embeddings.e5 import MODEL_FILES


def main() -> None:
    # Only the embedding settings matter here; downloading must not require
    # database credentials (e.g. in CI before any database exists).
    settings = Settings(postgres_password=SecretStr("unused-for-model-download"))
    path = snapshot_download(
        settings.embedding_model_name,
        revision=settings.embedding_model_revision,
        cache_dir=settings.embedding_cache_dir,
        allow_patterns=MODEL_FILES,
    )
    print(f"{settings.embedding_model_name}@{settings.embedding_model_revision} -> {path}")


if __name__ == "__main__":
    main()
