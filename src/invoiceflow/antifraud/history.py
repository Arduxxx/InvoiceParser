"""What the anti-fraud rules read from the archive, and what they report."""

import sqlite3
from dataclasses import dataclass

from invoiceflow import storage
from invoiceflow.duplicates import normalize_number
from invoiceflow.money import from_cents


@dataclass(frozen=True)
class Flag:
    """A suspicious invoice. rule has the same name as the generator's fraud kind
    (iban_change, clone_supplier, outlier_amount), so results can be compared with labels.csv."""

    invoice_id: int
    rule: str
    message: str  # why the invoice is suspicious, with the facts behind it


@dataclass(frozen=True)
class Record:
    """One archived invoice, with what the rules need to know about it."""

    id: int
    supplier_id: int
    id_paese: str
    partita_iva: str
    supplier_name: str
    tipo_documento: str
    numero: str
    data: str  # ISO date
    total_cents: int
    ibans: tuple[str, ...]  # IBANs of its payments, written without spaces, upper case

    def describe(self) -> str:
        return f"Invoice {self.numero} of {self.data} from {self.supplier_name}"

    def total(self) -> str:
        return f"€ {from_cents(self.total_cents):,.2f}"


def load_records(connection: sqlite3.Connection) -> list[Record]:
    """Every archived invoice, oldest first: the rules read them in the order they arrived."""
    ibans: dict[int, list[str]] = {}
    for invoice_id, iban in connection.execute(
        "SELECT invoice_id, iban FROM payments WHERE iban != ''"
    ):
        ibans.setdefault(invoice_id, []).append(iban.replace(" ", "").upper())
    rows = connection.execute(
        "SELECT i.id, c.id, c.id_paese, c.partita_iva, c.nome, i.tipo_documento, i.numero,"
        f" i.data, {storage.INVOICE_TOTAL_CENTS}"
        " FROM invoices i JOIN cedenti c ON c.id = i.cedente_id ORDER BY i.data, i.id"
    ).fetchall()
    return [
        Record(
            id=row[0],
            supplier_id=row[1],
            id_paese=row[2],
            partita_iva=row[3],
            supplier_name=row[4],
            tipo_documento=row[5],
            numero=row[6],
            data=row[7],
            total_cents=row[8],
            ibans=tuple(ibans.get(row[0], [])),
        )
        for row in rows
    ]


def without_extra_copies(records: list[Record]) -> list[Record]:
    """The records without the re-uploads of an invoice (same supplier, date and number, as in
    duplicates.py): a copy is not new history, and should not be flagged a second time."""
    seen: set[tuple[int, str, str]] = set()
    kept = []
    for record in records:
        key = (record.supplier_id, record.data, normalize_number(record.numero))
        if key not in seen:
            seen.add(key)
            kept.append(record)
    return kept
