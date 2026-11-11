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
    assert invoice.tipo_documento == "TD01"
    assert invoice.divisa == "EUR"
    assert invoice.data == date(2026, 10, 15)
    assert invoice.numero == "2026/123"
    assert invoice.importo_totale_cents == 12852


def test_reads_cedente_and_cessionario() -> None:
    [invoice] = parse_invoices(sample_bytes())
    cedente, cessionario = invoice.cedente, invoice.cessionario
    assert cedente.denominazione == "Alfa Forniture S.r.l."
    assert (cedente.id_paese, cedente.partita_iva) == ("IT", "01234567897")
    assert cedente.indirizzo == "Via Roma 1"  # NumeroCivico appended
    assert cedente.provincia == "MI"
    assert (cessionario.nome, cessionario.cognome) == ("Giulia", "Bianchi")  # a person
    assert cessionario.denominazione == ""
    assert cessionario.partita_iva == ""  # a private customer has no partita IVA
    assert cessionario.codice_fiscale == "BNCGLI85M41L219Q"


def test_reads_lines_vat_summary_and_payments() -> None:
    [invoice] = parse_invoices(sample_bytes())
    first, second = invoice.lines
    assert (first.numero_linea, first.quantita, first.prezzo_unitario) == (
        1,
        Decimal("20.00"),
        Decimal("4.50"),
    )
    assert (first.prezzo_totale, first.aliquota_iva) == (Decimal("90.00"), Decimal("22.00"))
    assert second.descrizione == "Manuale di contabilità"  # accented letters survive
    summaries = [(s.aliquota_iva, s.imponibile_cents, s.imposta_cents) for s in invoice.vat_summary]
    assert summaries == [(Decimal("22.00"), 9000, 1980), (Decimal("4.00"), 1800, 72)]
    assert invoice.vat_summary[0].esigibilita_iva == "I"
    assert [(p.importo_cents, p.data_scadenza) for p in invoice.payments] == [
        (6426, date(2026, 11, 15)),
        (6426, date(2026, 12, 15)),
    ]
    assert invoice.payments[0].modalita == "MP05"
    assert invoice.payments[0].iban == "IT60X0542811101000000123456"


def test_line_totals_keep_all_their_decimals() -> None:
    # PrezzoTotale may have up to 8 decimals; rounding each line to the cent would make
    # three lines of 0.333333 add up to 0.99 instead of the 1.00 of ImponibileImporto.
    xml = sample_bytes().replace(
        b"<PrezzoTotale>90.00</PrezzoTotale>", b"<PrezzoTotale>0.33333333</PrezzoTotale>"
    )
    [invoice] = parse_invoices(xml)
    assert invoice.lines[0].prezzo_totale == Decimal("0.33333333")


def test_source_is_recorded() -> None:
    [invoice] = parse_invoices(sample_bytes(), source_path="a/b.xml", source_sha256="abc")
    assert (invoice.source_path, invoice.source_sha256) == ("a/b.xml", "abc")


def test_a_file_with_two_bodies_holds_two_invoices() -> None:
    text = sample_bytes().decode("utf-8")
    start = text.index("<FatturaElettronicaBody>")
    end = text.index("</FatturaElettronicaBody>") + len("</FatturaElettronicaBody>")
    second = text[start:end].replace("2026/123", "2026/124")
    invoices = parse_invoices((text[:end] + second + text[end:]).encode("utf-8"))
    assert [invoice.numero for invoice in invoices] == ["2026/123", "2026/124"]
    assert invoices[0].cedente == invoices[1].cedente


def test_any_namespace_prefix_works() -> None:
    renamed = sample_bytes().replace(b"p:FatturaElettronica", b"ns2:FatturaElettronica")
    renamed = renamed.replace(b"xmlns:p=", b"xmlns:ns2=")
    assert parse_invoices(renamed)[0].numero == "2026/123"


def test_windows_1252_files_are_read() -> None:
    text = sample_bytes().decode("utf-8").replace('encoding="UTF-8"', 'encoding="windows-1252"')
    [invoice] = parse_invoices(text.encode("cp1252"))
    assert invoice.lines[1].descrizione == "Manuale di contabilità"


def test_optional_total_may_be_missing() -> None:
    xml = sample_bytes().replace(b"<ImportoTotaleDocumento>128.52</ImportoTotaleDocumento>", b"")
    assert parse_invoices(xml)[0].importo_totale_cents is None


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


def test_a_company_or_a_person_is_required() -> None:
    xml = sample_bytes().replace(b"<Nome>Giulia</Nome>", b"")
    with pytest.raises(InvoiceFormatError, match="Nome"):
        parse_invoices(xml)


def test_a_bad_amount_is_reported() -> None:
    xml = sample_bytes().replace(b"<PrezzoTotale>90.00", b"<PrezzoTotale>novanta")
    with pytest.raises(InvoiceFormatError, match="not a number"):
        parse_invoices(xml)
