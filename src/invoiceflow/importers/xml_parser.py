"""Reads FatturaPA XML files (format FPR12 or FPA12) into the shared Invoice model.

The objects built here are those of invoiceflow.model, the data contract shared with the
generator and the checks: field names follow the FatturaPA element names.

The file is parsed from bytes, never decoded by us: the XML declaration says which
encoding it uses (usually UTF-8, sometimes windows-1252), and the parser follows it on
every operating system.

One file can hold several invoices (a "lotto"): one header with the supplier and the
customer, then one FatturaElettronicaBody per invoice. parse_invoices() returns all of them.
"""

import xml.etree.ElementTree as ET
from datetime import date
from decimal import Decimal, InvalidOperation

from invoiceflow.model import Invoice, Line, Party, Payment, VatSummary
from invoiceflow.money import to_cents


class InvoiceFormatError(ValueError):
    """The file is not a FatturaPA invoice we can read; the message says why."""


def parse_invoices(xml: bytes, *, source_path: str = "", source_sha256: str = "") -> list[Invoice]:
    """The invoices in a FatturaPA file. source_path and source_sha256 (the hash of the raw
    file, before any .p7m extraction) are copied into every invoice, to trace where it came
    from and to recognise a file loaded twice."""
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
    cedente = _party(_child(header, "CedentePrestatore"))
    cessionario = _party(_child(header, "CessionarioCommittente"))
    bodies = root.findall("FatturaElettronicaBody")
    if not bodies:
        raise InvoiceFormatError("missing <FatturaElettronicaBody>")
    return [_invoice(body, cedente, cessionario, source_path, source_sha256) for body in bodies]


def _drop_namespaces(root: ET.Element) -> None:
    """'{http://...}FatturaElettronica' -> 'FatturaElettronica'. The root is qualified
    (prefix p: or similar), its children usually are not; this way both read the same."""
    for element in root.iter():
        element.tag = element.tag.rpartition("}")[2]


def _invoice(
    body: ET.Element, cedente: Party, cessionario: Party, source_path: str, source_sha256: str
) -> Invoice:
    general = _child(body, "DatiGenerali/DatiGeneraliDocumento")
    total = _optional(general, "ImportoTotaleDocumento")
    goods = _child(body, "DatiBeniServizi")
    return Invoice(
        tipo_documento=_required(general, "TipoDocumento"),
        divisa=_required(general, "Divisa"),
        data=_date(_required(general, "Data")),
        numero=_required(general, "Numero"),
        importo_totale_cents=None if total is None else to_cents(_decimal(total)),
        cedente=cedente,
        cessionario=cessionario,
        lines=[_line(element) for element in goods.findall("DettaglioLinee")],
        vat_summary=[_summary(element) for element in goods.findall("DatiRiepilogo")],
        payments=[
            _payment(element) for element in body.findall("DatiPagamento/DettaglioPagamento")
        ],
        source_path=source_path,
        source_sha256=source_sha256,
    )


def _party(element: ET.Element) -> Party:
    data = _child(element, "DatiAnagrafici")
    sede = _child(element, "Sede")
    indirizzo = _required(sede, "Indirizzo")
    civico = _optional(sede, "NumeroCivico")
    denominazione = _text(data, "Anagrafica/Denominazione")
    if not denominazione:
        # a person: Nome and Cognome take the place of Denominazione
        _required(data, "Anagrafica/Nome")
        _required(data, "Anagrafica/Cognome")
    return Party(
        denominazione=denominazione,
        nome=_text(data, "Anagrafica/Nome"),
        cognome=_text(data, "Anagrafica/Cognome"),
        id_paese=_text(data, "IdFiscaleIVA/IdPaese"),
        partita_iva=_text(data, "IdFiscaleIVA/IdCodice"),
        codice_fiscale=_text(data, "CodiceFiscale"),
        indirizzo=indirizzo if civico is None else f"{indirizzo} {civico}",
        cap=_required(sede, "CAP"),
        comune=_required(sede, "Comune"),
        provincia=_text(sede, "Provincia"),
        nazione=_required(sede, "Nazione"),
    )


def _line(element: ET.Element) -> Line:
    quantita = _optional(element, "Quantita")
    return Line(
        numero_linea=_line_number(_required(element, "NumeroLinea")),
        descrizione=_required(element, "Descrizione"),
        quantita=None if quantita is None else _decimal(quantita),
        unita_misura=_text(element, "UnitaMisura"),
        prezzo_unitario=_decimal(_required(element, "PrezzoUnitario")),
        prezzo_totale_cents=to_cents(_decimal(_required(element, "PrezzoTotale"))),
        aliquota_iva=_decimal(_required(element, "AliquotaIVA")),
        natura=_text(element, "Natura"),
    )


def _summary(element: ET.Element) -> VatSummary:
    return VatSummary(
        aliquota_iva=_decimal(_required(element, "AliquotaIVA")),
        imponibile_cents=to_cents(_decimal(_required(element, "ImponibileImporto"))),
        imposta_cents=to_cents(_decimal(_required(element, "Imposta"))),
        esigibilita_iva=_text(element, "EsigibilitaIVA"),
        natura=_text(element, "Natura"),
    )


def _payment(element: ET.Element) -> Payment:
    scadenza = _optional(element, "DataScadenzaPagamento")
    return Payment(
        modalita=_required(element, "ModalitaPagamento"),
        data_scadenza=None if scadenza is None else _date(scadenza),
        importo_cents=to_cents(_decimal(_required(element, "ImportoPagamento"))),
        iban=_text(element, "IBAN"),
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


def _text(element: ET.Element, path: str) -> str:
    """An optional text field; "" when absent, as in the shared model."""
    return _optional(element, path) or ""


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
