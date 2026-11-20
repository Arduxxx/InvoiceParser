"""Imports a folder of FatturaPA files (.xml and .xml.p7m) into the SQLite archive.

    python -m invoiceflow.importers.ingest data/real --db data/invoiceflow.sqlite

Every invoice is checked while it is imported (official XSD schema, amounts, VAT numbers,
IBANs, dates) and the problems found are stored with it, for the dashboard.
A file that cannot be read is reported and skipped; the others are imported anyway.
This is the sequential version; spreading the work over several cores comes later.
"""

import argparse
import csv
import hashlib
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from invoiceflow import storage
from invoiceflow.checks import check_invoice
from invoiceflow.checks.schema import schema_findings
from invoiceflow.importers.p7m import extract_xml
from invoiceflow.importers.xml_parser import InvoiceFormatError, parse_invoices
from invoiceflow.model import Invoice


@dataclass
class IngestReport:
    imported: int = 0  # invoices added to the archive
    with_problems: int = 0  # of which failed at least one check
    already_present: int = 0  # invoices of files loaded before
    errors: list[tuple[str, str]] = field(default_factory=list)  # (file name, problem)


def invoice_files(folder: Path) -> list[Path]:
    """The .xml and .xml.p7m files in folder and its subfolders, in a stable order."""
    return sorted(
        path
        for path in folder.rglob("*")
        if path.is_file() and path.name.lower().endswith((".xml", ".xml.p7m"))
    )


def read_invoices(path: Path, data: bytes) -> list[Invoice]:
    """The invoices in one file; data are its raw bytes (the XML declares its encoding).
    The hash is taken on the raw bytes, so a .p7m is recognised as the file it is."""
    return parse_invoices(
        _xml_of(path, data), source_path=path.as_posix(), source_sha256=_sha256(data)
    )


def _xml_of(path: Path, data: bytes) -> bytes:
    return extract_xml(data) if path.name.lower().endswith(".p7m") else data


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def ingest_folder(folder: Path, connection: sqlite3.Connection) -> IngestReport:
    report = IngestReport()
    for path in invoice_files(folder):
        try:
            data = path.read_bytes()
            xml = _xml_of(path, data)
            invoices = parse_invoices(xml, source_path=path.as_posix(), source_sha256=_sha256(data))
            # the schema looks at the whole file, the other checks at each invoice in it
            file_findings = schema_findings(xml)
            for position, invoice in enumerate(invoices):
                findings = file_findings + check_invoice(invoice)
                if storage.save_invoice(connection, invoice, position, findings):
                    report.imported += 1
                    report.with_problems += bool(findings)
                else:
                    report.already_present += 1
        except (InvoiceFormatError, ValueError, OSError) as error:
            report.errors.append((path.name, str(error)))
    return report


def load_categories(csv_path: Path, connection: sqlite3.Connection) -> int:
    """Read supplier categories from a CSV with columns partita_iva and category (the
    suppliers.csv written by the generator has them). Returns how many were applied."""
    applied = 0
    with open(csv_path, encoding="utf-8", newline="") as file:
        for row in csv.DictReader(file):
            if storage.set_category(connection, row["partita_iva"], row["category"]):
                applied += 1
    return applied


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Import FatturaPA invoices into SQLite.")
    parser.add_argument("folder", type=Path, help="folder with .xml / .xml.p7m files")
    parser.add_argument("--db", type=Path, default=Path("data") / "invoiceflow.sqlite")
    parser.add_argument(
        "--categories", type=Path, help="CSV with columns partita_iva,category (optional)"
    )
    args = parser.parse_args(argv)
    if not args.folder.is_dir():
        parser.error(f"{args.folder} is not a folder")
    connection = storage.connect(args.db)
    try:
        report = ingest_folder(args.folder, connection)
        if args.categories:
            applied = load_categories(args.categories, connection)
            print(f"Set the category of {applied} suppliers.")
    finally:
        connection.close()
    print(
        f"Imported {report.imported} invoices ({report.with_problems} with problems),"
        f" {report.already_present} already present."
    )
    for name, problem in report.errors:
        print(f"  skipped {name}: {problem}")
    return 1 if report.errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
