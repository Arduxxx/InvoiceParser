from pathlib import Path

import pytest

from invoiceflow import storage
from invoiceflow.anonymize import Anonymizer, anonymize_folder, anonymize_xml, load_key, main
from invoiceflow.antifraud import find_frauds
from invoiceflow.checks import check_invoice, iban_is_valid, partita_iva_is_valid
from invoiceflow.checks.schema import schema_findings
from invoiceflow.generator import DatasetConfig, generate_dataset, write_dataset
from invoiceflow.importers.ingest import ingest_folder
from invoiceflow.importers.xml_parser import InvoiceFormatError, parse_invoices
from tests.unit.test_p7m import make_p7m
from tests.unit.test_xml_parser import SAMPLE

ANONYMIZER = Anonymizer(b"test key")


def test_identifying_values_are_gone_and_the_facts_stay() -> None:
    original = SAMPLE.read_bytes()
    copy = anonymize_xml(original, ANONYMIZER)
    [before], [after] = parse_invoices(original), parse_invoices(copy)
    for secret in (
        before.cedente.denominazione,
        before.cedente.partita_iva,
        before.cedente.indirizzo,
        before.cessionario.codice_fiscale,
        before.payments[0].iban,
    ):
        assert secret and secret.encode() not in copy
    assert (after.numero, after.data, after.importo_totale_cents) == (
        before.numero,
        before.data,
        before.importo_totale_cents,
    )
    assert [p.importo_cents for p in after.payments] == [p.importo_cents for p in before.payments]
    assert schema_findings(copy) == [] and check_invoice(after) == []
    assert partita_iva_is_valid(after.cedente.partita_iva)
    assert iban_is_valid(after.payments[0].iban)


def test_unknown_fields_comments_and_attachments_are_removed() -> None:
    xml = SAMPLE.read_bytes().replace(
        b"</DatiBeniServizi>",
        b"<!-- Mario Rossi --></DatiBeniServizi>"
        b"<Allegati><NomeAttachment>x.pdf</NomeAttachment><Attachment>U0VDUkVU</Attachment>"
        b"</Allegati><CampoNuovo>riservato</CampoNuovo>",
    )
    copy = anonymize_xml(xml, ANONYMIZER)
    for secret in (b"Mario Rossi", b"U0VDUkVU", b"x.pdf", b"riservato"):
        assert secret not in copy


def test_free_text_in_a_line_is_replaced() -> None:
    [before] = parse_invoices(SAMPLE.read_bytes())
    copy = anonymize_xml(SAMPLE.read_bytes(), ANONYMIZER)
    assert before.lines[0].descrizione.encode() not in copy
    assert parse_invoices(copy)[0].lines[1].descrizione == "Riga 2"


def test_a_copy_with_an_identifying_value_is_refused() -> None:
    [invoice] = parse_invoices(SAMPLE.read_bytes())
    vat = invoice.cedente.partita_iva.encode()
    xml = SAMPLE.read_bytes().replace(b"2026/123", b"FT-" + vat)  # VAT number inside the number
    with pytest.raises(InvoiceFormatError, match="identifying value"):
        anonymize_xml(xml, ANONYMIZER)


def test_replacements_are_consistent_and_depend_on_the_key() -> None:
    assert ANONYMIZER.vat_number("01234567897") == ANONYMIZER.vat_number(" 01234567897 ")
    iban = "IT60X0542811101000000123456"
    assert ANONYMIZER.iban(iban) == ANONYMIZER.iban("IT60 X054 2811 1010 0000 0123 456")
    assert ANONYMIZER.vat_number("01234567897") != Anonymizer(b"other").vat_number("01234567897")


def test_wrong_check_digits_stay_wrong() -> None:
    assert not partita_iva_is_valid(ANONYMIZER.vat_number("01234567890"))
    assert not iban_is_valid(ANONYMIZER.iban("IT60X0542811101000000123457"))


def test_a_person_gets_a_fake_tax_code() -> None:
    fake = ANONYMIZER.tax_code("RSSMRA80A01F205X")
    assert len(fake) == 16 and fake != "RSSMRA80A01F205X"


def test_folder_with_xml_and_p7m(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    source, target, key = tmp_path / "in", tmp_path / "out", tmp_path / "secret" / "a.key"
    source.mkdir()
    data = SAMPLE.read_bytes()
    (source / SAMPLE.name).write_bytes(data)
    other = data.replace(b"2026/123", b"2026/200")
    (source / "IT01234567897_00002.xml.p7m").write_bytes(make_p7m(other))
    (source / "broken.xml").write_bytes(b"<FatturaElettronica>")
    before = {p.name: p.read_bytes() for p in source.iterdir()}

    assert main([str(source), str(target), "--key", str(key)]) == 1
    output = capsys.readouterr().out
    assert "Wrote 2 anonymized copies" in output and "skipped broken.xml" in output
    copies = sorted(target.iterdir())
    assert len(copies) == 2 and all(p.suffix == ".xml" for p in copies)
    assert not any("01234567897" in p.name for p in copies)
    assert {p.name: p.read_bytes() for p in source.iterdir()} == before  # originals untouched
    assert anonymize_folder(source, target, key)[:2] == (0, 2)  # same key, same files


def test_output_inside_the_originals_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="separate"):
        anonymize_folder(tmp_path, tmp_path / "out", tmp_path / "a.key")


def test_the_key_is_kept(tmp_path: Path) -> None:
    key = tmp_path / "a.key"
    assert load_key(key) == load_key(key)


def test_the_rules_find_the_same_things_on_the_copies(tmp_path: Path) -> None:
    write_dataset(generate_dataset(DatasetConfig(n_invoices=150, seed=4)), tmp_path / "gen")
    anonymize_folder(tmp_path / "gen" / "invoices", tmp_path / "copies", tmp_path / "a.key")
    results = []
    for folder, name in ((tmp_path / "gen" / "invoices", "a"), (tmp_path / "copies", "b")):
        connection = storage.connect(tmp_path / f"{name}.sqlite")
        report = ingest_folder(folder, connection)
        # fake names are unrelated to the real ones, so look-alike names are not preserved
        flags = [flag.rule for flag in find_frauds(connection) if flag.rule != "clone_supplier"]
        rules = sorted(flags)
        connection.close()
        results.append((report.imported, report.with_problems, rules))
    assert results[0] == results[1]
