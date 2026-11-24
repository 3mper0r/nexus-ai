"""Unit tests are hermetic: they never see the developer's shell or .env values."""

from __future__ import annotations

import pytest

from nexus_api.core.config import Settings


@pytest.fixture(autouse=True)
def _isolate_settings_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for field_name in Settings.model_fields:
        monkeypatch.delenv(field_name.upper(), raising=False)
