.PHONY: install dev import-data prepare-crests validate-covers validate-data test lint build release-check run

VENV := .venv
PYTHON := $(VENV)/bin/python
PIP := $(VENV)/bin/pip

install:
	python3 -m venv $(VENV)
	$(PIP) install -e '.[dev,importer]'
	npm ci --prefix frontend

dev:
	$(PYTHON) scripts/dev.py

import-data:
	$(PYTHON) scripts/import_football_data.py

prepare-crests:
	$(PYTHON) scripts/prepare_crest_assets.py

validate-covers:
	$(PYTHON) scripts/validate_crest_covers.py

validate-data:
	$(PYTHON) scripts/validate_data.py
	$(PYTHON) scripts/validate_crest_covers.py

test:
	$(PYTHON) -m pytest
	npm test --prefix frontend

lint:
	$(PYTHON) -m ruff check server src scripts tests

build:
	npm run build --prefix frontend

release-check: lint test build validate-data

run: build validate-data
	CREST_QUEST_ENV=production $(PYTHON) -m uvicorn server.app:app --host 0.0.0.0 --port 8000 --workers 1
