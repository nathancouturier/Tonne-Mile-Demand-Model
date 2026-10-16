# One command rebuilds everything: make all
PYTHON ?= python
VENV ?= .venv
PIP := $(VENV)/bin/pip
PY := $(VENV)/bin/python

.PHONY: all setup ingest validate transform model export site report test lint format typecheck clean-processed clean-all screenshots

all: ingest validate transform model export report

setup:
	$(PYTHON) -m venv $(VENV)
	$(PIP) install --upgrade pip
	$(PIP) install -e ".[dev]"

ingest:
	$(PY) -m tmdm.cli ingest

validate:
	$(PY) -m tmdm.cli validate

transform:
	$(PY) -m tmdm.cli transform

model:
	$(PY) -m tmdm.cli model

export:
	$(PY) -m tmdm.cli export

report:
	$(PY) -m tmdm.cli report

test:
	$(PY) -m pytest

lint:
	$(VENV)/bin/ruff check .
	$(VENV)/bin/ruff format --check .

format:
	$(VENV)/bin/ruff format .
	$(VENV)/bin/ruff check --fix .

typecheck:
	$(VENV)/bin/mypy

screenshots:
	$(PY) scripts/screenshots.py

# Remove derived outputs but keep raw downloads (which require network access)
clean-processed:
	rm -rf data/processed/* site/data/* docs/figures/*

# Remove everything that the pipeline can regenerate, including raw downloads
clean-all: clean-processed
	rm -rf data/raw/*
