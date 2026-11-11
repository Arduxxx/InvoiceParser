"""Reads FatturaPA XML files (format FPR12 or FPA12) into Invoice objects.

The file is parsed from bytes, never decoded by us: the XML declaration says which
encoding it uses (usually UTF-8, sometimes windows-1252), and the parser follows it on
every operating system.

One file can hold several invoices (a "lotto"): one header with the supplier and the
customer, then one FatturaElettronicaBody per invoice. parse_invoices() returns all of them.
"""

import xml.etree.ElementTree as ET
from datetime import date
from decimal import Decimal, InvalidOperation

from invoiceflow.importers.model import Address, Invoice, Line, Party, Payment, VatSummary
from invoiceflow.money import to_cents


class InvoiceFormatError(ValueError):
    """The file is not a FatturaPA invoice we can read; the message says why."""


def parse_invoices(xml: bytes) -> list[Invoice]:
    if b"<!DOCTYPE" in xml[:4096]:
        # Invoices never need a DOCTYPE; refusing it blocks entity tricks in untrusted files.
        raise InvoiceFormatError("the file declares a DOCTYPE, which invoices never use")
    try:
        root = ET.fromstring(xml)
    except ET.ParseError as error:
        raise InvoiceFormatError(f"not well-formed XML: {error}") from error
    _drop_namespaces(root)
    if root.tag != "FatturaElettronica":
        raise InvoiceFormatError(f"the root element is <{root.tag}>, not <FatturaElettronica>")

    header = _child(root, "FatturaElettronicaHeader")
    supplier = _party(_child(header, "CedentePrestatore"))
    customer = _party(_child(header, "CessionarioCommittente"))
    bodies = root.findall("FatturaElettronicaBody")
    if not bodies:
        raise InvoiceFormatError("missing <FatturaElettronicaBody>")
    return [_invoice(body, supplier, customer) for body in bodies]


def _drop_namespaces(root: ET.Element) -> None:
    """'{http://...}FatturaElettronica' -> 'FatturaElettronica'. The root is qualified
    (prefix p: or similar), its children usually are not; this way both read the same."""
    for element in root.iter():
        element.tag = element.tag.rpartition("}")[2]


def _invoice(body: ET.Element, supplier: Party, customer: Party) -> Invoice:
    general = _child(body, "DatiGenerali/DatiGeneraliDocumento")
    total = _optional(general, "ImportoTotaleDocumento")
    goods = _child(body, "DatiBeniServizi")
    return Invoice(
        document_type=_required(general, "TipoDocumento"),
        currency=_required(general, "Divisa"),
        issue_date=_date(_required(general, "Data")),
        number=_required(general, "Numero"),
        total_cents=None if total is None else to_cents(_decimal(total)),
        supplier=supplier,
        customer=customer,
        lines=tuple(_line(element) for element in goods.findall("DettaglioLinee")),
        vat_summaries=tuple(_summary(element) for element in goods.findall("DatiRiepilogo")),
        payments=tuple(
            _payment(element) for element in body.findall("DatiPagamento/DettaglioPagamento")
        ),
    )


def _party(element: ET.Element) -> Party:
    data = _child(element, "DatiAnagrafici")
    company = _optional(data, "Anagrafica/Denominazione")
    if company is None:
        name = f"{_required(data, 'Anagrafica/Nome')} {_required(data, 'Anagrafica/Cognome')}"
    else:
        name = company
    return Party(
        name=name,
        vat_country=_optional(data, "IdFiscaleIVA/IdPaese"),
        vat_number=_optional(data, "IdFiscaleIVA/IdCodice"),
        fiscal_code=_optional(data, "CodiceFiscale"),
        address=_address(_child(element, "Sede")),
    )


def _address(element: ET.Element) -> Address:
    street = _required(element, "Indirizzo")
    house_number = _optional(element, "NumeroCivico")
    return Address(
        street=street if house_number is None else f"{street} {house_number}",
        cap=_required(element, "CAP"),
        city=_required(element, "Comune"),
        province=_optional(element, "Provincia"),
        country=_required(element, "Nazione"),
    )


def _line(element: ET.Element) -> Line:
    quantity = _optional(element, "Quantita")
    return Line(
        number=_line_number(_required(element, "NumeroLinea")),
        description=_required(element, "Descrizione"),
        quantity=None if quantity is None else _decimal(quantity),
        unit_price=_decimal(_required(element, "PrezzoUnitario")),
        total_cents=to_cents(_decimal(_required(element, "PrezzoTotale"))),
        vat_rate=_decimal(_required(element, "AliquotaIVA")),
        nature=_optional(element, "Natura"),
    )


def _summary(element: ET.Element) -> VatSummary:
    return VatSummary(
        vat_rate=_decimal(_required(element, "AliquotaIVA")),
        nature=_optional(element, "Natura"),
        taxable_cents=to_cents(_decimal(_required(element, "ImponibileImporto"))),
        tax_cents=to_cents(_decimal(_required(element, "Imposta"))),
    )


def _payment(element: ET.Element) -> Payment:
    due = _optional(element, "DataScadenzaPagamento")
    return Payment(
        method=_required(element, "ModalitaPagamento"),
        amount_cents=to_cents(_decimal(_required(element, "ImportoPagamento"))),
        due_date=None if due is None else _date(due),
        iban=_optional(element, "IBAN"),
    )


def _child(element: ET.Element, path: str) -> ET.Element:
    found = element.find(path)
    if found is None:
        raise InvoiceFormatError(f"missing <{path}> inside <{element.tag}>")
    return found


def _optional(element: ET.Element, path: str) -> str | None:
    found = element.find(path)
    if found is None or found.text is None or not found.text.strip():
        return None
    return found.text.strip()


def _required(element: ET.Element, path: str) -> str:
    text = _optional(element, path)
    if text is None:
        raise InvoiceFormatError(f"missing or empty <{path}> inside <{element.tag}>")
    return text


def _decimal(text: str) -> Decimal:
    try:
        number = Decimal(text)
    except InvalidOperation as error:
        raise InvoiceFormatError(f"{text!r} is not a number") from error
    if not number.is_finite():
        raise InvoiceFormatError(f"{text!r} is not a number")
    return number


def _line_number(text: str) -> int:
    if not text.isascii() or not text.isdigit():
        raise InvoiceFormatError(f"{text!r} is not a line number")
    return int(text)


def _date(text: str) -> date:
    try:
        return date.fromisoformat(text)
    except ValueError as error:
        raise InvoiceFormatError(f"{text!r} is not a date (YYYY-MM-DD)") from error
