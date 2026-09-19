.DEFAULT_GOAL := help
UV ?= uv

.PHONY: help install fmt lint typecheck test test-e2e verify audit evals up down build \
	demo-bank discover discover-offline replay

help: ## Show available commands
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-16s %s\n", $$1, $$2}'

install: ## Install deps, git hooks, and Playwright Chromium
	$(UV) sync
	$(UV) run pre-commit install
	$(UV) run playwright install chromium

fmt: ## Auto-format and apply safe lint fixes
	$(UV) run ruff format .
	$(UV) run ruff check --fix .

lint: ## Check formatting and lint
	$(UV) run ruff format --check .
	$(UV) run ruff check .

typecheck: ## Static type check (mypy --strict)
	$(UV) run mypy src tests scripts

test: ## Unit + integration tests
	$(UV) run pytest

test-e2e: ## End-to-end tests (real server + Chromium)
	$(UV) run pytest -m e2e

verify: lint typecheck test ## Standard pre-push checks: lint -> typecheck -> tests

audit: ## Scan dependencies for known vulnerabilities
	$(UV) export --quiet --frozen --no-hashes --no-emit-project -o .audit-requirements.txt
	$(UV) run pip-audit --strict -r .audit-requirements.txt; status=$$?; rm -f .audit-requirements.txt; exit $$status

evals: ## Model-behavior evals (see evals/README.md)
	@echo "No eval runner yet: see evals/README.md" && exit 1

MEMBER ?= 12345
TASK ?= catalog/tasks/member_savings_balance.json
CAPABILITY ?= harbor.member.savings_balance

demo-bank: ## Run the legacy-style demo target on :8001
	$(UV) run assessments demo-bank

discover: ## LLM discovery run (needs ANTHROPIC_API_KEY): TASK=..., MEMBER=...
	$(UV) run assessments discover $(TASK) --example member_id=$(MEMBER)

discover-offline: ## Same pipeline with the scripted stand-in (no API key)
	$(UV) run assessments discover $(TASK) --example member_id=$(MEMBER) --decider offline

replay: ## Deterministic replay: CAPABILITY=..., MEMBER=...
	$(UV) run assessments replay $(CAPABILITY) --param member_id=$(MEMBER)

up: ## Build and run the service in Docker
	docker compose up --build

down: ## Stop Docker services
	docker compose down

build: ## Build the Docker image
	docker compose build
