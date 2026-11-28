# NEXUS AI developer commands.  Run `make` or `make help` to list them.
#
# Conventions
#   * Backend commands run inside apps/api through uv, with the repository-root
#     .env injected via `uv run --env-file` (the app itself only reads env vars).
#   * Frontend commands run inside apps/web through npm.
#   * Docker commands are scoped to the `nexus` Compose project (see compose.yaml);
#     they never touch other containers or the host PostgreSQL.

SHELL := /bin/bash
.DEFAULT_GOAL := help

API_DIR := apps/api
WEB_DIR := apps/web
ENV_FILE := $(CURDIR)/.env
UV_RUN := cd $(API_DIR) && uv run --env-file $(ENV_FILE)
COMPOSE := docker compose

.PHONY: help
help: ## List available commands
	@awk 'BEGIN {FS = ":.*?## "} /^##@/ {printf "\n\033[1m%s\033[0m\n", substr($$0, 5)} /^[a-zA-Z_-]+:.*?## / {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)

##@ Setup
.PHONY: setup env install
setup: env install ## First-time setup: create .env and install all dependencies

env: ## Create .env from .env.example if it does not exist
	@test -f .env && echo ".env already exists (left unchanged)" || (cp .env.example .env && echo "Created .env from .env.example")

install: ## Install backend (uv, locked) and frontend (npm ci) dependencies
	cd $(API_DIR) && uv sync --locked
	cd $(WEB_DIR) && npm ci

.PHONY: check-env
check-env:
	@test -f $(ENV_FILE) || (echo "Missing .env. Run 'make env' first." && exit 1)

##@ Database (Docker Compose project: nexus)
.PHONY: db-up db-down db-status db-logs db-psql db-reset
db-up: check-env ## Start PostgreSQL + pgvector on 127.0.0.1:5434 and wait until healthy
	$(COMPOSE) up -d --wait postgres

db-down: ## Stop PostgreSQL (data is kept in the nexus_pgdata volume)
	$(COMPOSE) down

db-status: ## Show container status and health
	$(COMPOSE) ps

db-logs: ## Follow PostgreSQL logs
	$(COMPOSE) logs -f postgres

db-psql: ## Open psql inside the container
	$(COMPOSE) exec postgres sh -c 'psql -U "$$POSTGRES_USER" -d "$$POSTGRES_DB"'

db-reset: ## DESTRUCTIVE: delete the nexus_pgdata volume, then start fresh and migrate
	@read -p "Delete all local NEXUS database data? [y/N] " ans && [ "$$ans" = "y" ]
	$(COMPOSE) down --volumes
	$(MAKE) db-up migrate

##@ Migrations (Alembic)
.PHONY: migrate migrate-down migration migrate-status
migrate: check-env ## Apply all migrations (upgrade to head)
	$(UV_RUN) alembic upgrade head

migrate-down: check-env ## Revert the most recent migration
	$(UV_RUN) alembic downgrade -1

migrate-status: check-env ## Show current revision and history
	$(UV_RUN) alembic current --verbose
	$(UV_RUN) alembic history

migration: check-env ## Create a migration: make migration name="add documents table"
	@test -n "$(name)" || (echo 'Usage: make migration name="describe the change"' && exit 1)
	$(UV_RUN) alembic revision --autogenerate -m "$(name)"

##@ Embedding model
.PHONY: models
models: ## Download the pinned embedding model (~490 MB) into the Hugging Face cache
	cd $(API_DIR) && uv run python -m nexus_api.embeddings.download

##@ Run
.PHONY: api web
api: check-env ## Run the API with auto-reload on http://127.0.0.1:8000
	$(UV_RUN) uvicorn nexus_api.main:create_app --factory --reload --host 127.0.0.1 --port 8000 --no-access-log

web: ## Run the web app (dev server) on http://localhost:3000
	cd $(WEB_DIR) && npm run dev

##@ Quality
.PHONY: format lint typecheck test test-unit test-integration test-model test-llm check api-check web-check
format: ## Auto-format backend code (ruff)
	cd $(API_DIR) && uv run ruff format . && uv run ruff check --fix .

lint: ## Lint backend (ruff) and frontend (eslint)
	cd $(API_DIR) && uv run ruff check . && uv run ruff format --check .
	cd $(WEB_DIR) && npm run lint

typecheck: ## Type-check backend (mypy) and frontend (tsc)
	cd $(API_DIR) && uv run mypy
	cd $(WEB_DIR) && npm run typecheck

test-unit: ## Backend unit tests (no database, no model, no API key)
	cd $(API_DIR) && uv run pytest -m "not integration and not model and not llm"

test-integration: check-env ## Backend integration tests (requires `make db-up`; fails if DB is down)
	$(UV_RUN) pytest -m "integration and not model and not llm"

test-model: check-env ## Real embedding model tests (requires `make models` and `make db-up`)
	$(UV_RUN) pytest -m model

test-llm: check-env ## Live Anthropic API tests (needs ANTHROPIC_API_KEY in .env; costs a few cents)
	$(UV_RUN) pytest -m llm

test: check-env ## Backend unit + integration tests (requires `make db-up`; no model, no API)
	$(UV_RUN) pytest -m "not model and not llm"

api-check: ## Backend: lint, format check, mypy, unit tests
	cd $(API_DIR) && uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run pytest -m "not integration and not model and not llm"

web-check: ## Frontend: lint, typecheck, production build
	cd $(WEB_DIR) && npm run check

check: api-check web-check test-integration ## Everything CI runs (requires `make db-up`)
