# InvoiceFlow

InvoiceFlow reads the invoices a freelancer or small business receives, checks that each one is
correct, finds duplicates and flags suspicious ones, such as a known supplier suddenly asking to
be paid to a new bank account. A Streamlit dashboard shows spending and payments coming due.

It reads Italian e-invoices (FatturaPA XML, including digitally signed `.xml.p7m`) and Italian
PDF invoices. Written in Python for the Bocconi Software Engineering course (2026), by two
students.

## Setup

Needs Python 3.11 or newer and GNU `make`. It runs on Linux, macOS and Windows.

```
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
make install
```

Activate the virtual environment in every new terminal, or add `PYTHON=.venv/bin/python` to each
`make` command.

## Try it in two minutes

The generator creates realistic fake invoices with planted errors, frauds and duplicates, so you
can try everything without real data:

```
make generate OUT=data/generated/demo N=1000 SEED=42
make import IN=data/generated/demo/invoices CATEGORIES=data/generated/demo/suppliers.csv
make run
```

`make run` opens the dashboard at http://localhost:8501. A menu on the left opens one page at a
time:

- spending by month, supplier and category;
- payments coming due in a chosen period;
- invoices that failed the checks, as a table you can filter by check;
- suspicious invoices, grouped by kind of fraud, each with an explanation;
- invoices uploaded more than once, and which copy is counted;
- one invoice in detail: header, lines, VAT summary, payments and everything found in it;
- supplier categories, which you can set and remove.

You can also drop `.xml`, `.xml.p7m` and `.pdf` files on **Add invoices** in the sidebar. They
are imported and checked on the spot, and a box at the top of the page says what was found in
each one: a duplicate, a suspicious invoice, failed checks, or all clear. The dashboard starts
with an empty archive if none exists yet.

The generator refuses to write into a folder that already holds invoices: choose a new `OUT`.

## Use your own invoices

1. **Anonymize them** (recommended, and required for this course's real invoices):
   ```
   make anonymize ORIGINALS=path/to/your/invoices ANONYMIZED=data/real/xml
   ```
   Originals are only read. XML copies go to `data/real/xml`, PDF copies to `data/real/pdf`.
   Names, VAT numbers, tax codes, IBANs and addresses are replaced by consistent fakes;
   amounts, dates and invoice numbers are kept. The secret key is created in
   `data/anonymize.key`: keep it private.
2. **Import them:** `make import IN=data/real`. Subfolders are included, and importing the same
   files again adds nothing. For a few files, you can instead drop them on **Add invoices** in
   the dashboard sidebar.
3. **Open the dashboard:** `make run`.

After updating the code, delete `data/invoiceflow.sqlite` and import again: check results are
saved at import time.

## Commands

| Command | What it does |
|---|---|
| `make generate OUT= N= SEED=` | Fake invoices in `OUT/invoices/`, with the answer key in `OUT/labels.csv` |
| `make import IN= [DB=] [CATEGORIES=]` | Read `.xml`, `.xml.p7m` and `.pdf` files into the SQLite archive and check them |
| `make run` | Start the dashboard, where you can also drop new invoices |
| `make duplicates [DB=]` | List invoices uploaded more than once |
| `make frauds [DB=]` | List suspicious invoices, with the reason for each |
| `make anonymize ORIGINALS= ANONYMIZED=` | Make anonymized copies of real invoices |
| `python -m invoiceflow.checks.evaluate DIR` | Measure the checks on a generated dataset |
| `python -m invoiceflow.antifraud.evaluate DIR` | Measure the fraud rules on a generated dataset |
| `python -m invoiceflow.checks.schema FILE...` | Validate XML files against the official schema |

The archive is `data/invoiceflow.sqlite` unless you pass `DB=`.

## What it checks

**Correctness:** each rule comes from the FatturaPA specification.
- Line totals equal quantity × unit price.
- Each VAT summary equals its lines, within 1 cent.
- VAT equals taxable amount × rate.
- The grand total equals taxable amounts plus VAT.
- Payments add up to the total, or to the total minus withholding tax.
- VAT number and IBAN check digits are valid.
- Due dates are not before the invoice date.
- XML files are valid against the official schema.

**Duplicates:** the same supplier, date and invoice number, even when the number is written
differently (`2026/007`, `2026-7` and `N. 2026/007` all match).

**Fraud:** these invoices add up correctly but look like a scam.
- **New bank account:** a supplier with at least 3 earlier invoices asks to be paid to an IBAN
  none of them used.
- **Look-alike supplier:** a new supplier whose name is nearly identical to a known one
  (`Rossl` for `Rossi`, `Srl` for `S.r.l.`), but with a different VAT number.
- **Unusual amount:** a total more than 2.5 times the supplier's largest earlier invoice, after
  at least 4 of them.

Invoices that fail a check stay in the spending totals, with a note, so a real cost never
disappears because of a warning.

## Results

Measured on generated data, with seeds that were not used to tune the rules:

| Measurement | Result |
|---|---|
| 1,000 invoices, each with one altered field | 1,000 errors caught |
| 1,000 correct invoices | 0 false alarms |
| 10,000 invoices with planted frauds | 95.3% of frauds caught, 0.05 false alarms per 100 invoices |

On 24 distinct real, anonymized invoices from 3 suppliers there were no false alarms, and every
field of the 18 PDFs that have a matching XML was read correctly. That sample is small, and all
those PDFs use the official SdI layout.

To reproduce the error measurement:

```
python -m invoiceflow.generator --out data/generated/errors --n 1000 --seed 2001 --error-rate 1 --fraud-rate 0 --duplicate-rate 0
python -m invoiceflow.checks.evaluate data/generated/errors
```

## Limits

- **Not built:** checking that VAT numbers exist (EU VIES), and importing on several cores.
- **PDFs:** scans are refused (no OCR), and foreign suppliers such as Amazon or Google are not
  read. Outside the official SdI layout, item lines are not read.
- **Not modelled:** stamp duty (*bollo*) and discounts, so invoices using them can get a warning.
- **Signed files** named `.p7m` without `.xml` are not picked up by the import.
- **Anonymized copies** keep dates, amounts and invoice numbers, and the look-alike supplier rule
  cannot be measured on them.

## Project layout

| Path | Contents |
|---|---|
| `src/invoiceflow/model.py` | The shared description of an invoice, used by every module |
| `src/invoiceflow/money.py` | Money as integer cents or `Decimal`, never `float` |
| `src/invoiceflow/importers/` | XML, `.p7m` and PDF readers, and the folder import |
| `src/invoiceflow/storage.py` | The SQLite archive |
| `src/invoiceflow/checks/` | Correctness checks and the official schema |
| `src/invoiceflow/duplicates.py` | Duplicate search, grouped by key instead of comparing every pair |
| `src/invoiceflow/antifraud/` | The three fraud rules |
| `src/invoiceflow/reports.py`, `dashboard.py` | Dashboard figures, and the Streamlit page |
| `src/invoiceflow/generator/` | Fake invoices with labelled errors, frauds and duplicates |
| `src/invoiceflow/anonymize.py`, `pdf_redact.py` | Anonymizer for XML, `.p7m` and PDF |
| `tests/unit/`, `tests/property/` | pytest tests, and hypothesis tests on thousands of random invoices |

## Development

- `make check` runs ruff, `mypy --strict` and the tests, and must pass before every commit.
- `make cov` shows test coverage.
- Never commit to `main`: work on a branch and open a pull request, which the other team member
  reviews and merges.
- `data/` is git-ignored on purpose. Real invoices, anonymized copies and the anonymizer key
  never go to GitHub.
