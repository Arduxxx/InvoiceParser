"""Finds the same invoice uploaded more than once, even under another file name or with
the invoice number written differently ("2026/007", "2026-7", "N. 2026/007").

Two invoices are the same invoice when they have
- the same supplier (VAT number),
- the same issue date,
- the same invoice number, once normalised (see normalize_number).
An Italian supplier may not reuse a number in the same year, so this is safe.

Speed: instead of comparing every invoice with every other one (n * n comparisons), each
invoice is put in a dictionary under its key; invoices that land on the same key are
duplicates. That is one pass over the archive, fast even at 100,000 invoices.

    python -m invoiceflow.duplicates --db data/invoiceflow.sqlite
"""

import argparse
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from invoiceflow import storage

_PREFIX = re.compile(r"^(N|NR|NUM)\s*[.°:]\s*")  # "N. ", "Nr.", "N°"
_PARTS = re.compile(r"[A-Z]+|[0-9]+")  # runs of letters or of digits


def normalize_number(numero: str) -> str:
    """The invoice number reduced to what matters: letters upper case, no "N." prefix,
    no separators, no leading zeros.  "n. 2026 / 007" -> "2026/7", "ft00012" -> "FT/12"."""
    text = _PREFIX.sub("", numero.strip().upper())
    parts = [(part.lstrip("0") or "0") if part.isdigit() else part for part in _PARTS.findall(text)]
    return "/".join(parts)


@dataclass(frozen=True)
class ArchivedInvoice:
    """The few fields of an archived invoice needed to spot duplicates and show them."""

    id: int
    supplier_vat: str  # id_paese + partita_iva, e.g. "IT01234567897"
    supplier_name: str
    numero: str
    data: str  # ISO date, YYYY-MM-DD
    total_cents: int | None
    source_path: str


def load_invoices(connection: sqlite3.Connection) -> list[ArchivedInvoice]:
    rows = connection.execute(
        "SELECT i.id, c.id_paese || c.partita_iva, c.nome, i.numero, i.data,"
        " i.importo_totale_cents, i.source_path"
        " FROM invoices i JOIN cedenti c ON c.id = i.cedente_id ORDER BY i.id"
    ).fetchall()
    return [ArchivedInvoice(*row) for row in rows]


def find_duplicates(invoices: list[ArchivedInvoice]) -> list[list[ArchivedInvoice]]:
    """Groups of two or more copies of the same invoice. In each group the first one is
    the oldest in the list (the original), the others are re-uploads."""
    groups: dict[tuple[str, str, str], list[ArchivedInvoice]] = {}
    for invoice in invoices:
        key = (invoice.supplier_vat, invoice.data, normalize_number(invoice.numero))
        groups.setdefault(key, []).append(invoice)
    return [group for group in groups.values() if len(group) > 1]


def explain(group: list[ArchivedInvoice]) -> str:
    """Why these invoices are considered the same one, in words."""
    first = group[0]
    numbers = sorted({invoice.numero for invoice in group})
    written = " / ".join(repr(n) for n in numbers)
    files = ", ".join(Path(invoice.source_path).name for invoice in group)
    return (
        f"{len(group)} copies of invoice {written} from {first.supplier_name}"
        f" ({first.supplier_vat}) dated {first.data}: same supplier, date and number"
        f" (files: {files})"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="List invoices uploaded more than once.")
    parser.add_argument("--db", type=Path, default=Path("data") / "invoiceflow.sqlite")
    args = parser.parse_args(argv)
    connection = storage.connect(args.db)
    try:
        groups = find_duplicates(load_invoices(connection))
    finally:
        connection.close()
    print(f"{len(groups)} duplicated invoices found.")
    for group in groups:
        print(f"  {explain(group)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
