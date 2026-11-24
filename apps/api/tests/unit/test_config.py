from __future__ import annotations

import pytest
from pydantic import ValidationError

from nexus_api.core.config import Settings
from tests.conftest import SettingsFactory


def test_defaults_are_local_development_values(make_settings: SettingsFactory) -> None:
    settings = make_settings(app_env="local", postgres_port=5434)

    assert settings.app_env == "local"
    assert settings.api_port == 8000
    assert settings.postgres_port == 5434
    assert settings.cors_origins == ["http://localhost:3000"]


def test_password_is_required() -> None:
    with pytest.raises(ValidationError, match="postgres_password"):
        Settings(_env_file=None)


def test_reads_values_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("POSTGRES_PASSWORD", "from-env")
    monkeypatch.setenv("API_PORT", "9001")
    monkeypatch.setenv("CORS_ORIGINS", "http://localhost:3000, https://nexus.example.com")

    settings = Settings()

    assert settings.postgres_password.get_secret_value() == "from-env"
    assert settings.api_port == 9001
    assert settings.cors_origins == ["http://localhost:3000", "https://nexus.example.com"]


@pytest.mark.parametrize(
    "origins",
    ["*", "localhost:3000", "http://localhost:3000/app", "ftp://example.com"],
)
def test_rejects_invalid_cors_origins(make_settings: SettingsFactory, origins: str) -> None:
    with pytest.raises(ValidationError, match="cors_origins"):
        make_settings(cors_origins=origins)


@pytest.mark.parametrize("port", [0, 70000])
def test_rejects_out_of_range_ports(make_settings: SettingsFactory, port: int) -> None:
    with pytest.raises(ValidationError, match="api_port"):
        make_settings(api_port=port)


def test_rejects_unknown_environment(make_settings: SettingsFactory) -> None:
    with pytest.raises(ValidationError, match="app_env"):
        make_settings(app_env="staging")


def test_rejects_non_positive_health_timeout(make_settings: SettingsFactory) -> None:
    with pytest.raises(ValidationError, match="db_health_timeout_seconds"):
        make_settings(db_health_timeout_seconds=0)


def test_production_requires_json_logs(make_settings: SettingsFactory) -> None:
    with pytest.raises(ValidationError, match="LOG_FORMAT must be 'json'"):
        make_settings(app_env="production", log_format="console")


def test_database_url_escapes_and_masks_password(make_settings: SettingsFactory) -> None:
    settings = make_settings(postgres_password="p@ss/word:1", postgres_port=5434)
    url = settings.database_url

    assert url.drivername == "postgresql+asyncpg"
    assert url.password == "p@ss/word:1"
    assert "p@ss" not in str(url)
    assert "p@ss" not in repr(settings)
    assert url.render_as_string(hide_password=False).startswith(
        "postgresql+asyncpg://nexus:p%40ss%2Fword%3A1@127.0.0.1:5434/"
    )
