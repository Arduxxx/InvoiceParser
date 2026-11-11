"""SQLite archive of imported invoices.

Amounts are stored as integer cents; quantities, unit prices and VAT rates as text, so that
a Decimal goes in and comes out exactly.

Loading the same file twice does not duplicate anything: every invoice remembers the
SHA-256 of the file it came from and its position in that file. The same invoice arriving
in a *different* file is stored again on purpose: finding those duplicates is a separate
check, which needs to see both copies.
"""

import sqlite3
from pathlib import Path

from invoiceflow.importers.model import Invoice, Party

SCHEMA = """
CREATE TABLE IF NOT EXISTS suppliers (
    id INTEGER PRIMARY KEY,
    vat_country TEXT NOT NULL,
    vat_number TEXT NOT NULL,
    name TEXT NOT NULL,
    fiscal_code TEXT,
    UNIQUE (vat_country, vat_number)
);
CREATE TABLE IF NOT EXISTS invoices (
    id INTEGER PRIMARY KEY,
    supplier_id INTEGER NOT NULL REFERENCES suppliers (id),
    document_type TEXT NOT NULL,
    number TEXT NOT NULL,
    issue_date TEXT NOT NULL,
    currency TEXT NOT NULL,
    total_cents INTEGER,
    customer_name TEXT NOT NULL,
    customer_vat_number TEXT,
    customer_fiscal_code TEXT,
    source_file TEXT NOT NULL,
    source_sha256 TEXT NOT NULL,
    position_in_file INTEGER NOT NULL,
    UNIQUE (source_sha256, position_in_file)
);
CREATE TABLE IF NOT EXISTS lines (
    invoice_id INTEGER NOT NULL REFERENCES invoices (id),
    number INTEGER NOT NULL,
    description TEXT NOT NULL,
    quantity TEXT,
    unit_price TEXT NOT NULL,
    total_cents INTEGER NOT NULL,
    vat_rate TEXT NOT NULL,
    nature TEXT
);
CREATE TABLE IF NOT EXISTS vat_summaries (
    invoice_id INTEGER NOT NULL REFERENCES invoices (id),
    vat_rate TEXT NOT NULL,
    nature TEXT,
    taxable_cents INTEGER NOT NULL,
    tax_cents INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS payments (
    invoice_id INTEGER NOT NULL REFERENCES invoices (id),
    method TEXT NOT NULL,
    amount_cents INTEGER NOT NULL,
    due_date TEXT,
    iban TEXT
);
CREATE INDEX IF NOT EXISTS invoices_by_supplier ON invoices (supplier_id, total_cents);
"""


def connect(path: Path) -> sqlite3.Connection:
    """Open (and if needed create) the archive."""
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA foreign_keys = ON")
    connection.executescript(SCHEMA)
    return connection


def save_invoice(
    connection: sqlite3.Connection,
    invoice: Invoice,
    source_file: str,
    source_sha256: str,
    position_in_file: int,
) -> bool:
    """Store one invoice with its lines, VAT summaries and payments, all or nothing.
    Returns False if it was already there (same file, same position)."""
    with connection:  # one transaction: commit at the end, rollback on any error
        already = connection.execute(
            "SELECT 1 FROM invoices WHERE source_sha256 = ? AND position_in_file = ?",
            (source_sha256, position_in_file),
        ).fetchone()
        if already:
            return False
        supplier_id = _supplier_id(connection, invoice.supplier)
        customer = invoice.customer
        cursor = connection.execute(
            "INSERT INTO invoices (supplier_id, document_type, number, issue_date, currency,"
            " total_cents, customer_name, customer_vat_number, customer_fiscal_code,"
            " source_file, source_sha256, position_in_file)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                supplier_id,
                invoice.document_type,
                invoice.number,
                invoice.issue_date.isoformat(),
                invoice.currency,
                invoice.total_cents,
                customer.name,
                customer.vat_number,
                customer.fiscal_code,
                source_file,
                source_sha256,
                position_in_file,
            ),
        )
        invoice_id = cursor.lastrowid
        connection.executemany(
            "INSERT INTO lines VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    invoice_id,
                    line.number,
                    line.description,
                    None if line.quantity is None else str(line.quantity),
                    str(line.unit_price),
                    line.total_cents,
                    str(line.vat_rate),
                    line.nature,
                )
                for line in invoice.lines
            ],
        )
        connection.executemany(
            "INSERT INTO vat_summaries VALUES (?, ?, ?, ?, ?)",
            [
                (invoice_id, str(s.vat_rate), s.nature, s.taxable_cents, s.tax_cents)
                for s in invoice.vat_summaries
            ],
        )
        connection.executemany(
            "INSERT INTO payments VALUES (?, ?, ?, ?, ?)",
            [
                (
                    invoice_id,
                    p.method,
                    p.amount_cents,
                    None if p.due_date is None else p.due_date.isoformat(),
                    p.iban,
                )
                for p in invoice.payments
            ],
        )
    return True


def _supplier_id(connection: sqlite3.Connection, supplier: Party) -> int:
    """The supplier is identified by its VAT number; the first name seen is kept."""
    if supplier.vat_country is None or supplier.vat_number is None:
        raise ValueError(f"supplier {supplier.name!r} has no VAT number")
    key = (supplier.vat_country, supplier.vat_number)
    connection.execute(
        "INSERT OR IGNORE INTO suppliers (vat_country, vat_number, name, fiscal_code)"
        " VALUES (?, ?, ?, ?)",
        (*key, supplier.name, supplier.fiscal_code),
    )
    row = connection.execute(
        "SELECT id FROM suppliers WHERE vat_country = ? AND vat_number = ?", key
    ).fetchone()
    return int(row[0])
