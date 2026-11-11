from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from invoiceflow.importers.xml_parser import InvoiceFormatError, parse_invoices

SAMPLE = Path(__file__).parent.parent / "fixtures" / "IT01234567897_00001.xml"


def sample_bytes() -> bytes:
    return SAMPLE.read_bytes()


def test_reads_the_sample_invoice() -> None:
    [invoice] = parse_invoices(sample_bytes())
    assert invoice.document_type == "TD01"
    assert invoice.currency == "EUR"
    assert invoice.issue_date == date(2026, 10, 15)
    assert invoice.number == "2026/123"
    assert invoice.total_cents == 12852


def test_reads_supplier_and_customer() -> None:
    [invoice] = parse_invoices(sample_bytes())
    supplier, customer = invoice.supplier, invoice.customer
    assert supplier.name == "Alfa Forniture S.r.l."
    assert (supplier.vat_country, supplier.vat_number) == ("IT", "01234567897")
    assert supplier.address.street == "Via Roma 1"  # NumeroCivico appended
    assert supplier.address.province == "MI"
    assert customer.name == "Giulia Bianchi"  # a person: Nome + Cognome
    assert customer.vat_number is None
    assert customer.fiscal_code == "BNCGLI85M41L219Q"


def test_reads_lines_vat_summaries_and_payments() -> None:
    [invoice] = parse_invoices(sample_bytes())
    first, second = invoice.lines
    assert (first.number, first.quantity, first.unit_price) == (
        1,
        Decimal("20.00"),
        Decimal("4.50"),
    )
    assert (first.total_cents, first.vat_rate) == (9000, Decimal("22.00"))
    assert second.description == "Manuale di contabilità"  # accented letters survive
    assert [(s.vat_rate, s.taxable_cents, s.tax_cents) for s in invoice.vat_summaries] == [
        (Decimal("22.00"), 9000, 1980),
        (Decimal("4.00"), 1800, 72),
    ]
    assert [(p.amount_cents, p.due_date) for p in invoice.payments] == [
        (6426, date(2026, 11, 15)),
        (6426, date(2026, 12, 15)),
    ]
    assert invoice.payments[0].iban == "IT60X0542811101000000123456"


def test_a_file_with_two_bodies_holds_two_invoices() -> None:
    text = sample_bytes().decode("utf-8")
    start = text.index("<FatturaElettronicaBody>")
    end = text.index("</FatturaElettronicaBody>") + len("</FatturaElettronicaBody>")
    body = text[start:end]
    second = body.replace("2026/123", "2026/124")
    both = text[:end] + second + text[end:]
    invoices = parse_invoices(both.encode("utf-8"))
    assert [invoice.number for invoice in invoices] == ["2026/123", "2026/124"]
    assert invoices[0].supplier == invoices[1].supplier


def test_any_namespace_prefix_works() -> None:
    renamed = sample_bytes().replace(b"p:FatturaElettronica", b"ns2:FatturaElettronica")
    renamed = renamed.replace(b"xmlns:p=", b"xmlns:ns2=")
    assert parse_invoices(renamed)[0].number == "2026/123"


def test_windows_1252_files_are_read() -> None:
    text = sample_bytes().decode("utf-8").replace('encoding="UTF-8"', 'encoding="windows-1252"')
    [invoice] = parse_invoices(text.encode("cp1252"))
    assert invoice.lines[1].description == "Manuale di contabilità"


def test_optional_total_may_be_missing() -> None:
    xml = sample_bytes().replace(b"<ImportoTotaleDocumento>128.52</ImportoTotaleDocumento>", b"")
    assert parse_invoices(xml)[0].total_cents is None


@pytest.mark.parametrize(
    ("xml", "problem"),
    [
        (b"not xml at all", "not well-formed"),
        (b"<Ordine/>", "root element"),
        (b'<!DOCTYPE x [<!ENTITY a "b">]><x/>', "DOCTYPE"),
    ],
)
def test_files_that_are_not_invoices(xml: bytes, problem: str) -> None:
    with pytest.raises(InvoiceFormatError, match=problem):
        parse_invoices(xml)


def test_a_missing_required_field_is_named() -> None:
    xml = sample_bytes().replace(b"<Numero>2026/123</Numero>", b"")
    with pytest.raises(InvoiceFormatError, match="Numero"):
        parse_invoices(xml)


def test_a_bad_amount_is_reported() -> None:
    xml = sample_bytes().replace(b"<PrezzoTotale>90.00", b"<PrezzoTotale>novanta")
    with pytest.raises(InvoiceFormatError, match="not a number"):
        parse_invoices(xml)
