# Ampule Chamber root Makefile.

SHELL := /usr/bin/env bash
.SHELLFLAGS := -euo pipefail -c

UV ?= uv
PYTHON ?= python

.DEFAULT_GOAL := help

.PHONY: help
help: ## Show this help
	@grep -E '^[a-zA-Z0-9_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-22s\033[0m %s\n", $$1, $$2}'

.PHONY: sync
sync: ## Sync project and development dependencies
	$(UV) sync --group dev

.PHONY: format
format: ## Check Python formatting
	$(UV) run ruff format --check chamber scripts tests

.PHONY: format-fix
format-fix: ## Format Python code in place
	$(UV) run ruff format chamber scripts tests

.PHONY: lint
lint: ## Run Python lint checks
	$(UV) run ruff check chamber scripts tests

.PHONY: lint-fix
lint-fix: ## Fix auto-fixable Python lint issues
	$(UV) run ruff check --fix chamber scripts tests

.PHONY: typecheck
typecheck: ## Run Python static type checks
	$(UV) run ty check chamber scripts tests

.PHONY: test
test: ## Run Python tests
	$(UV) run $(PYTHON) -m unittest discover -s tests

.PHONY: coverage
coverage: ## Run tests with coverage threshold
	$(UV) run coverage run -m unittest discover -s tests
	$(UV) run coverage report

.PHONY: validate-scenarios
validate-scenarios: ## Validate scenario YAML files
	$(UV) run $(PYTHON) scripts/validate_scenarios.py

.PHONY: docs
docs: ## Build public documentation with MkDocs
	$(UV) run mkdocs build --strict

.PHONY: validate-release
validate-release: ## Validate package, changelog, and release metadata
	$(UV) run $(PYTHON) scripts/validate_release_metadata.py

.PHONY: build
build: ## Build source distribution and wheel
	$(UV) build

.PHONY: check
check: ## Run format, lint, typecheck, tests, coverage, scenario validation, and build
	$(MAKE) format
	$(MAKE) lint
	$(MAKE) typecheck
	$(MAKE) test
	$(MAKE) coverage
	$(MAKE) validate-scenarios
	$(MAKE) validate-release
	$(MAKE) docs
	$(MAKE) build

.PHONY: clean
clean: ## Remove local build and coverage artifacts
	rm -rf dist build site *.egg-info .coverage htmlcov
