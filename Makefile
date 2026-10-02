# ---------------------------------------------------------------------------
# AgentPay task runner
# ---------------------------------------------------------------------------
# Every command a developer or a judge needs to reproduce the demo lives here,
# so the README can point at `make <target>` instead of a wall of shell.
# ---------------------------------------------------------------------------

.DEFAULT_GOAL := help
SHELL := /bin/sh

PY      ?= python
VENV    ?= .venv
COMPOSE ?= docker compose

ifeq ($(OS),Windows_NT)
	VENV_BIN := $(VENV)/Scripts
else
	VENV_BIN := $(VENV)/bin
endif

.PHONY: help
help: ## Show this help
	@grep -hE '^[a-zA-Z0-9_-]+:.*?## ' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-22s\033[0m %s\n", $$1, $$2}'

# --- Setup ----------------------------------------------------------------

.PHONY: venv
venv: ## Create the local virtualenv and install everything
	$(PY) -m venv $(VENV)
	$(VENV_BIN)/python -m pip install --upgrade pip
	$(VENV_BIN)/python -m pip install -e ".[dev]"

.PHONY: env
env: ## Create .env from the template if it does not exist
	$(VENV_BIN)/python -c "import pathlib,shutil; p=pathlib.Path('.env'); p.exists() or (shutil.copy('.env.example', '.env'), print('created .env from .env.example'))"

# --- Stack ----------------------------------------------------------------

.PHONY: up
up: env ## Start the full stack (api, worker, web, postgres, redis)
	$(COMPOSE) up -d --build
	@echo ""
	@echo "  api    http://localhost:8000/health"
	@echo "  docs   http://localhost:8000/docs"
	@echo "  web    http://localhost:3000"

.PHONY: down
down: ## Stop the stack, keeping volumes
	$(COMPOSE) down

.PHONY: nuke
nuke: ## Stop the stack AND delete its volumes (destroys local data)
	$(COMPOSE) down -v

.PHONY: logs
logs: ## Tail api and worker logs
	$(COMPOSE) logs -f api worker

.PHONY: ps
ps: ## Show service status
	$(COMPOSE) ps

.PHONY: api
api: ## Run the API on the host with reload
	$(VENV_BIN)/uvicorn apps.api.main:app --reload --host 127.0.0.1 --port 8000

.PHONY: worker
worker: ## Run the worker on the host
	$(VENV_BIN)/python -m apps.worker.main

# --- Database -------------------------------------------------------------

.PHONY: migrate
migrate: ## Apply all migrations
	$(VENV_BIN)/alembic -c infra/migrations/alembic.ini upgrade head

.PHONY: downgrade
downgrade: ## Roll back one migration
	$(VENV_BIN)/alembic -c infra/migrations/alembic.ini downgrade -1

.PHONY: revision
revision: ## Autogenerate a migration: make revision m="add offers"
	$(VENV_BIN)/alembic -c infra/migrations/alembic.ini revision --autogenerate -m "$(m)"

.PHONY: seed
seed: ## Seed the demo merchant, buyer policy, and published catalog
	$(VENV_BIN)/python -m apps.worker.seed_catalog

.PHONY: seed-operator
seed-operator: ## Create the first console login. EMAIL=... make seed-operator (prompts for the password)
	@test -n "$(EMAIL)" || { echo "usage: EMAIL=admin@example.com make seed-operator"; exit 2; }
	$(VENV_BIN)/python -m apps.worker.seed_operator --email "$(EMAIL)"

.PHONY: reset-operator
reset-operator: ## Set a new password for an existing console login. EMAIL=... make reset-operator
	@test -n "$(EMAIL)" || { echo "usage: EMAIL=admin@example.com make reset-operator"; exit 2; }
	$(VENV_BIN)/python -m apps.worker.reset_operator_password --email "$(EMAIL)"

# --- Catalog pipeline -----------------------------------------------------


.PHONY: sample-data
sample-data: ## Generate synthetic sample data for pipeline development
	$(VENV_BIN)/python -m pipeline.sample_data

.PHONY: catalog
catalog: ## Run all six pipeline stages (honours MAX_LINES_DEBUG)
	$(VENV_BIN)/python -m pipeline.build_catalog all

.PHONY: catalog-demo
catalog-demo: ## Generate sample data then run the full pipeline (no download needed)
	AGENTPAY_RAW_DIR=./data/raw $(VENV_BIN)/python -m pipeline.sample_data
	AGENTPAY_RAW_DIR=./data/raw MAX_LINES_DEBUG=100 $(VENV_BIN)/python -m pipeline.build_catalog all

.PHONY: catalog-report
catalog-report: ## Recompute and print the catalog health report
	$(VENV_BIN)/python -m pipeline.build_catalog report

# --- Quality gates --------------------------------------------------------

.PHONY: test
test: ## Run the unit suite (no Docker, no credentials needed)
	$(VENV_BIN)/python -m pytest tests/unit -q

.PHONY: test-all
test-all: ## Run every suite including integration and contract
	$(VENV_BIN)/python -m pytest -q

.PHONY: test-integration
test-integration: ## Smoke the running stack (skips cleanly without Docker)
	$(VENV_BIN)/python -m pytest tests/integration -m integration -q

.PHONY: test-contract
test-contract: ## Run the external-buyer contract suite
	$(VENV_BIN)/python -m pytest tests/contract -q

.PHONY: test-security
test-security: ## Run the security suite
	$(VENV_BIN)/python -m pytest tests/security -q

.PHONY: lint
lint: ## Ruff, mypy, and the architecture boundary checks
	$(VENV_BIN)/ruff check .
	$(VENV_BIN)/ruff format --check .
	$(VENV_BIN)/mypy apps packages services pipeline
	$(VENV_BIN)/lint-imports

.PHONY: fmt
fmt: ## Apply formatting and safe autofixes
	$(VENV_BIN)/ruff check --fix .
	$(VENV_BIN)/ruff format .

.PHONY: web-build
web-build: ## Build Next.js production web app
	cd apps/web && npm run build

.PHONY: web-lint
web-lint: ## Run Next.js linter
	cd apps/web && npm run lint

.PHONY: web-typecheck
web-typecheck: ## Typecheck the Next.js app (no emit)
	cd apps/web && npm run typecheck

.PHONY: web-install
web-install: ## Reproducible install of the web app's dependencies
	cd apps/web && npm ci

.PHONY: test-e2e
test-e2e: ## Run the Tier 1-4 requirement-driven e2e suites
	$(VENV_BIN)/python -m pytest tests/e2e -q

.PHONY: test-chaos
test-chaos: ## Run the concurrency chaos and state-invariant suite
	$(VENV_BIN)/python -m pytest tests/chaos -q

.PHONY: test-operations
test-operations: ## Run the backup/restore operations-script suite
	$(VENV_BIN)/python -m pytest tests/operations -q

.PHONY: buyer-scenario
buyer-scenario: ## Run the external autonomous buyer against a running gateway
	PYTHONPATH=buyer-agent $(VENV_BIN)/python -m buyer_agent.scenario \
		--base-url $${AGENTPAY_BASE_URL:-http://localhost:8000} \
		--api-key $${AGENTPAY_BUYER_API_KEY}

.PHONY: check
check: lint test test-contract test-security test-e2e test-chaos test-operations ## Everything CI runs on a pull request

.PHONY: check-all
check-all: check web-build web-lint web-typecheck ## Full verification: backend, contract, security, e2e, chaos, frontend

