# ADR 0008: Integration tests: real database, throwaway schema, fail-not-skip

* Status: Accepted (M0, 2026-11-24)

## Decision
* Integration tests carry `pytest.mark.integration`; local runs can deselect them with
  `-m "not integration"` (`make test-unit`).
* When selected and PostgreSQL is unreachable, the session **fails** (`pytest.fail`),
  never skips. CI runs them against a PostgreSQL service and therefore fails if it is absent.
* Each session drops/creates `<POSTGRES_DB>_test`, migrates it with Alembic
  (programmatically, injecting `Settings` into `env.py`), and drops it afterwards.
* Explicit safeguards before any `DROP/CREATE DATABASE` (`resolve_test_database_name`):
  refuse `APP_ENV=production`; the name is always derived as `<POSTGRES_DB>_test`, must differ
  from the configured database, and must be a plain lowercase identifier (it is interpolated
  into DDL). Unit-tested in `tests/unit/test_integration_safety.py`.
* Database connectivity, migrations, session commit/rollback and engine disposal are
  verified against the real server; nothing in that path is mocked.
* Unit tests are hermetic: an autouse fixture removes all `Settings`-backed env vars.

## Consequences
* + A green run means the DB path really works; a missing DB can't produce a false green.
* + Developer data is never touched; migrations are tested from an empty database every run.
* − Integration tests need Docker locally; mitigated by `make test-unit`.
* − Tests share one test database per session; tests clean up what they create.
