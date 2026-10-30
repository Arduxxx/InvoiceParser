# CLAUDE.md

Guidance for Claude Code (and humans) working in this repository.

## Project

**InvoiceFlow** — Bocconi Software Engineering course project, built by two students.
It reads the invoices of a freelancer or small business, checks they are correct, and flags
suspicious ones (e.g. a known supplier suddenly changing IBAN).

- Project deadline: **27 Nov 2026**. Presentations: 30 Nov – 4 Dec 2026.
- The full roadmap (in Italian) is `InvoiceFlowroadmap.pdf`, kept outside the repo. This file
  summarizes it; ask the user if something here seems out of date.

### Scope

Core — finish and test first:
1. **Import**: FatturaPA XML, including digitally signed `.xml.p7m`, and PDFs from foreign
   suppliers that don't use XML (e.g. Amazon, Google).
2. **Checks**: line totals, VAT and grand total add up; VAT number (partita IVA) and IBAN check
   digits are valid (IBAN = mod 97); dates are consistent. Every check is first written as a
   precise rule derived from the official FatturaPA spec (fatturapa.gov.it).
3. **Duplicates**: same invoice uploaded twice, even with different file names or a differently
   formatted invoice number.
4. **Archive and dashboard**: SQLite database + Streamlit UI (spend by month, supplier, category;
   invoices coming due).

Anti-fraud — the original part, added one rule at a time, each flag must explain *why*:
- IBAN different from the one this supplier usually uses;
- supplier with a near-identical name to a known one but different VAT number or IBAN;
- VAT number that doesn't exist, checked via the EU VIES API (cache results; if VIES is down,
  mark the invoice "not verified" instead of failing);
- amount far outside the usual range for that supplier.

Out of scope: sending to SdI, bank connections, automatic payments, full tax management.

### Work split

- **A**: XML import + database → checks + tests → PDF reading.
- **B**: fake-invoice generator → duplicates + dashboard → anti-fraud.
- Together: final week (21–27 Nov) for measurements and the report.

Ask the user which part they are working on before starting a task.

## Commands

Set up once:

```
python -m venv .venv
# Linux/macOS: source .venv/bin/activate    Windows: .venv\Scripts\activate
make install
```

- `make test` — run pytest
- `make run` — start the Streamlit dashboard
- `make lint` — ruff
- `make typecheck` — mypy (strict)
- `make cov` — tests with coverage
- `make check` — lint + typecheck + test; **must pass before every commit/PR**

Without an activated venv: `make check PYTHON=.venv/bin/python`.

## Layout

- `src/invoiceflow/importers/` — XML, `.p7m`, PDF readers
- `src/invoiceflow/checks/` — arithmetic, VAT number, IBAN, date checks
- `src/invoiceflow/antifraud/` — fraud rules
- `src/invoiceflow/money.py` — money helpers (integer cents / `Decimal`)
- `src/invoiceflow/dashboard.py` — Streamlit entry point
- `tests/unit/` — regular pytest tests; `tests/property/` — hypothesis tests on generated invoices
- `data/` — real and generated invoices; **git-ignored on purpose**, never commit invoices

## Rules (these are graded course requirements)

- **Money is never `float`.** Use integer cents or `Decimal`; round VAT exactly as the FatturaPA
  spec says. Use the helpers in `money.py`.
- **Portable**: must run on Windows and Linux. Use `pathlib`, open files with an explicit
  `encoding=`, no shell-outs to external programs (read `.p7m` with a Python library).
- **Typed**: all code passes `mypy --strict` and `ruff`.
- **Tested**: every check and fraud rule gets unit tests; invariants like
  "sum of lines = taxable amount" get hypothesis property tests over thousands of random invoices.
- **Scales**: duplicate and clone-supplier search must use indexes (e.g. group by supplier and
  amount before comparing), not compare every invoice with every other — it must stay fast at
  100,000 invoices.
- **Parallel**: importing/checking hundreds of PDFs should use multiple cores.
- **Privacy**: real invoices are used only with permission and anonymized; they never go to GitHub.

## Git workflow

- Never commit directly to `main`. Create a branch (e.g. `feature/xml-import`), commit, push,
  open a pull request with `gh pr create`.
- The **other** team member reviews and merges the PR (peer code review is graded).
- Keep PRs small: one feature or one check per PR.

First-time GitHub setup on a new machine:

```
brew install gh        # or the installer from https://cli.github.com
gh auth login          # GitHub.com → HTTPS → yes to authenticate git → login with browser
gh auth setup-git      # lets plain `git push` use the gh login
```

## How we measure success (for the final report)

- Errors found: alter one random field in 1,000 correct invoices, count how many the checks catch.
- Frauds found and false alarms per 100 generated invoices.
- PDF reading: % of fields read correctly on real invoices, before and after the checks.
- Speed: duplicate search on 1k / 10k / 100k invoices, pairwise vs. indexed.
