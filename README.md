# InvoiceFlow

Reads invoices of a freelancer or small business (FatturaPA XML, including signed `.xml.p7m`, and
foreign-supplier PDFs), checks they are correct, detects duplicates and flags suspicious ones
(e.g. a known supplier suddenly changing IBAN).

## Quick start

```
python -m venv .venv
# Linux/macOS: source .venv/bin/activate    Windows: .venv\Scripts\activate
make install
make test
make run
```

Other targets: `make lint`, `make typecheck`, `make cov`, `make check` (all of the above).

## Layout

- `src/invoiceflow/importers/` – XML / p7m / PDF readers
- `src/invoiceflow/checks/` – arithmetic, VAT number, IBAN, date checks
- `src/invoiceflow/antifraud/` – IBAN change, clone suppliers, VIES, outlier amounts
- `src/invoiceflow/money.py` – integer cents / `Decimal` helpers (no floats)
- `src/invoiceflow/dashboard.py` – Streamlit UI
- `tests/unit/`, `tests/property/` – pytest and generated-data tests
- `data/real/` – anonymized real invoices (`data/` is git-ignored, so invoices never reach GitHub)
- `data/generated/` – output of the fake-invoice generator
