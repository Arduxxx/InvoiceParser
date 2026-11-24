from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from invoiceflow import storage
from invoiceflow.checks import check_invoice
from invoiceflow.duplicates import find_duplicates, load_invoices
from invoiceflow.importers.ingest import ingest_folder
from invoiceflow.importers.pdf_parser import invoice_from_text, parse_pdf_invoice
from invoiceflow.importers.xml_parser import InvoiceFormatError
from tests.pdf_helpers import make_pdf
from tests.unit.test_xml_parser import SAMPLE

IBAN = "IT60X0542811101000000123456"
LINES = [
    "Rossi Informatica S.r.l.",
    "Via Roma 1, 20121 Milano",
    "Partita IVA: IT01234567897",
    "Spett.le Studio Bianchi",
    "P.IVA 12345678903",
    "FATTURA N. 2026/042 del 15/10/2026",
    "Assistenza tecnica 10 ore 100,00 1.000,00",
    "Aliquota Imponibile Imposta",
    "22% 1.000,00 220,00",
    "Totale documento EUR 1.220,00",
    "Scadenza pagamento: 14/11/2026",
    "IBAN IT60 X054 2811 1010 0000 0123 456",
]


def text(*replacements: tuple[str, str]) -> str:
    joined = "\n".join(LINES)
    for old, new in replacements:
        assert old in joined
        joined = joined.replace(old, new)
    return joined


def test_reads_a_real_pdf_file() -> None:
    invoice = parse_pdf_invoice(make_pdf(LINES), source_path="a.pdf", source_sha256="abc")
    assert (invoice.numero, invoice.data) == ("2026/042", date(2026, 10, 15))
    assert (invoice.cedente.denominazione, invoice.cedente.partita_iva) == (
        "Rossi Informatica S.r.l.",
        "01234567897",
    )
    assert (invoice.cessionario.denominazione, invoice.cessionario.partita_iva) == (
        "Studio Bianchi",
        "12345678903",
    )
    assert invoice.importo_totale_cents == 122000
    [summary] = invoice.vat_summary
    assert (summary.aliquota_iva, summary.imponibile_cents, summary.imposta_cents) == (
        Decimal("22.00"),
        100000,
        22000,
    )
    [payment] = invoice.payments
    assert (payment.iban, payment.data_scadenza, payment.importo_cents) == (
        IBAN,
        date(2026, 11, 14),
        122000,
    )
    assert (invoice.source_path, invoice.source_sha256) == ("a.pdf", "abc")
    assert check_invoice(invoice) == []


SDI_VIEW = """FATTURA ELETTRONICA
Dati del cedente / prestatore
Dati anagrafici
Identificativo fiscale ai fini IVA: IT01234567897
Codice fiscale: 01234567897
Denominazione: Rossi Informatica S.r.l.
Dati del cessionario / committente
Dati anagrafici
Codice Fiscale: RSSMRA80A01F205X
Nome: Mario
Cognome: Bianchi
Dati generali del documento
Tipologia documento: TD01 (fattura)
Valuta importi: EUR
Data documento: 2026-10-15 (15 Ottobre 2026)
Numero documento: 2026/42
Importo totale documento: 61.00
Dati relativi alle linee di dettaglio della fornitura
Nr. linea: 1
Descrizione bene/servizio: Consulenza
Quantità: 2.00
Valore unitario: 25.00
Valore totale: 50.00
IVA (%) 22.00
Dati di riepilogo per aliquota IVA e natura
Aliquota IVA (%) 22.00
Totale imponibile/importo: 50.00
Totale imposta: 11.00
Dati relativi al pagamento
Dettaglio pagamento
Modalità: MP05 (bonifico)
Data scadenza pagamento: 2026-11-14 (14 Novembre 2026)
Importo: 61.00
IBAN: IT60X0542811101000000123456
"""


def test_reads_the_official_sdi_view() -> None:
    invoice = invoice_from_text(SDI_VIEW)
    assert (invoice.numero, invoice.data, invoice.tipo_documento) == (
        "2026/42",
        date(2026, 10, 15),
        "TD01",
    )
    assert (invoice.cedente.partita_iva, invoice.cedente.denominazione) == (
        "01234567897",
        "Rossi Informatica S.r.l.",
    )
    assert (invoice.cessionario.nome, invoice.cessionario.cognome) == ("Mario", "Bianchi")
    [line] = invoice.lines
    assert (line.quantita, line.prezzo_unitario, line.prezzo_totale) == (
        Decimal("2.00"),
        Decimal("25.00"),
        Decimal("50.00"),
    )
    [payment] = invoice.payments
    assert (payment.modalita, payment.data_scadenza, payment.importo_cents, payment.iban) == (
        "MP05",
        date(2026, 11, 14),
        6100,
        IBAN,
    )
    assert check_invoice(invoice) == []


