# Author: ai-circus-framework contributors. Scaffolded from ai-circus-template.

VENV_DIR      := .venv
.DEFAULT_GOAL := help

# Export .env variables (includes SSL_CERT_FILE if configured)
ifneq (,$(wildcard .env))
    include .env
    export
endif

CYAN  := $(shell tput setaf 6 2>/dev/null)
RESET := $(shell tput sgr0 2>/dev/null)

.PHONY: help setup install check update qa ssl-check test unused-packages all build clean zip \
        generate-data-model run hello-world run-container build-container build-container-clean

# ── Help ──────────────────────────────────────────────────────────────────────

help: ## Show this help message
	@grep -hE '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | \
		awk 'BEGIN {FS=":.*?## "}; {printf "  $(CYAN)%-22s$(RESET) %s\n", $$1, $$2}'

# ── Bootstrap ─────────────────────────────────────────────────────────────────

setup: ## Complete setup: venv, .env, generate settings, and verify environment
	@echo "🚀 Starting complete setup..."
	@if [ ! -f .env ] && [ -f .env.example ]; then echo "📝 Creating .env from .env.example..."; cp .env.example .env; fi
	@uv sync -q && uv run pre-commit install >/dev/null || { echo "❌ setup failed"; exit 1; }
	@PYTHONWARNINGS="ignore" $(MAKE) --no-print-directory generate-data-model && uv run python check_full_env.py || { echo "⚠️  Environment check found issues."; }
	@echo "✓ Complete setup finished!"

install: ## Sync deps and install pre-commit hooks (run after cloning)
	@uv sync -q  || { echo "❌ uv sync failed."; exit 1; }
	@uv run pre-commit install >/dev/null || { echo "❌ pre-commit install failed."; exit 1; }
	@echo "✓ install complete"

check: qa test ## Verify code quality and run tests
	@echo "✓ Check complete"

update: ## Upgrade lockfile, sync deps & update pre-commit hooks
	@uv lock --upgrade            || { echo "❌ uv lock upgrade failed."; exit 1; }
	@uv sync -q  || { echo "❌ uv sync failed."; exit 1; }
	@uv run pre-commit autoupdate || { echo "❌ pre-commit autoupdate failed."; exit 1; }
	@$(MAKE) --no-print-directory generate-data-model
	@echo "✓ update complete"

# ── Dev workflow ──────────────────────────────────────────────────────────────

generate-data-model: ## Generate Pydantic data model from settings.yaml
	@uv run llm-gateway-generate-data-model && uv run ruff format src/llm_gateway/data_model.py

ssl-check: ## Detect and configure SSL CA bundle (for networks with SSL inspection)
	@uv run python scripts/ssl_setup.py

qa: ## Run all pre-commit checks (ruff, ruff-format, etc.) plus config drift check
	@set -a && [ -f .env ] && . ./.env; uv run pre-commit run --all-files || { echo "↻ Autofixers modified files — re-running pre-commit to verify..."; uv run pre-commit run --all-files || { echo "❌ qa failed."; exit 1; }; }
	@uv run llm-gateway-config-drift-check || { echo "❌ qa failed: settings.yaml/data_model.py drift."; exit 1; }
	@echo "✓ qa complete"

test: ## Run test suite
	@uv run pytest -v --tb=short --disable-warnings --maxfail=1 || { echo "❌ tests failed."; exit 1; }

unused-packages: ## Detect unused packages (deptry)
	@uv run deptry src

# ── Build / release ───────────────────────────────────────────────────────────

all: clean setup check run ## Full end-to-end verification: clean, setup, check, and run

build: ## Build the package
	@uv build || { echo "❌ build failed."; exit 1; }
	@echo "✓ build complete"

build-container: ## Build the Docker image (uses layer cache)
	@DOCKER_BUILDKIT=1 docker build -t llm-gateway .

build-container-clean: ## Force full rebuild of Docker image (no cache)
	@DOCKER_BUILDKIT=1 docker build --no-cache -t llm-gateway .

run-container: build-container ## Build (cached) and run the Docker container
	@[ -f .env ] && docker run --rm -it --env-file .env llm-gateway || docker run --rm -it llm-gateway

clean: ## Remove build artifacts, caches, and .venv (with .env backup)
	@python3 scripts/clean.py

zip: ## Zip git-tracked files into project.zip
	@git archive --format=zip --output=project.zip HEAD || { echo "❌ zip failed."; exit 1; }
	@echo "✓ project zipped"

# ── App & tools ───────────────────────────────────────────────────────────────

run: ## Run the main application (app.py)
	@uv run llm-gateway-app

hello-world: ## Run hello world demonstration tool
	@uv run llm-gateway-hello-world

