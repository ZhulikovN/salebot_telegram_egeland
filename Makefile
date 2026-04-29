SHELL:=bash

CHECK_DIRS=./app
TOML_FILES=poetry.lock pyproject.toml
POETRY_EXEC=poetry

## help: print this help message
.PHONY: help
help:
	@echo 'Usage:'
	@sed -n 's/^##//p' ${MAKEFILE_LIST} | column -t -s ':' |  sed -e 's/^/ /'

## format: run isort, black
.PHONY: format
format:
	$(POETRY_EXEC) run isort $(CHECK_DIRS)
	$(POETRY_EXEC) run black $(CHECK_DIRS) --line-length 128
	$(POETRY_EXEC) run toml-sort $(TOML_FILES) -i -a

## lint: flake8, pylint
.PHONY: lint
lint:
	$(POETRY_EXEC) run mypy $(CHECK_DIRS)
	$(POETRY_EXEC) run flake8 $(CHECK_DIRS)
	$(POETRY_EXEC) run pylint $(CHECK_DIRS) 2>/dev/null

## test: run unit tests (without integration tests)
.PHONY: test
test:
	$(POETRY_EXEC) run pytest ./tests -v -m "not integration"

## test-cov: run tests with coverage
.PHONY: test-cov
test-cov:
	$(POETRY_EXEC) run pytest ./tests -vv -m "not integration" --cov=app --cov-report=term-missing

## test-integration: run integration tests (requires .env and real services)
.PHONY: test-integration
test-integration:
	$(POETRY_EXEC) run pytest ./tests -v -m integration

## test-full: run full integration test (all checks with real API and Google Sheets)
.PHONY: test-full
test-full:
	$(POETRY_EXEC) run python tests/test_real_integration.py

## test-all: run all tests including integration
.PHONY: test-all
test-all:
	$(POETRY_EXEC) run pytest ./tests -vv

## dev: run format, lint
.PHONY: dev
dev: format lint
