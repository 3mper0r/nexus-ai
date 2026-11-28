"""Application configuration loaded from environment variables.

Configuration is read exclusively from the process environment (12-factor). The
developer workflow injects the repository-root ``.env`` file via
``uv run --env-file`` (see the Makefile), so application code never needs to know
where an env file lives on disk.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Annotated, Literal
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict
from sqlalchemy.engine import URL

AppEnv = Literal["local", "test", "production"]
LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
LogFormat = Literal["console", "json"]
LlmEffort = Literal["low", "medium", "high", "xhigh", "max"]

# The embedding model accepts at most 512 tokens. A chunk is embedded as
# "passage: " + text plus <s> and </s>, so chunks must leave room for those.
EMBEDDING_MODEL_MAX_TOKENS = 512
EMBEDDING_RESERVED_TOKENS = 16
_COMMIT_HASH = re.compile(r"^[0-9a-f]{40}$")


class Settings(BaseSettings):
    """Validated, immutable application settings."""

    model_config = SettingsConfigDict(
        env_file=None,
        case_sensitive=False,
        extra="ignore",
        frozen=True,
    )

    # --- Application ---------------------------------------------------------
    app_env: AppEnv = "local"
    log_level: LogLevel = "INFO"
    log_format: LogFormat = "console"
    api_host: str = "127.0.0.1"
    api_port: int = Field(default=8000, ge=1, le=65535)
    cors_origins: Annotated[list[str], NoDecode] = ["http://localhost:3000"]

    # --- Database ------------------------------------------------------------
    postgres_host: str = "127.0.0.1"
    postgres_port: int = Field(default=5434, ge=1, le=65535)
    postgres_user: str = Field(default="nexus", min_length=1)
    postgres_password: SecretStr
    postgres_db: str = Field(default="nexus", min_length=1)
    db_pool_size: int = Field(default=5, ge=1, le=50)
    db_max_overflow: int = Field(default=5, ge=0, le=50)
    db_connect_timeout_seconds: float = Field(default=5.0, gt=0, le=60)
    db_health_timeout_seconds: float = Field(default=2.0, gt=0, le=30)

    # --- Uploads -------------------------------------------------------------
    max_upload_bytes: int = Field(default=20 * 1024 * 1024, ge=1024, le=200 * 1024 * 1024)
    max_pdf_pages: int = Field(default=500, ge=1, le=5000)
    # Extracted-text caps. A small, highly compressed PDF can expand to millions of
    # characters per page (pypdf only caps each stream at 75 MB); without these
    # limits one upload could exhaust memory and occupy ingestion for days.
    # A dense printed page holds ~3,000-6,000 characters.
    max_page_chars: int = Field(default=100_000, ge=1_000, le=10_000_000)
    max_document_chars: int = Field(default=5_000_000, ge=10_000, le=100_000_000)

    # --- Embeddings ----------------------------------------------------------
    embedding_model_name: str = Field(default="intfloat/multilingual-e5-small", min_length=1)
    # A commit hash, never a branch: model repositories are mutable.
    embedding_model_revision: str = "614241f622f53c4eeff9890bdc4f31cfecc418b3"
    # None means the Hugging Face default cache (~/.cache/huggingface/hub).
    embedding_cache_dir: Path | None = None
    embedding_batch_size: int = Field(default=16, ge=1, le=256)
    embedding_threads: int = Field(default=4, ge=1, le=64)

    # --- Chunking --------------------------------------------------------------
    chunk_tokens: int = Field(default=400, ge=32)
    chunk_overlap_tokens: int = Field(default=60, ge=0)

    # --- Ingestion queue -------------------------------------------------------
    ingest_max_concurrency: int = Field(default=1, ge=1, le=8)
    # Accepted but unfinished documents (queued + running). Bounds memory at
    # roughly max_upload_bytes * ingest_queue_capacity.
    ingest_queue_capacity: int = Field(default=8, ge=1, le=100)

    # --- Search ------------------------------------------------------------------
    search_top_k_max: int = Field(default=20, ge=1, le=100)
    search_ef_search: int = Field(default=64, ge=1, le=1000)

    # --- Grounded answers (Claude) ----------------------------------------------
    # Optional: without a key, search keeps working and /chat explains the setup.
    anthropic_api_key: SecretStr | None = None
    llm_model: str = "claude-sonnet-5-5"
    llm_effort: LlmEffort = "low"
    llm_max_output_tokens: int = Field(default=2048, ge=256, le=64_000)
    llm_daily_budget_usd: float = Field(default=1.0, ge=0, le=1000)
    llm_max_concurrent_generations: int = Field(default=4, ge=1, le=32)
    llm_timeout_seconds: float = Field(default=120.0, gt=0, le=600)
    llm_max_retries: int = Field(default=2, ge=0, le=5)

    # --- Retrieval-augmented generation -------------------------------------------
    rag_top_k: int = Field(default=8, ge=1, le=20)
    rag_context_char_budget: int = Field(default=24_000, ge=1_000, le=200_000)
    rag_history_turns: int = Field(default=4, ge=0, le=20)

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_cors_origins(cls, value: object) -> object:
        """Accept a comma-separated string from the environment."""
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    @field_validator("cors_origins")
    @classmethod
    def _validate_cors_origins(cls, origins: list[str]) -> list[str]:
        for origin in origins:
            if origin == "*":
                raise ValueError("wildcard CORS origin is not allowed; list origins explicitly")
            parts = urlsplit(origin)
            if parts.scheme not in {"http", "https"} or not parts.netloc:
                raise ValueError(f"invalid CORS origin {origin!r}: expected scheme://host[:port]")
            if parts.path or parts.query or parts.fragment:
                raise ValueError(f"invalid CORS origin {origin!r}: must not contain a path")
        return origins

    @field_validator("embedding_model_revision")
    @classmethod
    def _validate_revision(cls, revision: str) -> str:
        if not _COMMIT_HASH.fullmatch(revision):
            raise ValueError("EMBEDDING_MODEL_REVISION must be a 40-character commit hash")
        return revision

    @model_validator(mode="after")
    def _validate_production(self) -> Settings:
        if self.app_env == "production" and self.log_format != "json":
            raise ValueError("LOG_FORMAT must be 'json' when APP_ENV is 'production'")
        return self

    @model_validator(mode="after")
    def _validate_chunking(self) -> Settings:
        limit = EMBEDDING_MODEL_MAX_TOKENS - EMBEDDING_RESERVED_TOKENS
        if self.chunk_tokens > limit:
            raise ValueError(f"CHUNK_TOKENS must be <= {limit} (model limit minus prefix)")
        if self.chunk_overlap_tokens >= self.chunk_tokens // 2:
            raise ValueError("CHUNK_OVERLAP_TOKENS must be less than half of CHUNK_TOKENS")
        if self.search_top_k_max > self.search_ef_search:
            raise ValueError("SEARCH_EF_SEARCH must be >= SEARCH_TOP_K_MAX")
        if self.rag_top_k > self.search_top_k_max:
            raise ValueError("RAG_TOP_K must be <= SEARCH_TOP_K_MAX")
        return self

    @model_validator(mode="after")
    def _validate_llm_model(self) -> Settings:
        # Imported here: pricing is a leaf module, config must not import app code at load.
        from nexus_api.llm.pricing import PRICES

        if self.llm_model not in PRICES:
            known = ", ".join(sorted(PRICES))
            raise ValueError(
                f"LLM_MODEL {self.llm_model!r} has no price entry (known: {known}); "
                "budget enforcement needs one"
            )
        return self

    @property
    def llm_configured(self) -> bool:
        key = self.anthropic_api_key
        return key is not None and bool(key.get_secret_value().strip())

    @property
    def database_url(self) -> URL:
        """SQLAlchemy URL for the asyncpg driver.

        ``URL`` escapes special characters in credentials and masks the password in
        its ``repr``/``str``, so it is safe to log accidentally.
        """
        return URL.create(
            drivername="postgresql+asyncpg",
            username=self.postgres_user,
            password=self.postgres_password.get_secret_value(),
            host=self.postgres_host,
            port=self.postgres_port,
            database=self.postgres_db,
        )

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"
