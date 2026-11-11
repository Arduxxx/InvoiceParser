"""Imports a folder of FatturaPA files (.xml and .xml.p7m) into the SQLite archive.

    python -m invoiceflow.importers.ingest data/real --db data/invoiceflow.sqlite

A file that cannot be read is reported and skipped; the others are imported anyway.
This is the sequential version; spreading the work over several cores comes later.
"""

import argparse
import hashlib
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from invoiceflow import storage
from invoiceflow.importers.p7m import extract_xml
from invoiceflow.importers.xml_parser import InvoiceFormatError, parse_invoices
from invoiceflow.model import Invoice


@dataclass
class IngestReport:
    imported: int = 0  # invoices added to the archive
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
    digest = hashlib.sha256(data).hexdigest()
    if path.name.lower().endswith(".p7m"):
        data = extract_xml(data)
    return parse_invoices(data, source_path=path.as_posix(), source_sha256=digest)


def ingest_folder(folder: Path, connection: sqlite3.Connection) -> IngestReport:
    report = IngestReport()
    for path in invoice_files(folder):
        try:
            invoices = read_invoices(path, path.read_bytes())
            for position, invoice in enumerate(invoices):
                if storage.save_invoice(connection, invoice, position):
                    report.imported += 1
                else:
                    report.already_present += 1
        except (InvoiceFormatError, ValueError, OSError) as error:
            report.errors.append((path.name, str(error)))
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Import FatturaPA invoices into SQLite.")
    parser.add_argument("folder", type=Path, help="folder with .xml / .xml.p7m files")
    parser.add_argument("--db", type=Path, default=Path("data") / "invoiceflow.sqlite")
    args = parser.parse_args(argv)
    if not args.folder.is_dir():
        parser.error(f"{args.folder} is not a folder")
    connection = storage.connect(args.db)
    try:
        report = ingest_folder(args.folder, connection)
    finally:
        connection.close()
    print(f"Imported {report.imported} invoices, {report.already_present} already present.")
    for name, problem in report.errors:
        print(f"  skipped {name}: {problem}")
    return 1 if report.errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
