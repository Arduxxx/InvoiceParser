import shutil
import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from invoiceflow import storage
from invoiceflow.generator import DatasetConfig, generate_dataset, write_dataset
from invoiceflow.importers.ingest import ingest_folder, main, read_invoices
from tests.unit.test_p7m import make_p7m
from tests.unit.test_xml_parser import SAMPLE


@pytest.fixture
def folder(tmp_path: Path) -> Path:
    invoices = tmp_path / "invoices"
    invoices.mkdir()
    shutil.copy(SAMPLE, invoices / SAMPLE.name)
    other = SAMPLE.read_bytes().replace(b"2026/123", b"2026/200")
    (invoices / "IT01234567897_00002.xml.p7m").write_bytes(make_p7m(other))
    (invoices / "broken.xml").write_bytes(b"<FatturaElettronica>")
    (invoices / "notes.txt").write_text("not an invoice", encoding="utf-8")
    return invoices


@pytest.fixture
def connection(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    db = storage.connect(tmp_path / "db" / "archive.sqlite")
    yield db
    db.close()  # an open database file cannot be deleted on Windows


def test_imports_xml_and_p7m_and_reports_broken_files(
    folder: Path, connection: sqlite3.Connection
) -> None:
    report = ingest_folder(folder, connection)
    assert report.imported == 2
    assert [name for name, _ in report.errors] == ["broken.xml"]
    numbers = connection.execute("SELECT numero FROM invoices ORDER BY numero").fetchall()
    assert numbers == [("2026/123",), ("2026/200",)]


def test_loading_twice_does_not_duplicate(folder: Path, connection: sqlite3.Connection) -> None:
    ingest_folder(folder, connection)
    again = ingest_folder(folder, connection)
    assert (again.imported, again.already_present) == (0, 2)
    assert connection.execute("SELECT COUNT(*) FROM invoices").fetchone() == (2,)
    assert connection.execute("SELECT COUNT(*) FROM cedenti").fetchone() == (1,)
    assert connection.execute("SELECT COUNT(*) FROM lines").fetchone() == (4,)


def test_the_same_invoice_in_another_file_is_kept_for_the_duplicate_check(
    folder: Path, connection: sqlite3.Connection
) -> None:
    ingest_folder(folder, connection)
    (folder / "copy.xml").write_bytes(SAMPLE.read_bytes() + b"\n")  # same invoice, new bytes
    assert ingest_folder(folder, connection).imported == 1


def test_amounts_are_stored_as_integer_cents(folder: Path, connection: sqlite3.Connection) -> None:
    ingest_folder(folder, connection)
    total, kind = connection.execute(
        "SELECT importo_totale_cents, typeof(importo_totale_cents) FROM invoices"
        " WHERE numero = '2026/123'"
    ).fetchone()
    assert (total, kind) == (12852, "integer")
    prices = connection.execute(
        "SELECT prezzo_unitario, prezzo_totale FROM lines ORDER BY rowid LIMIT 2"
    ).fetchall()
    assert prices == [("4.50", "90.00"), ("18.00", "18.00")]  # Decimal kept exactly, as text


def test_command_line(folder: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code = main([str(folder), "--db", str(tmp_path / "cli.sqlite")])
    output = capsys.readouterr().out
    assert code == 1  # one file was skipped
    assert "Imported 2 invoices" in output and "skipped broken.xml" in output


def test_imports_the_invoices_of_the_generator(
    tmp_path: Path, connection: sqlite3.Connection
) -> None:
    """The contract with part B: every generated invoice is read back unchanged."""
    dataset = generate_dataset(DatasetConfig(n_invoices=60, seed=3))
    write_dataset(dataset, tmp_path / "generated")
    report = ingest_folder(tmp_path / "generated" / "invoices", connection)
    assert (report.imported, report.errors) == (60, [])

    path = tmp_path / "generated" / "invoices" / f"{dataset.invoices[0].file_id}.xml"
    [read_back] = read_invoices(path, path.read_bytes())
    original = dataset.invoices[0].invoice
    assert (read_back.numero, read_back.cedente, read_back.lines) == (
        original.numero,
        original.cedente,
        original.lines,
    )