def test_the_sdi_view_may_have_no_payment_section() -> None:
    assert invoice_from_text(SDI_VIEW.split("Dati relativi al pagamento")[0]).payments == []


def test_a_wrong_line_in_the_sdi_view_is_caught() -> None:
    invoice = invoice_from_text(SDI_VIEW.replace("Quantità: 2.00", "Quantità: 3.00"))
    assert "line_total" in {finding.code for finding in check_invoice(invoice)}


@pytest.mark.parametrize(
    "header",
    ["Fattura n° FT00012", "Numero fattura: FT00012", "Fattura FT00012", "FATTURA NR. FT00012"],
)
def test_number_labels(header: str) -> None:
    changed = text(("FATTURA N. 2026/042 del 15/10/2026", f"{header}\nData: 15/10/2026"))
    assert invoice_from_text(changed).numero == "FT00012"


def test_a_number_with_spaces_around_the_slash_is_read_whole() -> None:
    changed = text(("FATTURA N. 2026/042", "FATTURA N. 2026 / 042"))
    assert invoice_from_text(changed).numero == "2026 / 042"


def test_the_issue_date_is_not_the_due_date() -> None:
    changed = text(
        ("FATTURA N. 2026/042 del 15/10/2026", "Fattura n. 7"),
        ("Scadenza pagamento: 14/11/2026", "Scadenza: 14/11/2026\nData fattura: 15.10.26"),
    )
    assert invoice_from_text(changed).data == date(2026, 10, 15)


def test_a_supplier_label_wins_when_the_customer_comes_first() -> None:
    changed = "\n".join(
        [
            "Cliente: Studio Bianchi",
            "P.IVA 12345678903",
            "Fornitore: Rossi S.r.l.",
            "P.IVA 01234567897",
            *LINES[5:],
        ]
    )
    invoice = invoice_from_text(changed)
    assert (invoice.cedente.denominazione, invoice.cedente.partita_iva) == (
        "Rossi S.r.l.",
        "01234567897",
    )
    assert invoice.cessionario.partita_iva == "12345678903"


def test_several_vat_rates() -> None:
    changed = text(
        ("22% 1.000,00 220,00", "22% 1.000,00 220,00\n10% 100,00 10,00\n0% N4 50,00 0,00"),
        ("EUR 1.220,00", "EUR 1.380,00"),
    )
    invoice = invoice_from_text(changed)
    assert [(s.aliquota_iva, s.natura) for s in invoice.vat_summary] == [
        (Decimal("22.00"), ""),
        (Decimal("10.00"), ""),
        (Decimal("0.00"), "N4"),
    ]
    assert check_invoice(invoice) == []


def test_vat_written_in_words() -> None:
    changed = text(
        ("Aliquota Imponibile Imposta\n", "Imponibile: 1.000,00\n"),
        ("22% 1.000,00 220,00", "IVA 22%: 220,00"),
    )
    assert invoice_from_text(changed).vat_summary[0].imposta_cents == 22000


def test_a_wrong_total_is_caught_by_the_checks() -> None:
    invoice = invoice_from_text(text(("EUR 1.220,00", "EUR 1.300,00")))
    assert "document_total" in {finding.code for finding in check_invoice(invoice)}


@pytest.mark.parametrize(
    "label",
    [
        "Ritenuta d'acconto 20% EUR 200,00",
        "Ritenuta d'acconto (20%): -200,00",
        "Ritenuta: 200,00",
    ],
)
def test_a_payment_net_of_withholding_passes_the_checks(label: str) -> None:
    changed = text(
        (
            "Totale documento EUR 1.220,00",
            f"Totale documento EUR 1.220,00\n{label}\nNetto a pagare EUR 1.020,00",
        ),
    )
    invoice = invoice_from_text(changed)
    assert (invoice.ritenuta_cents, invoice.payments[0].importo_cents) == (20000, 102000)
    assert check_invoice(invoice) == []


def test_a_wrong_net_payment_is_still_reported() -> None:
    changed = text(
        (
            "Totale documento EUR 1.220,00",
            "Totale documento EUR 1.220,00\nRitenuta: 200,00\nNetto a pagare EUR 1.000,00",
        ),
    )
    assert "payment_amount" in {f.code for f in check_invoice(invoice_from_text(changed))}


