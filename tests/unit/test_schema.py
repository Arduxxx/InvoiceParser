from pathlib import Path

import pytest

from invoiceflow.checks.schema import main, schema_findings
from invoiceflow.generator import DatasetConfig, generate_dataset, invoice_to_xml
from invoiceflow.importers.p7m import extract_xml
from tests.unit.test_p7m import make_p7m

SAMPLE = Path(__file__).parent.parent / "fixtures" / "IT01234567897_00001.xml"


def test_the_sample_invoice_is_valid() -> None:
    assert schema_findings(SAMPLE.read_bytes()) == []


def test_every_generated_invoice_is_valid() -> None:
    # errors and frauds planted by the generator keep the format valid: only the values change
    dataset = generate_dataset(DatasetConfig(n_invoices=200, seed=8))
    for generated in dataset.invoices:
        assert schema_findings(invoice_to_xml(generated.invoice, "00001")) == [], generated.file_id


def test_a_missing_element_is_named() -> None:
    xml = SAMPLE.read_bytes().replace(b"<Numero>2026/123</Numero>", b"")
    [finding] = schema_findings(xml)
    assert finding.code == "schema"
    assert "Numero" in finding.message


def test_a_value_in_the_wrong_format() -> None:
    xml = SAMPLE.read_bytes().replace(b"<CAP>20121</CAP>", b"<CAP>2012</CAP>")
    [finding] = schema_findings(xml)
    assert "CAP" in finding.message and "CAP" in finding.field


def test_elements_in_the_wrong_order() -> None:
    xml = SAMPLE.read_bytes().replace(
        b"<Divisa>EUR</Divisa>\n        <Data>2026-10-15</Data>",
        b"<Data>2026-10-15</Data>\n        <Divisa>EUR</Divisa>",
    )
    assert schema_findings(xml) != []


def test_not_xml() -> None:
    [finding] = schema_findings(b"not xml")
    assert finding.message.startswith("not well-formed XML")


def test_a_signed_file_is_validated_after_extraction() -> None:
    assert schema_findings(extract_xml(make_p7m(SAMPLE.read_bytes()))) == []


def test_command_line(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    broken = tmp_path / "broken.xml"
    broken.write_bytes(SAMPLE.read_bytes().replace(b"<CAP>20121</CAP>", b"<CAP>x</CAP>"))
    assert main([str(SAMPLE), str(broken)]) == 1
    output = capsys.readouterr().out
    assert f"{SAMPLE.name}: valid" in output
    assert "broken.xml: 1 problems" in output
