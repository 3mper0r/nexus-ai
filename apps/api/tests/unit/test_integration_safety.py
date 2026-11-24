"""Regression tests: the integration suite may only ever DROP/CREATE an isolated
``<db>_test`` database, and never under a production configuration."""

from __future__ import annotations

import pytest

from tests.conftest import SettingsFactory
from tests.integration.conftest import UnsafeTestDatabaseError, resolve_test_database_name


def test_derives_isolated_test_database_name(make_settings: SettingsFactory) -> None:
    name = resolve_test_database_name(make_settings(postgres_db="nexus"))

    assert name == "nexus_test"


def test_refuses_production_configuration(make_settings: SettingsFactory) -> None:
    with pytest.raises(UnsafeTestDatabaseError, match="APP_ENV=production"):
        resolve_test_database_name(make_settings(app_env="production"))


@pytest.mark.parametrize(
    "db_name", ['nexus"; DROP DATABASE nexus; --', "Nexus", "1nexus", "x" * 60]
)
def test_refuses_names_unsafe_for_ddl(make_settings: SettingsFactory, db_name: str) -> None:
    with pytest.raises(UnsafeTestDatabaseError, match="unsafe test database name"):
        resolve_test_database_name(make_settings(postgres_db=db_name))
