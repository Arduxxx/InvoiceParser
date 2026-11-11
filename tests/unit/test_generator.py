import csv
import xml.etree.ElementTree as ET
from collections import Counter
from datetime import date
from decimal import Decimal
from pathlib import Path
from random import Random

import pytest

from invoiceflow.generator import (
    ERROR_KINDS,
    DatasetConfig,
    corrupt,
    generate_dataset,
    invoice_to_xml,
    make_client,
    make_invoice,
    make_supplier,
)
from invoiceflow.generator.__main__ import main
from invoiceflow.generator.dataset import progressivo
from invoiceflow.generator.ids import (
    iban_check_digits,
    italian_cin,
    partita_iva_check_digit,
    random_italian_iban,
    random_partita_iva,
)
from invoiceflow.generator.scenarios import near_identical_name, reformat_number
from invoiceflow.generator.xml_writer import NAMESPACE, format_decimal
from invoiceflow.model import Invoice
from tests.generator_helpers import broken_rules, iban_is_valid, partita_iva_is_valid


def _invoice(seed: int) -> Invoice:
    rng = Random(seed)
    supplier = make_supplier(rng)
    return make_invoice(rng, supplier, make_client(rng), date(2026, 3, 1), "2026/001")


# --- identifiers -----------------------------------------------------------------------------

def test_partita_iva_check_digit_known_example() -> None:
    assert partita_iva_check_digit("0123456789") == "7"


def test_italian_iban_known_example() -> None:
    # Standard example IBAN: IT60 X054 2811 1010 0000 0123 456
    assert italian_cin("05428", "11101", "000000123456") == "X"
    assert iban_check_digits("IT", "X0542811101000000123456") == "60"


def test_random_identifiers_are_valid() -> None:
    rng = Random(1)
    for _ in range(500):
        assert partita_iva_is_valid(random_partita_iva(rng))
        iban = random_italian_iban(rng)
        assert len(iban) == 27 and iban_is_valid(iban)


# --- correct invoices ------------------------------------------------------------------------

def test_generated_invoice_is_correct() -> None:
    assert broken_rules(_invoice(7)) == set()


def test_money_is_never_float() -> None:
    inv = _invoice(3)
    assert isinstance(inv.importo_totale_cents, int)
    for line in inv.lines:
        assert isinstance(line.prezzo_unitario, Decimal)
        assert isinstance(line.prezzo_totale, Decimal)


# --- errors ----------------------------------------------------------------------------------

# Changing one field can also break the rules that are computed from it.
_CASCADES = {
    "line_total": {"taxable_amount"},                     # riepilogo != sum of lines
    "vat_amount": {"document_total"},                     # total != imponibile + imposta
    "taxable_amount": {"vat_amount", "document_total"},
    "document_total": {"payment_amount"},                 # payment != declared total
}


@pytest.mark.parametrize("kind", ERROR_KINDS)
def test_each_error_breaks_its_rule_and_nothing_unrelated(kind: str) -> None:
    for seed in range(30):
        corrupted, got_kind, detail = corrupt(_invoice(seed), Random(seed), kind)
        assert got_kind == kind and detail
        broken = broken_rules(corrupted)
        assert kind in broken
        assert broken <= {kind} | _CASCADES.get(kind, set())


def test_unknown_error_kind_is_rejected() -> None:
    with pytest.raises(ValueError):
        corrupt(_invoice(0), Random(0), "nope")


# --- names and numbers -----------------------------------------------------------------------

def test_near_identical_name_differs_slightly() -> None:
    rng = Random(0)
    for name in ("Rossi Informatica S.r.l.", "Tecnoenergia S.p.A.", "Net Gallo & C. S.a.s."):
        clone = near_identical_name(name, rng)
        assert clone != name
        assert abs(len(clone) - len(name)) <= 3


def test_reformat_number_keeps_the_digits() -> None:
    rng = Random(0)
    for numero in ("42", "2026/042", "FT00042", "A0007", "2026-0042"):
        other = reformat_number(numero, rng)
        assert other != numero
        assert [int(x) for x in _digit_runs(other)] == [int(x) for x in _digit_runs(numero)]


def _digit_runs(text: str) -> list[str]:
    import re
    return re.findall(r"\d+", text)


def test_progressivo() -> None:
    assert progressivo(0) == "00000"
    assert progressivo(35) == "0000Z"
    assert progressivo(36) == "00010"
    with pytest.raises(ValueError):
        progressivo(36**5)


# --- dataset ---------------------------------------------------------------------------------

def test_dataset_is_deterministic() -> None:
    config = DatasetConfig(n_invoices=200, seed=5)
    assert generate_dataset(config) == generate_dataset(config)


