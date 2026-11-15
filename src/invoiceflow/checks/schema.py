"""Validates a FatturaPA file against the official XML schema (XSD).

The schema is the Agenzia delle Entrate's Schema_VFPR12_v1.2.3.xsd, which covers both formats
(FPA12 for public administrations, FPR12 between private parties). It checks the structure:
which elements exist, in which order, how many times, and the format of each value (dates,
amounts with two decimals, a CAP of five digits, Latin-1 text...). It cannot check that the
amounts add up or that a partita IVA has the right check digit: those are the other checks.

The schema imports the XML-signature schema from w3.org. A local copy of it is handed over
instead, so validation never goes on the network and works offline on every machine.

    python -m invoiceflow.checks.schema data/real/IT01234567890_00001.xml
"""

import argparse
import sys
from functools import cache
from pathlib import Path
from typing import Any

from lxml import etree

from invoiceflow.checks.finding import Finding

SCHEMAS = Path(__file__).parent / "schemas"
INVOICE_SCHEMA = SCHEMAS / "Schema_VFPR12_v1.2.3.xsd"
SIGNATURE_SCHEMA = SCHEMAS / "xmldsig-core-schema.xsd"
SIGNATURE_URL = "http://www.w3.org/TR/2002/REC-xmldsig-core-20020212/xmldsig-core-schema.xsd"


def schema_findings(xml: bytes) -> list[Finding]:
    """What the official schema finds wrong in the XML (empty list: the file is valid).
    For a .p7m, pass the XML extracted from it (importers.p7m.extract_xml)."""
    parser = etree.XMLParser(no_network=True, resolve_entities=False)
    try:
        document = etree.fromstring(xml, parser)
    except etree.XMLSyntaxError as error:
        return [Finding("schema", "", f"not well-formed XML: {error}")]
    schema = _official_schema()
    if schema.validate(document):
        return []
    return [
        Finding("schema", error.path or "", f"line {error.line}: {error.message}")
        for error in schema.error_log
    ]


class _LocalSignatureSchema(etree.Resolver):
    """Answers the schema's import of the signature schema with our local copy."""

    def resolve(self, system_url: str | None, public_id: str | None, context: object) -> Any:
        if system_url == SIGNATURE_URL:
            return self.resolve_string(SIGNATURE_SCHEMA.read_bytes(), context)
        return None


@cache
def _official_schema() -> etree.XMLSchema:
    """Compiled once per process: compiling takes a moment, validating afterwards is fast.
    Files are read as bytes by Python, so paths with spaces or accents work on Windows too."""
    parser = etree.XMLParser(no_network=True)
    parser.resolvers.add(_LocalSignatureSchema())
    return etree.XMLSchema(etree.fromstring(INVOICE_SCHEMA.read_bytes(), parser))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate FatturaPA files against the XSD.")
    parser.add_argument("files", type=Path, nargs="+", help=".xml files to validate")
    args = parser.parse_args(argv)
    invalid = 0
    for path in args.files:
        findings = schema_findings(path.read_bytes())
        if findings:
            invalid += 1
            print(f"{path.name}: {len(findings)} problems")
            for finding in findings:
                print(f"  {finding.message}")
        else:
            print(f"{path.name}: valid")
    return 1 if invalid else 0


if __name__ == "__main__":
    sys.exit(main())
