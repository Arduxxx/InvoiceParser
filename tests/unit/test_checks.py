from dataclasses import replace
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from invoiceflow.checks import check_invoice, iban_is_valid, partita_iva_is_valid
from invoiceflow.checks.evaluate import evaluate, main
from invoiceflow.generator import DatasetConfig, generate_dataset, write_dataset
from invoiceflow.importers.xml_parser import parse_invoices
from invoiceflow.model import Invoice

SAMPLE = Path(__file__).parent.parent / "fixtures" / "IT01234567897_00001.xml"


def sample() -> Invoice:
    [invoice] = parse_invoices(SAMPLE.read_bytes())
    return invoice


def codes(invoice: Invoice) -> list[str]:
    return [finding.code for finding in check_invoice(invoice)]


def test_the_sample_invoice_passes_every_check() -> None:
    assert check_invoice(sample()) == []


def test_line_total() -> None:
    invoice = sample()
    line = replace(invoice.lines[0], prezzo_totale=Decimal("90.02"))
    findings = check_invoice(replace(invoice, lines=[line, invoice.lines[1]]))
    # the line no longer matches its quantity, and the 22% total no longer matches the lines
    assert [f.code for f in findings] == ["line_total", "taxable_amount"]
    finding = findings[0]
    assert finding.field == "DettaglioLinee[1]/PrezzoTotale"
    assert "20.00 x 4.50" in finding.message


def test_line_total_allows_one_cent() -> None:
    invoice = sample()
    line = replace(invoice.lines[0], prezzo_totale=Decimal("90.01"))
    assert "line_total" not in codes(replace(invoice, lines=[line, invoice.lines[1]]))


def test_missing_quantity_counts_as_one() -> None:
    invoice = sample()
    line = replace(invoice.lines[1], quantita=None)  # 18.00 x 1
    assert codes(replace(invoice, lines=[invoice.lines[0], line])) == []


def test_taxable_amount() -> None:
    invoice = sample()
    summary = replace(invoice.vat_summary[0], imponibile_cents=9100)
    summaries = [summary, invoice.vat_summary[1]]
    assert "taxable_amount" in codes(replace(invoice, vat_summary=summaries))


def test_lines_without_their_vat_summary() -> None:
    invoice = sample()
    [finding] = [
        f
        for f in check_invoice(replace(invoice, vat_summary=invoice.vat_summary[:1]))
        if f.code == "taxable_amount"
    ]
    assert "no DatiRiepilogo" in finding.message


def test_taxable_amount_rounds_only_the_sum() -> None:
    # three lines of 0.333333 add up to 0.999999, which rounds to 1.00: correct
    invoice = sample()
    third = replace(
        invoice.lines[0],
        quantita=Decimal(1),
        prezzo_unitario=Decimal("0.333333"),
        prezzo_totale=Decimal("0.333333"),
    )
    summary = replace(invoice.vat_summary[0], imponibile_cents=100, imposta_cents=22)
    changed = replace(invoice, lines=[third, third, third], vat_summary=[summary])
    assert "taxable_amount" not in codes(changed)


def test_vat_amount() -> None:
    invoice = sample()
    summary = replace(invoice.vat_summary[0], imposta_cents=1990)
    assert "vat_amount" in codes(replace(invoice, vat_summary=[summary, invoice.vat_summary[1]]))


def test_document_total_and_payments() -> None:
    invoice = sample()
    assert codes(replace(invoice, importo_totale_cents=12900)) == [
        "document_total",
        "payment_amount",
    ]
    # the total is optional: without it, there is nothing to compare
    assert codes(replace(invoice, importo_totale_cents=None)) == []


def test_taxable_amount_allows_one_cent_of_rounding() -> None:
    invoice = sample()
    line = replace(invoice.lines[0], prezzo_totale=Decimal("90.01"))
    assert codes(replace(invoice, lines=[line, invoice.lines[1]])) == []