def test_the_sdi_view_reads_withholding() -> None:
    view = SDI_VIEW.replace(
        "Importo totale documento: 61.00",
        "Importo ritenuta: 6.00\nImporto ritenuta: 4.00\nImporto totale documento: 61.00",
    ).replace("Importo: 61.00", "Importo: 51.00")
    invoice = invoice_from_text(view)
    assert invoice.ritenuta_cents == 1000
    assert check_invoice(invoice) == []


def test_one_cent_of_rounding_passes_in_the_sdi_view() -> None:
    view = SDI_VIEW.replace("Valore totale: 50.00", "Valore totale: 50.01")
    assert check_invoice(invoice_from_text(view)) == []


def test_a_credit_note() -> None:
    changed = text(("FATTURA N. 2026/042", "NOTA DI CREDITO N. 2026/042"))
    assert invoice_from_text(changed).tipo_documento == "TD04"


@pytest.mark.parametrize(
    ("removed", "problem"),
    [
        ("FATTURA N. 2026/042 ", "number"),
        ("Partita IVA: IT01234567897\n", "VAT number"),
        ("22% 1.000,00 220,00", "VAT summary"),
        ("Totale documento EUR 1.220,00", "total"),
    ],
)
def test_a_missing_field_is_named(removed: str, problem: str) -> None:
    changed = text((removed, "")).replace("P.IVA 12345678903", "")
    with pytest.raises(InvoiceFormatError, match=problem):
        invoice_from_text(changed)


def test_files_that_cannot_be_read() -> None:
    with pytest.raises(InvoiceFormatError, match="not a readable PDF"):
        parse_pdf_invoice(b"not a pdf")
    with pytest.raises(InvoiceFormatError, match="no text"):
        parse_pdf_invoice(make_pdf([]))


def test_imported_with_xml_and_recognised_as_a_duplicate(tmp_path: Path) -> None:
    folder = tmp_path / "in"
    folder.mkdir()
    (folder / "a.xml").write_bytes(SAMPLE.read_bytes())
    same_invoice = [
        "Alfa Forniture S.r.l.",
        "Partita IVA: IT01234567897",
        "Fattura n. 2026-123 del 15/10/2026",
        "22% 90,00 19,80",
        "4% 18,00 0,72",
        "Totale documento EUR 128.52",
    ]
    (folder / "copy.PDF").write_bytes(make_pdf(same_invoice))
    (folder / "broken.pdf").write_bytes(b"not a pdf")
    connection = storage.connect(tmp_path / "archive.sqlite")
    try:
        report = ingest_folder(folder, connection)
        groups = find_duplicates(load_invoices(connection))
    finally:
        connection.close()
    assert report.imported == 2
    assert [name for name, _ in report.errors] == ["broken.pdf"]
    assert len(groups) == 1 and len(groups[0]) == 2


NO_VAT = "\n".join(
    [
        "Studio Verdi",
        "p.iva: 01234567897 - c.f.: 01234567897",
        "Spett. Mario Bianchi",
        "fattura n. 7/2026 del 03/11/2026",
        "ART DESCRIZIONE PREZZO IMPORTO IVA",
        "Lezioni private 1,00 300,00 300,00",
        "Spese di trasferta 1,00 50,00 50,00",
        "Totale imponibile € 350,00",
        "Importo Iva € 0,00",
        "Totale fattura € 350,00",
        "Netto a pagare € 350,00",
        "Riepilogo Iva:",
        "Cod IVA Aliquota Imponibile Imposta",
        "N2.2 300,00 0,00",
        "N2.2 50,00 0,00",
        f"iban: {IBAN}",
    ]
)


def test_an_invoice_without_vat_has_one_summary_at_zero() -> None:
    invoice = invoice_from_text(NO_VAT)
    [summary] = invoice.vat_summary
    assert (summary.aliquota_iva, summary.imponibile_cents, summary.imposta_cents) == (
        Decimal("0.00"),
        35000,
        0,
    )
    assert summary.natura == "N2.2"
    assert invoice.importo_totale_cents == 35000
    assert check_invoice(invoice) == []


def test_a_zero_vat_line_does_not_hide_a_missing_summary() -> None:
    # VAT is charged somewhere but no summary can be read: still refused, never guessed
    changed = NO_VAT.replace("Importo Iva € 0,00", "Importo Iva € 77,00")
    with pytest.raises(InvoiceFormatError, match="VAT summary not found"):
        invoice_from_text(changed)
