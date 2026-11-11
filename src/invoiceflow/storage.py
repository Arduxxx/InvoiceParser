"""SQLite archive of imported invoices.

Columns are named like the fields of invoiceflow.model (and so like the FatturaPA elements).
Amounts are stored as integer cents; quantities, unit prices, line totals (up to 8 decimals)
and VAT rates as text, so that a Decimal goes in and comes out exactly.

Loading the same file twice does not duplicate anything: every invoice remembers the
SHA-256 of the file it came from and its position in that file. The same invoice arriving
in a *different* file is stored again on purpose: finding those duplicates is a separate
check, which needs to see both copies.
"""

import sqlite3
from pathlib import Path

from invoiceflow.model import Invoice, Party

SCHEMA = """
CREATE TABLE IF NOT EXISTS cedenti (
    id INTEGER PRIMARY KEY,
    id_paese TEXT NOT NULL,
    partita_iva TEXT NOT NULL,
    nome TEXT NOT NULL,
    codice_fiscale TEXT NOT NULL,
    UNIQUE (id_paese, partita_iva)
);
CREATE TABLE IF NOT EXISTS invoices (
    id INTEGER PRIMARY KEY,
    cedente_id INTEGER NOT NULL REFERENCES cedenti (id),
    tipo_documento TEXT NOT NULL,
    numero TEXT NOT NULL,
    data TEXT NOT NULL,
    divisa TEXT NOT NULL,
    importo_totale_cents INTEGER,
    cessionario_nome TEXT NOT NULL,
    cessionario_partita_iva TEXT NOT NULL,
    cessionario_codice_fiscale TEXT NOT NULL,
    source_path TEXT NOT NULL,
    source_sha256 TEXT NOT NULL,
    position_in_file INTEGER NOT NULL,
    UNIQUE (source_sha256, position_in_file)
);
CREATE TABLE IF NOT EXISTS lines (
    invoice_id INTEGER NOT NULL REFERENCES invoices (id),
    numero_linea INTEGER NOT NULL,
    descrizione TEXT NOT NULL,
    quantita TEXT,
    unita_misura TEXT NOT NULL,
    prezzo_unitario TEXT NOT NULL,
    prezzo_totale TEXT NOT NULL,
    aliquota_iva TEXT NOT NULL,
    natura TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS vat_summary (
    invoice_id INTEGER NOT NULL REFERENCES invoices (id),
    aliquota_iva TEXT NOT NULL,
    imponibile_cents INTEGER NOT NULL,
    imposta_cents INTEGER NOT NULL,
    esigibilita_iva TEXT NOT NULL,
    natura TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS payments (
    invoice_id INTEGER NOT NULL REFERENCES invoices (id),
    modalita TEXT NOT NULL,
    data_scadenza TEXT,
    importo_cents INTEGER,
    iban TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS invoices_by_cedente ON invoices (cedente_id, importo_totale_cents);
"""


def connect(path: Path) -> sqlite3.Connection:
    """Open (and if needed create) the archive."""
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA foreign_keys = ON")
    connection.executescript(SCHEMA)
    return connection


def display_name(party: Party) -> str:
    """Denominazione for a company, "Nome Cognome" for a person."""
    return party.denominazione or f"{party.nome} {party.cognome}".strip()


def save_invoice(connection: sqlite3.Connection, invoice: Invoice, position_in_file: int) -> bool:
    """Store one invoice with its lines, VAT summaries and payments, all or nothing.
    Returns False if it was already there (same source file, same position in it)."""
    with connection:  # one transaction: commit at the end, rollback on any error
        already = connection.execute(
            "SELECT 1 FROM invoices WHERE source_sha256 = ? AND position_in_file = ?",
            (invoice.source_sha256, position_in_file),
        ).fetchone()
        if already:
            return False
        cedente_id = _cedente_id(connection, invoice.cedente)
        cessionario = invoice.cessionario
        cursor = connection.execute(
            "INSERT INTO invoices (cedente_id, tipo_documento, numero, data, divisa,"
            " importo_totale_cents, cessionario_nome, cessionario_partita_iva,"
            " cessionario_codice_fiscale, source_path, source_sha256, position_in_file)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                cedente_id,
                invoice.tipo_documento,
                invoice.numero,
                invoice.data.isoformat(),
                invoice.divisa,
                invoice.importo_totale_cents,
                display_name(cessionario),
                cessionario.partita_iva,
                cessionario.codice_fiscale,
                invoice.source_path,
                invoice.source_sha256,
                position_in_file,
            ),
        )
        invoice_id = cursor.lastrowid
        connection.executemany(
            "INSERT INTO lines VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    invoice_id,
                    line.numero_linea,
                    line.descrizione,
                    None if line.quantita is None else str(line.quantita),
                    line.unita_misura,
                    str(line.prezzo_unitario),
                    str(line.prezzo_totale),
                    str(line.aliquota_iva),
                    line.natura,
                )
                for line in invoice.lines
            ],
        )
        connection.executemany(
            "INSERT INTO vat_summary VALUES (?, ?, ?, ?, ?, ?)",
            [
                (
                    invoice_id,
                    str(s.aliquota_iva),
                    s.imponibile_cents,
                    s.imposta_cents,
                    s.esigibilita_iva,
                    s.natura,
                )
                for s in invoice.vat_summary
            ],
        )
        connection.executemany(
            "INSERT INTO payments VALUES (?, ?, ?, ?, ?)",
            [
                (
                    invoice_id,
                    p.modalita,
                    None if p.data_scadenza is None else p.data_scadenza.isoformat(),
                    p.importo_cents,
                    p.iban,
                )
                for p in invoice.payments
            ],
        )
    return True


def _cedente_id(connection: sqlite3.Connection, cedente: Party) -> int:
    """The supplier is identified by its VAT number; the first name seen is kept."""
    if not cedente.id_paese or not cedente.partita_iva:
        raise ValueError(f"supplier {display_name(cedente)!r} has no VAT number")
    key = (cedente.id_paese, cedente.partita_iva)
    connection.execute(
        "INSERT OR IGNORE INTO cedenti (id_paese, partita_iva, nome, codice_fiscale)"
        " VALUES (?, ?, ?, ?)",
        (*key, display_name(cedente), cedente.codice_fiscale),
    )
    row = connection.execute(
        "SELECT id FROM cedenti WHERE id_paese = ? AND partita_iva = ?", key
    ).fetchone()
    return int(row[0])
