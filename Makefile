.PHONY: setup build-data test lint catalog-check demo help

VENV ?= .venv
PYTHON ?= $(VENV)/bin/python
PIP ?= $(VENV)/bin/pip
PYTEST ?= $(VENV)/bin/pytest
RUFF ?= $(VENV)/bin/ruff
MYPY ?= $(VENV)/bin/mypy

help:
	@echo "Available targets:"
	@echo "  setup         - Initialize virtual environment and install dependencies"
	@echo "  build-data    - Build O*NET database and search indexes (not implemented yet)"
	@echo "  test          - Run test suite"
	@echo "  lint          - Run ruff linter/formatter check and mypy type checking"
	@echo "  catalog-check - Validate O*NET data catalog integrity (not implemented yet)"
	@echo "  demo          - Run demo career roadmap pipeline (not implemented yet)"

setup:
	@test -d $(VENV) || python3 -m venv $(VENV)
	$(PIP) install --upgrade pip
	$(PIP) install -r requirements.txt -r requirements-dev.txt

build-data:
	@echo "build-data: not implemented yet"

test:
	$(PYTEST) tests/unit

lint:
	$(RUFF) check .
	$(MYPY) careercompass

catalog-check:
	@echo "catalog-check: not implemented yet"

demo:
	@echo "demo: not implemented yet"
