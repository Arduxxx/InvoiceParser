"""Imports a folder of invoices (FatturaPA .xml and .xml.p7m, Italian .pdf) into SQLite.

    python -m invoiceflow.importers.ingest data/real --db data/invoiceflow.sqlite

Every invoice is checked while it is imported (amounts, VAT numbers, IBANs, dates, and the
official XSD schema for XML files) and the problems found are stored with it, for the dashboard.
A file that cannot be read is reported and skipped; the others are imported anyway.
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
from invoiceflow.importers.pdf_parser import parse_pdf_invoice
from invoiceflow.importers.xml_parser import InvoiceFormatError, parse_invoices
from invoiceflow.model import Invoice


@dataclass
class IngestReport:
    imported: int = 0  # invoices added to the archive
    with_problems: int = 0  # of which failed at least one check
    already_present: int = 0  # invoices of files loaded before
    errors: list[tuple[str, str]] = field(default_factory=list)  # (file name, problem)


def invoice_files(folder: Path) -> list[Path]:
    """The .xml, .xml.p7m and .pdf files in folder and its subfolders, in a stable order."""
    return sorted(
        path
        for path in folder.rglob("*")
        if path.is_file() and path.name.lower().endswith((".xml", ".xml.p7m", ".pdf"))
    )


def read_invoices(path: Path, data: bytes) -> list[Invoice]:
    """The invoices in one file; data are its raw bytes (the XML declares its encoding).
    The hash is taken on the raw bytes, so a .p7m is recognised as the file it is."""
    if _is_pdf(path):
        return [parse_pdf_invoice(data, source_path=path.as_posix(), source_sha256=_sha256(data))]
    return parse_invoices(
        _xml_of(path, data), source_path=path.as_posix(), source_sha256=_sha256(data)
    )


def _is_pdf(path: Path) -> bool:
    return path.name.lower().endswith(".pdf")


def _xml_of(path: Path, data: bytes) -> bytes:
    return extract_xml(data) if path.name.lower().endswith(".p7m") else data


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def ingest_folder(folder: Path, connection: sqlite3.Connection) -> IngestReport:
    report = IngestReport()
    for path in invoice_files(folder):
        try:
            data = path.read_bytes()
        except OSError as error:
            report.errors.append((path.name, str(error)))
            continue
        ingest_file(path, data, connection, report)
    return report


def ingest_file(
    path: Path, data: bytes, connection: sqlite3.Connection, report: IngestReport
) -> None:
    """Import the invoices of one file, given its raw bytes, and add the outcome to report.
    Used for each file of a folder, and by the dashboard for the files dropped on it."""
    try:
        invoices = read_invoices(path, data)
        # the schema looks at the whole XML file, the other checks at each invoice in it
        file_findings = [] if _is_pdf(path) else schema_findings(_xml_of(path, data))
        for position, invoice in enumerate(invoices):
            findings = file_findings + check_invoice(invoice)
            if storage.save_invoice(connection, invoice, position, findings):
                report.imported += 1
                report.with_problems += bool(findings)
            else:
                report.already_present += 1
    except (InvoiceFormatError, ValueError) as error:
        report.errors.append((path.name, str(error)))


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
    parser.add_argument("folder", type=Path, help="folder with .xml / .xml.p7m / .pdf files")
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
