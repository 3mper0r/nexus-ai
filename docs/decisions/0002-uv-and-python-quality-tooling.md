# ADR 0002: uv, Ruff and strict mypy for Python

* Status: Accepted (M0, 2026-11-24)

## Context
Python needs reproducible environments, a lockfile, and fast quality gates
comparable to the npm + ESLint + tsc experience.

## Decision
* **uv** manages the interpreter (3.12.16 via `.python-version`), virtualenv
  (`apps/api/.venv`), dependencies (`pyproject.toml`, PEP 735 `[dependency-groups]`)
  and the cross-platform lockfile `uv.lock`. CI installs with `uv sync --locked`.
* Dependency ranges are **bounded to the current major/minor line** (e.g.
  `sqlalchemy>=2.0.40,<2.1`); exact versions live in `uv.lock`.
* **Ruff** for formatting and linting (pycodestyle, pyflakes, isort, bugbear,
  pyupgrade, naming, simplify, async, bandit-style security, pytest style).
* **mypy `strict = true`** with the Pydantic plugin across `src`, `tests` and `migrations`.
* `src/` layout: the package is only importable once installed, so tests exercise the
  installed package rather than the working directory.

## Consequences
* + One fast tool for env + deps; lockfile-driven CI.
* + Strict typing catches whole classes of bugs before runtime (e.g. it flagged five stale
  `type: ignore` comments during M0). Note that runtime-version issues still need tests:
  a Python-3.13-only constant used on 3.12 was caught by a unit test, not by mypy.
* − Strict mypy needs explicit annotations and occasional overrides (e.g. `asyncpg` ships
  no type information; it's ignored and used only in test fixtures).
* Rejected: Poetry (slower, non-standard dependency groups at the time), pip-tools
  (no interpreter management), Black + isort + Flake8 (three tools where Ruff is one).
* Deviation from "newest": SQLAlchemy is held on the mature 2.0 line rather than 2.1.
