"""Serialize an Invoice as FatturaPA 1.2 XML (FPR12, invoices between private parties).

Element order follows the official XSD (Schema_del_file_xml_FatturaPA_v1.2.x).
"""

import xml.etree.ElementTree as ET
from decimal import Decimal

from invoiceflow.model import Invoice, Party
from invoiceflow.money import from_cents

NAMESPACE = "http://ivaservizi.agenziaentrate.gov.it/docs/xsd/fatture/v1.2"
ET.register_namespace("p", NAMESPACE)


def _sub(parent: ET.Element, tag: str, text: str | None = None) -> ET.Element:
    element = ET.SubElement(parent, tag)
    if text is not None:
        element.text = text
    return element


def format_amount(cents: int) -> str:
    """Amount with exactly 2 decimals, e.g. 12345 -> '123.45'."""
    return f"{from_cents(cents):.2f}"


def format_decimal(value: Decimal) -> str:
    """Quantity / unit price / rate: at least 2 decimals, no trailing zeros beyond that."""
    exponent = value.normalize().as_tuple().exponent
    places = max(2, -exponent) if isinstance(exponent, int) else 2
    return f"{value:.{places}f}"


def _party(parent: ET.Element, tag: str, party: Party, regime: str | None) -> None:
    node = _sub(parent, tag)
    anagrafici = _sub(node, "DatiAnagrafici")
    if party.partita_iva:
        id_iva = _sub(anagrafici, "IdFiscaleIVA")
        _sub(id_iva, "IdPaese", party.id_paese)
        _sub(id_iva, "IdCodice", party.partita_iva)
    if party.codice_fiscale:
        _sub(anagrafici, "CodiceFiscale", party.codice_fiscale)
    anagrafica = _sub(anagrafici, "Anagrafica")
    if party.denominazione:
        _sub(anagrafica, "Denominazione", party.denominazione)
    else:
        _sub(anagrafica, "Nome", party.nome)
        _sub(anagrafica, "Cognome", party.cognome)
    if regime is not None:
        _sub(anagrafici, "RegimeFiscale", regime)
    sede = _sub(node, "Sede")
    _sub(sede, "Indirizzo", party.indirizzo)
    _sub(sede, "CAP", party.cap)
    _sub(sede, "Comune", party.comune)
    if party.provincia:
        _sub(sede, "Provincia", party.provincia)
    _sub(sede, "Nazione", party.nazione)


def invoice_to_xml(invoice: Invoice, progressivo_invio: str) -> bytes:
    """UTF-8 encoded FatturaPA XML document for `invoice`."""
    root = ET.Element(f"{{{NAMESPACE}}}FatturaElettronica", versione="FPR12")

    header = _sub(root, "FatturaElettronicaHeader")
    trasmissione = _sub(header, "DatiTrasmissione")
    trasmittente = _sub(trasmissione, "IdTrasmittente")
    _sub(trasmittente, "IdPaese", invoice.cedente.id_paese)
    _sub(trasmittente, "IdCodice", invoice.cedente.partita_iva)
    _sub(trasmissione, "ProgressivoInvio", progressivo_invio)
    _sub(trasmissione, "FormatoTrasmissione", "FPR12")
    _sub(trasmissione, "CodiceDestinatario", "0000000")
    # RF19 = regime forfettario: such suppliers charge no VAT (Natura N2.2).
    forfettario = all(line.natura == "N2.2" for line in invoice.lines)
    _party(header, "CedentePrestatore", invoice.cedente, "RF19" if forfettario else "RF01")
    _party(header, "CessionarioCommittente", invoice.cessionario, None)

    body = _sub(root, "FatturaElettronicaBody")
    documento = _sub(_sub(body, "DatiGenerali"), "DatiGeneraliDocumento")
    _sub(documento, "TipoDocumento", invoice.tipo_documento)
    _sub(documento, "Divisa", invoice.divisa)
    _sub(documento, "Data", invoice.data.isoformat())
    _sub(documento, "Numero", invoice.numero)
    if invoice.importo_totale_cents is not None:
        _sub(documento, "ImportoTotaleDocumento", format_amount(invoice.importo_totale_cents))

    beni = _sub(body, "DatiBeniServizi")
    for line in invoice.lines:
        dettaglio = _sub(beni, "DettaglioLinee")
        _sub(dettaglio, "NumeroLinea", str(line.numero_linea))
        _sub(dettaglio, "Descrizione", line.descrizione)
        if line.quantita is not None:
            _sub(dettaglio, "Quantita", format_decimal(line.quantita))
        if line.unita_misura:
            _sub(dettaglio, "UnitaMisura", line.unita_misura)
        _sub(dettaglio, "PrezzoUnitario", format_decimal(line.prezzo_unitario))
        _sub(dettaglio, "PrezzoTotale", format_amount(line.prezzo_totale_cents))
        _sub(dettaglio, "AliquotaIVA", format_decimal(line.aliquota_iva))
        if line.natura:
            _sub(dettaglio, "Natura", line.natura)
    for summary in invoice.vat_summary:
        riepilogo = _sub(beni, "DatiRiepilogo")
        _sub(riepilogo, "AliquotaIVA", format_decimal(summary.aliquota_iva))
        if summary.natura:
            _sub(riepilogo, "Natura", summary.natura)
        _sub(riepilogo, "ImponibileImporto", format_amount(summary.imponibile_cents))
        _sub(riepilogo, "Imposta", format_amount(summary.imposta_cents))
        if summary.esigibilita_iva:
            _sub(riepilogo, "EsigibilitaIVA", summary.esigibilita_iva)

    if invoice.payments:
        pagamento = _sub(body, "DatiPagamento")
        _sub(pagamento, "CondizioniPagamento", "TP02")  # payment in full
        for payment in invoice.payments:
            dettaglio = _sub(pagamento, "DettaglioPagamento")
            _sub(dettaglio, "ModalitaPagamento", payment.modalita)
            if payment.data_scadenza is not None:
                _sub(dettaglio, "DataScadenzaPagamento", payment.data_scadenza.isoformat())
            if payment.importo_cents is not None:
                _sub(dettaglio, "ImportoPagamento", format_amount(payment.importo_cents))
            if payment.iban:
                _sub(dettaglio, "IBAN", payment.iban)

    ET.indent(root)
    xml: bytes = ET.tostring(root, encoding="UTF-8", xml_declaration=True)
    return xml