def test_dataset_labels_match_content() -> None:
    config = DatasetConfig(n_invoices=2000, n_suppliers=20, error_rate=0.1,
                           duplicate_rate=0.05, fraud_rate=0.1, seed=11)
    dataset = generate_dataset(config)
    items = dataset.invoices
    assert len(items) == 2000
    assert len({i.file_id for i in items}) == len(items)
    counts = Counter(i.label for i in items)
    assert counts["duplicate"] == 100
    assert counts["error"] > 0 and counts["fraud"] > 0

    by_id = {i.file_id: i for i in items}
    registry = {s.party.partita_iva: s for s in dataset.suppliers}
    for item in items:
        if item.label == "error":
            assert item.kind in broken_rules(item.invoice)
            continue
        assert broken_rules(item.invoice) == set(), item  # ok, duplicates and frauds add up
        if item.label == "duplicate":
            original = by_id[item.duplicate_of]
            assert original.label == "ok"
            assert item.invoice.lines == original.invoice.lines
            assert item.invoice.cedente == original.invoice.cedente
        elif item.label == "fraud":
            vat = item.invoice.cedente.partita_iva
            if item.kind in ("iban_change", "outlier_amount"):
                supplier = registry[vat]
                same_iban = item.invoice.payments[0].iban == supplier.iban
                assert same_iban == (item.kind == "outlier_amount")
            else:
                assert vat not in registry


def test_outliers_are_far_above_usual_amounts() -> None:
    dataset = generate_dataset(DatasetConfig(n_invoices=3000, fraud_rate=0.1, seed=2))
    usual: dict[str, list[int]] = {}
    for item in dataset.invoices:
        if item.label == "ok":
            key = item.invoice.cedente.partita_iva
            usual.setdefault(key, []).append(item.invoice.importo_totale_cents or 0)
    outliers = [i for i in dataset.invoices if i.kind == "outlier_amount"]
    assert outliers
    above = sum((i.invoice.importo_totale_cents or 0) > max(usual[i.invoice.cedente.partita_iva])
                for i in outliers)
    assert above / len(outliers) > 0.8


def test_frauds_need_supplier_history() -> None:
    dataset = generate_dataset(DatasetConfig(n_invoices=500, fraud_rate=1.0, error_rate=0,
                                             duplicate_rate=0, seed=4))
    assert Counter(i.label for i in dataset.invoices)["ok"] > 0


# --- XML -------------------------------------------------------------------------------------

def test_format_decimal() -> None:
    assert format_decimal(Decimal("3")) == "3.00"
    assert format_decimal(Decimal("22.00")) == "22.00"
    assert format_decimal(Decimal("0.234567")) == "0.234567"
    assert format_decimal(Decimal("1E+2")) == "100.00"


def test_xml_contains_the_invoice_data() -> None:
    inv = _invoice(9)
    root = ET.fromstring(invoice_to_xml(inv, "00001"))
    assert root.tag == f"{{{NAMESPACE}}}FatturaElettronica"
    assert root.get("versione") == "FPR12"
    assert root.findtext(".//CedentePrestatore//IdCodice") == inv.cedente.partita_iva
    assert root.findtext(".//DatiGeneraliDocumento/Numero") == inv.numero
    total = Decimal(root.findtext(".//ImportoTotaleDocumento") or "")
    assert total * 100 == inv.importo_totale_cents
    line_totals = [Decimal(e.text or "") for e in root.iter("PrezzoTotale")]
    taxable = [Decimal(e.text or "") for e in root.iter("ImponibileImporto")]
    assert sum(line_totals) == sum(taxable)
    assert root.findtext(".//IBAN") == inv.payments[0].iban


# --- command line ----------------------------------------------------------------------------

def test_cli_writes_invoices_and_labels(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    out = tmp_path / "run"
    assert main(["--out", str(out), "--n", "50", "--seed", "1"]) == 0
    assert "Wrote 50 invoices" in capsys.readouterr().out
    assert not list(out.glob("*.xml"))  # XMLs live in invoices/, apart from the labels
    assert len(list((out / "invoices").glob("*.xml"))) == 50
    with open(out / "labels.csv", encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 50
    assert all(row["file"].startswith("invoices/") for row in rows)
    assert all((out / row["file"]).is_file() for row in rows)
    assert all((out / row["duplicate_of"]).is_file() for row in rows if row["duplicate_of"])
    assert (out / "suppliers.csv").is_file()


def test_cli_refuses_non_empty_directory(tmp_path: Path) -> None:
    assert main(["--out", str(tmp_path), "--n", "5"]) == 0
    with pytest.raises(SystemExit):
        main(["--out", str(tmp_path), "--n", "5"])
