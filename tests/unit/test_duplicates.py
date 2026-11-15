import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from invoiceflow import storage
from invoiceflow.duplicates import (
    ArchivedInvoice,
    explain,
    find_duplicates,
    load_invoices,
    main,
    normalize_number,
)
from invoiceflow.generator import DatasetConfig, generate_dataset, write_dataset
from invoiceflow.importers.ingest import ingest_folder


@pytest.mark.parametrize(
    ("written", "normalised"),
    [
        ("2026/007", "2026/7"),
        ("2026-7", "2026/7"),
        ("2026 / 000007", "2026/7"),
        ("N. 2026/007", "2026/7"),
        ("n° 2026/007", "2026/7"),
        ("FT00012", "FT/12"),
        ("ft12", "FT/12"),
        ("0", "0"),
    ],
)
def test_normalize_number(written: str, normalised: str) -> None:
    assert normalize_number(written) == normalised


def test_different_numbers_stay_different() -> None:
    assert normalize_number("2026/17") != normalize_number("2026/7")
    assert normalize_number("A0012") != normalize_number("FT0012")
    assert normalize_number("12/2026") != normalize_number("2026/12")


def invoice(id: int, numero: str, data: str = "2026-03-01", vat: str = "IT01234567897") -> (
    ArchivedInvoice
):
    return ArchivedInvoice(id, vat, "Rossi S.r.l.", numero, data, 12200, f"in/{id}.xml")


def test_finds_the_same_invoice_written_differently() -> None:
    groups = find_duplicates([invoice(1, "2026/007"), invoice(2, "x"), invoice(3, "N. 2026-7")])
    assert [[i.id for i in group] for group in groups] == [[1, 3]]


def test_same_number_but_other_supplier_or_date_is_not_a_duplicate() -> None:
    invoices = [
        invoice(1, "1"),
        invoice(2, "1", vat="IT99999999999"),
        invoice(3, "1", data="2027-03-01"),
    ]
    assert find_duplicates(invoices) == []


def test_explain_says_why() -> None:
    text = explain([invoice(1, "2026/007"), invoice(2, "2026-7")])
    assert "2 copies" in text and "'2026-7' / '2026/007'" in text
    assert "same supplier, date and number" in text and "1.xml, 2.xml" in text


@pytest.fixture
def connection(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    db = storage.connect(tmp_path / "archive.sqlite")
    yield db
    db.close()


def test_finds_exactly_the_duplicates_of_the_generator(
    tmp_path: Path, connection: sqlite3.Connection
) -> None:
    """Ground truth from the generator: every re-upload is found, nothing else is flagged."""
    dataset = generate_dataset(DatasetConfig(n_invoices=400, duplicate_rate=0.05, seed=7))
    write_dataset(dataset, tmp_path / "generated")
    ingest_folder(tmp_path / "generated" / "invoices", connection)

    expected = {
        (item.duplicate_of, item.file_id) for item in dataset.invoices if item.duplicate_of
    }
    found = set()
    for group in find_duplicates(load_invoices(connection)):
        original = Path(group[0].source_path).stem
        found |= {(original, Path(copy.source_path).stem) for copy in group[1:]}
    assert len(expected) == 20
    assert found == expected


def test_command_line(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--db", str(tmp_path / "empty.sqlite")]) == 0
    assert "0 duplicated invoices found." in capsys.readouterr().out
