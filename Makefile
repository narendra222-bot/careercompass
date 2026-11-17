.PHONY: setup build-data test lint catalog-check demo api web web-setup help

VENV ?= .venv
PYTHON ?= $(VENV)/bin/python
PIP ?= $(VENV)/bin/pip
PYTEST ?= $(VENV)/bin/pytest
RUFF ?= $(VENV)/bin/ruff
MYPY ?= $(VENV)/bin/mypy

help:
	@echo "Available targets:"
	@echo "  setup         - Initialize virtual environment and install dependencies"
	@echo "  build-data    - Download O*NET 31.0 and build the database and search index"
	@echo "  test          - Run test suite"
	@echo "  lint          - Run ruff linter/formatter check and mypy type checking"
	@echo "  catalog-check - Validate learning catalog (DAG, links, names) and dump target elements"
	@echo "  demo          - Regenerate the 3 sample roadmaps + JSON Schema in docs/"
	@echo "  api           - Run the HTTP API on http://localhost:8000 (docs at /docs)"
	@echo "  web-setup     - Install the web app dependencies (needs Node.js + pnpm)"
	@echo "  web           - Run the web app on http://localhost:3000 (start 'make api' first)"

setup:
	@test -d $(VENV) || python3 -m venv $(VENV)
	$(PIP) install --upgrade pip
	$(PIP) install -r requirements.txt -r requirements-dev.txt

build-data:
	$(PYTHON) scripts/build_data.py

test:
	$(PYTEST) tests/unit

lint:
	$(RUFF) check .
	$(MYPY) careercompass

catalog-check:
	$(PYTEST) -q tests/unit/test_catalog.py tests/unit/test_tech.py
	$(PYTHON) scripts/dump_elements.py > /dev/null

demo:
	$(PYTHON) scripts/export_schema.py

api:
	$(VENV)/bin/uvicorn careercompass.api:app --reload --port 8000

web-setup:
	cd web && pnpm install

web:
	cd web && pnpm dev
