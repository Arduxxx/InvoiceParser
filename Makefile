# Works on Linux, macOS and Windows (via GNU make). Activate your virtualenv first,
# or override: make test PYTHON=.venv/bin/python
PYTHON ?= python

.PHONY: install test run lint typecheck cov check

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
