"""Application configuration loaded from environment variables.

Configuration is read exclusively from the process environment (12-factor). The
developer workflow injects the repository-root ``.env`` file via
``uv run --env-file`` (see the Makefile), so application code never needs to know
where an env file lives on disk.
"""

from __future__ import annotations

from typing import Annotated, Literal
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict
from sqlalchemy.engine import URL

AppEnv = Literal["local", "test", "production"]
LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
LogFormat = Literal["console", "json"]


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

    @model_validator(mode="after")
    def _validate_production(self) -> Settings:
        if self.app_env == "production" and self.log_format != "json":
            raise ValueError("LOG_FORMAT must be 'json' when APP_ENV is 'production'")
        return self

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
