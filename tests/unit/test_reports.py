import sqlite3
from collections.abc import Iterator
from dataclasses import replace
from datetime import date
from pathlib import Path

import pytest

from invoiceflow import reports, storage
from invoiceflow.importers.xml_parser import parse_invoices
from invoiceflow.model import Invoice
from tests.unit.test_xml_parser import SAMPLE


@pytest.fixture
def sample() -> Invoice:
    [invoice] = parse_invoices(SAMPLE.read_bytes(), source_sha256="a")
    return invoice  # total 128.52, paid in two instalments of 64.26


@pytest.fixture
def connection(tmp_path: Path, sample: Invoice) -> Iterator[sqlite3.Connection]:
    db = storage.connect(tmp_path / "archive.sqlite")
    march = replace(sample, numero="1", data=date(2026, 3, 5), source_sha256="b")
    storage.save_invoice(db, sample, 0)
    storage.save_invoice(db, march, 0)
    yield db
    db.close()


def test_spend_by_month_supplier_and_category(
    connection: sqlite3.Connection, sample: Invoice
) -> None:
    supplier = storage.display_name(sample.cedente)
    assert storage.set_category(connection, sample.cedente.partita_iva, "office")
    spends = reports.load_spend(connection, skip_ids=set())
    assert reports.total_by(spends, lambda s: s.supplier) == {supplier: 2 * 12852}
    assert reports.total_by(spends, lambda s: s.category) == {"office": 2 * 12852}
    months = reports.total_by(spends, reports.by_month)
    assert months["2026-03"] == 12852 and sum(months.values()) == 2 * 12852


def test_skipped_invoices_and_credit_notes(connection: sqlite3.Connection) -> None:
    connection.execute("UPDATE invoices SET tipo_documento = 'TD04' WHERE numero = '1'")
    spends = reports.load_spend(connection, skip_ids=set())
    assert sum(s.amount_cents for s in spends) == 0  # the credit note cancels the invoice
    assert len(reports.load_spend(connection, skip_ids={1})) == 1
    assert spends[0].category == reports.UNCATEGORISED


def test_missing_total_falls_back_to_the_vat_summary(connection: sqlite3.Connection) -> None:
    connection.execute("UPDATE invoices SET importo_totale_cents = NULL")
    spends = reports.load_spend(connection, skip_ids=set())
    assert [s.amount_cents for s in spends] == [12852, 12852]


def test_unknown_supplier_gets_no_category(connection: sqlite3.Connection) -> None:
    assert not storage.set_category(connection, "00000000000", "office")


def test_coming_due(connection: sqlite3.Connection, sample: Invoice) -> None:
    due = sample.payments[0].data_scadenza
    assert due is not None
    found = reports.coming_due(connection, due, due, skip_ids=set())
    assert [p.amount_cents for p in found] == [6426, 6426]  # first of two instalments
    assert reports.coming_due(connection, due, due, skip_ids={1, 2}) == []


def test_money_formatting() -> None:
    assert reports.euros(123456) == "€ 1,234.56"
    assert [reports.whole_euros(c) for c in (149, 150, 0)] == [1, 2, 0]
