# Works on Linux, macOS and Windows (via GNU make). Activate your virtualenv first,
# or override: make test PYTHON=.venv/bin/python
PYTHON ?= python

.PHONY: install test run lint typecheck cov check generate import duplicates

install:
	$(PYTHON) -m pip install -e ".[dev]"

test:
	$(PYTHON) -m pytest

run:
	$(PYTHON) -m streamlit run src/invoiceflow/dashboard.py

lint:
	$(PYTHON) -m ruff check .

typecheck:
	$(PYTHON) -m mypy

cov:
	$(PYTHON) -m pytest --cov --cov-report=term-missing

check: lint typecheck test

# Fake invoices + labels.csv; e.g. make generate OUT=data/generated/run1 N=1000 SEED=42
OUT ?= data/generated/default
N ?= 1000
SEED ?= 0
generate:
	$(PYTHON) -m invoiceflow.generator --out $(OUT) --n $(N) --seed $(SEED)

# Import a folder of invoices into the archive; e.g.
# make import IN=data/generated/run1/invoices CATEGORIES=data/generated/run1/suppliers.csv
DB ?= data/invoiceflow.sqlite
IN ?= data/real
CATEGORIES ?=
import:
	$(PYTHON) -m invoiceflow.importers.ingest $(IN) --db $(DB) $(if $(CATEGORIES),--categories $(CATEGORIES))

duplicates:
	$(PYTHON) -m invoiceflow.duplicates --db $(DB)