def test_taxable_amount_two_cents_off_is_reported() -> None:
    invoice = sample()
    line = replace(invoice.lines[0], prezzo_totale=Decimal("90.02"))
    assert "taxable_amount" in codes(replace(invoice, lines=[line, invoice.lines[1]]))


def test_payment_net_of_withholding_tax_is_correct() -> None:
    invoice = replace(sample(), ritenuta_cents=2160)
    net = [replace(payment, importo_cents=5346) for payment in invoice.payments]
    assert codes(replace(invoice, payments=net)) == []
    assert codes(invoice) == []  # the full total is accepted too


def test_payment_matching_neither_total_nor_net_is_reported() -> None:
    invoice = replace(sample(), ritenuta_cents=2160)
    wrong = [replace(payment, importo_cents=5000) for payment in invoice.payments]
    [finding] = check_invoice(replace(invoice, payments=wrong))
    assert finding.code == "payment_amount" and "106.92 after withholding" in finding.message


def test_payment_amount() -> None:
    invoice = sample()
    payment = replace(invoice.payments[0], importo_cents=6400)
    assert codes(replace(invoice, payments=[payment, invoice.payments[1]])) == ["payment_amount"]


def test_due_date_before_issue() -> None:
    invoice = sample()
    payment = replace(invoice.payments[0], data_scadenza=date(2026, 10, 14))
    changed = replace(invoice, payments=[payment, invoice.payments[1]])
    assert codes(changed) == ["due_date_before_issue"]


@pytest.mark.parametrize(
    ("vat", "valid"),
    [
        ("01234567897", True),
        ("01234567890", False),  # the number in the official FatturaPA examples
        ("0123456789", False),
        ("0123456789A", False),
        ("０１２３４５６７８９７", False),  # full-width digits are not ASCII digits
    ],
)
def test_partita_iva(vat: str, valid: bool) -> None:
    assert partita_iva_is_valid(vat) is valid


def test_supplier_and_customer_vat_numbers() -> None:
    invoice = sample()
    supplier = replace(invoice.cedente, partita_iva="01234567890")
    assert codes(replace(invoice, cedente=supplier)) == ["supplier_vat_number"]
    customer = replace(invoice.cessionario, id_paese="IT", partita_iva="12345678900")
    assert codes(replace(invoice, cessionario=customer)) == ["customer_vat_number"]
    foreign = replace(invoice.cedente, id_paese="DE", partita_iva="123456789")
    assert codes(replace(invoice, cedente=foreign)) == []  # other countries, other rules


@pytest.mark.parametrize(
    ("iban", "valid"),
    [
        ("IT60X0542811101000000123456", True),
        ("IT60 X054 2811 1010 0000 0123 456", True),  # spaces are allowed when written
        ("it60x0542811101000000123456", True),
        ("IT61X0542811101000000123456", False),  # wrong check digits
        ("IT60X0542811101000000123457", False),  # one digit changed
        ("IT60X054281110100000012345", False),  # too short for Italy
        ("DE89370400440532013000", True),
        ("", False),
    ],
)
def test_iban(iban: str, valid: bool) -> None:
    assert iban_is_valid(iban) is valid


def test_iban_rule() -> None:
    invoice = sample()
    payment = replace(invoice.payments[1], iban="IT60X0542811101000000123457")
    [finding] = check_invoice(replace(invoice, payments=[invoice.payments[0], payment]))
    assert (finding.code, finding.field) == ("iban", "DettaglioPagamento[2]/IBAN")
    no_iban = replace(invoice.payments[1], iban="")  # e.g. paid in cash: nothing to check
    assert codes(replace(invoice, payments=[invoice.payments[0], no_iban])) == []


def test_evaluation_on_a_generated_dataset(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    write_dataset(generate_dataset(DatasetConfig(n_invoices=300, seed=5)), tmp_path)
    result = evaluate(tmp_path)
    assert sum(result.planted.values()) > 0
    assert result.caught == result.planted  # every planted error is caught
    assert result.false_alarms == []
    assert main([str(tmp_path)]) == 0
    assert "false alarms: 0" in capsys.readouterr().out
