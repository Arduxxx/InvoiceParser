"""Makes anonymized copies of real FatturaPA invoices (.xml and .xml.p7m).

    python -m invoiceflow.anonymize data/originals data/real/xml

The originals are only read. Every copy is a plain .xml: a .p7m is unwrapped first, so its
signature and the signer's certificate are left behind.

The idea is an allow-list. Each value in the invoice is either
- KEPT, because the checks need it (amounts, dates, VAT rates, invoice number, codes);
- REPLACED by a fake value (names, VAT numbers, tax codes, IBANs, addresses);
- REMOVED, which is what happens to anything else, including fields we did not think of.

Replacements are consistent: with the same secret key, one real supplier always gets the same
fake VAT number, name and IBAN, so the duplicate and anti-fraud rules still work on the copies.
Fake identifiers have valid check digits, and an original with a wrong check digit gets a
fake with a wrong one, so the checks give the same answer. The key stays in data/ (git-ignored):
without it, the fake values cannot be traced back to the real ones.

Before a copy is written it is verified: amounts and dates must be unchanged, a valid file must
stay valid for the official schema, and no original name, VAT number, tax code, IBAN or address
may appear in it. A file that fails is reported and no copy is written.

Dates, amounts and invoice numbers remain, so a copy can still be recognised by someone who
knows that invoice: use only invoices you have permission to use. Fake names are unrelated to
the real ones, so the clone-supplier rule cannot be measured on anonymized copies.
"""

import argparse
import hashlib
import hmac
import secrets
from pathlib import Path
from random import Random
from string import ascii_lowercase, ascii_uppercase, digits

from lxml import etree

from invoiceflow.checks import iban_is_valid, partita_iva_is_valid
from invoiceflow.checks.schema import schema_findings
from invoiceflow.generator.ids import random_italian_iban, random_partita_iva
from invoiceflow.importers.p7m import extract_xml
from invoiceflow.importers.xml_parser import InvoiceFormatError, parse_invoices
from invoiceflow.model import Invoice

_KEPT_FIELDS = """
    FormatoTrasmissione IdPaese RegimeFiscale Nazione TipoDocumento Divisa Data Numero
    ImportoTotaleDocumento Arrotondamento Art73 TipoRitenuta ImportoRitenuta AliquotaRitenuta
    CausalePagamento BolloVirtuale ImportoBollo TipoCassa AlCassa ImportoContributoCassa
    ImponibileCassa AliquotaIVA Ritenuta Natura Tipo Percentuale Importo NumeroLinea
    TipoCessionePrestazione Quantita UnitaMisura DataInizioPeriodo DataFinePeriodo
    PrezzoUnitario PrezzoTotale SpeseAccessorie ImponibileImporto Imposta EsigibilitaIVA
    CondizioniPagamento ModalitaPagamento DataRiferimentoTerminiPagamento
    GiorniTerminiPagamento DataScadenzaPagamento ImportoPagamento
"""
KEPT = frozenset(_KEPT_FIELDS.split())
# Whole blocks about third parties, orders, transport, attachments and signatures.
_REMOVED_FIELDS = """
    Signature Allegati ContattiTrasmittente Contatti IscrizioneREA StabileOrganizzazione
    RappresentanteFiscale TerzoIntermediarioOSoggettoEmittente DatiOrdineAcquisto DatiContratto
    DatiConvenzione DatiRicezione DatiFattureCollegate DatiSAL DatiDDT DatiTrasporto
    FatturaPrincipale DatiVeicoli CodiceArticolo AltriDatiGestionali
"""
REMOVED_BLOCKS = frozenset(_REMOVED_FIELDS.split())
FIXED = {
    "CodiceDestinatario": "0000000",
    "Indirizzo": "Via Anonima 1",
    "CAP": "00000",
    "Comune": "Comune Anonimo",
}
NAMES = ("Denominazione", "Nome", "Cognome")
# What must never appear in a copy. Short values could match by chance, so they are not scanned.
MUST_NOT_LEAK = (*NAMES, "IdCodice", "CodiceFiscale", "IBAN", "Indirizzo")
MIN_LEAK_LENGTH = 5


class Anonymizer:
    """Turns real values into fake ones, always the same for the same value and key."""

    def __init__(self, key: bytes) -> None:
        self._key = key

    def _digest(self, kind: str, value: str) -> bytes:
        return hmac.new(self._key, f"{kind}:{value}".encode(), hashlib.sha256).digest()

    def _rng(self, kind: str, value: str) -> Random:
        return Random(int.from_bytes(self._digest(kind, value), "big"))

    def vat_number(self, value: str) -> str:
        fake = random_partita_iva(self._rng("vat", value.strip().upper()))
        return fake if _is_valid_or_foreign_vat(value) else _wrong_last_digit(fake)

    def tax_code(self, value: str) -> str:
        """An 11-digit code is a company's VAT number; 16 characters are a person's code."""
        value = value.strip().upper()
        if len(value) == 11 and value.isdigit():
            return self.vat_number(value)
        rng = self._rng("tax_code", value)
        return "".join(
            rng.choice(digits if char == "0" else ascii_uppercase)
            for char in "AAAAAA00A00A000A"
        )

    def iban(self, value: str) -> str:
        clean = value.replace(" ", "").upper()
        fake = random_italian_iban(self._rng("iban", clean))
        return fake if iban_is_valid(clean) else _wrong_last_digit(fake)

    def word(self, kind: str, value: str) -> str:
        rng = self._rng(kind, value.strip().casefold())
        return "".join(rng.choice(ascii_lowercase) for _ in range(10)).capitalize()

    def token(self, value: bytes | str) -> str:
        text = value.hex() if isinstance(value, bytes) else value
        return self._digest("token", text).hex()[:10].upper()


def anonymize_xml(xml: bytes, anonymizer: Anonymizer) -> bytes:
    """The anonymized, verified copy of a FatturaPA XML file."""
    originals = parse_invoices(xml)  # also refuses DOCTYPEs and files that are not invoices
    parser = etree.XMLParser(
        no_network=True, resolve_entities=False, remove_comments=True, remove_pis=True
    )
    root = etree.fromstring(xml, parser)
    forbidden = {
        text.casefold()
        for element in root.iter("{*}*")
        if _name(element) in MUST_NOT_LEAK and (text := (element.text or "").strip())
        if len(text) >= MIN_LEAK_LENGTH
    }
    for attribute in list(root.attrib):
        if attribute != "versione":
            del root.attrib[attribute]
    for element in list(root.iter("{*}*")):
        if _name(element) in REMOVED_BLOCKS:
            _remove(element)
    _replace_names(root, anonymizer)  # first: they are keyed on the real VAT number
    for element in list(root.iter("{*}*")):
        if len(element) == 0 and _name(element) not in NAMES:
            _clean_value(element, anonymizer)
    _remove_empty_blocks(root)
    copy = etree.tostring(root, encoding="UTF-8", xml_declaration=True)
    _verify(xml, originals, copy, forbidden)
    return copy


def anonymized_file(path: Path, anonymizer: Anonymizer) -> tuple[str, bytes]:
    """(file name, content) of the copy of one invoice file. The name holds no real data."""
    data = path.read_bytes()
    xml = extract_xml(data) if path.name.lower().endswith(".p7m") else data
    copy = anonymize_xml(xml, anonymizer)
    supplier = etree.fromstring(copy).find(".//{*}CedentePrestatore//{*}IdFiscaleIVA")
    if supplier is None:
        raise InvoiceFormatError("the supplier has no VAT number")
    country = supplier.findtext("{*}IdPaese", default="")
    code = supplier.findtext("{*}IdCodice", default="")
    return f"{country}{code}_{anonymizer.token(data)}.xml", copy


def anonymize_folder(
    source: Path, target: Path, key_file: Path
) -> tuple[int, int, list[tuple[str, str]]]:
    """Returns (copies written, copies already there, [(file, problem)])."""
    source, target = source.resolve(), target.resolve()
    if target == source or target.is_relative_to(source) or source.is_relative_to(target):
        raise ValueError("the output folder must be separate from the originals")
    anonymizer = Anonymizer(load_key(key_file))
    target.mkdir(parents=True, exist_ok=True)
    written, existing = 0, 0
    errors: list[tuple[str, str]] = []
    for path in sorted(source.rglob("*")):
        if not (path.is_file() and path.name.lower().endswith((".xml", ".xml.p7m"))):
            continue
        try:
            name, copy = anonymized_file(path, anonymizer)
        except (ValueError, OSError, etree.LxmlError) as error:
            errors.append((path.name, str(error)))
            continue
        if (target / name).exists():
            existing += 1
        else:
            (target / name).write_bytes(copy)
            written += 1
    return written, existing, errors


def load_key(path: Path) -> bytes:
    """The secret key; created the first time. Keep it to stay consistent across runs."""
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch(mode=0o600)
        path.write_text(secrets.token_hex(32), encoding="ascii")
    return bytes.fromhex(path.read_text(encoding="ascii").strip())


def _name(element: etree._Element) -> str:
    return etree.QName(element).localname


def _remove(element: etree._Element) -> None:
    parent = element.getparent()
    if parent is not None:
        parent.remove(element)


def _replace_names(root: etree._Element, anonymizer: Anonymizer) -> None:
    """One company keeps one fake name even if it spells its real name in different ways."""
    for block in root.iter("{*}DatiAnagrafici"):
        owner = block.findtext(".//{*}IdCodice") or block.findtext("{*}CodiceFiscale") or ""
        for tag in NAMES:
            for element in block.iterfind(f".//{{*}}{tag}"):
                fake = anonymizer.word(tag, owner.strip() or element.text or "")
                element.text = f"Azienda {fake}" if tag == "Denominazione" else fake


def _clean_value(element: etree._Element, anonymizer: Anonymizer) -> None:
    name, text = _name(element), element.text or ""
    if name in KEPT:
        return
    if name in FIXED:
        element.text = FIXED[name]
    elif name == "IdCodice":
        element.text = anonymizer.vat_number(text)
    elif name == "CodiceFiscale":
        element.text = anonymizer.tax_code(text)
    elif name == "IBAN":
        element.text = anonymizer.iban(text)
    elif name == "ProgressivoInvio":
        element.text = anonymizer.token(text)
    elif name == "Descrizione":
        number = element.getparent()
        line = number.findtext("{*}NumeroLinea") if number is not None else None
        element.text = f"Riga {line or 1}"
    else:
        _remove(element)


def _remove_empty_blocks(root: etree._Element) -> None:
    for element in reversed(list(root.iter("{*}*"))):
        if element is not root and len(element) == 0 and not (element.text or "").strip():
            _remove(element)


def _verify(original: bytes, invoices: list[Invoice], copy: bytes, forbidden: set[str]) -> None:
    text = copy.decode("utf-8").casefold()
    if any(value in text for value in forbidden):
        raise InvoiceFormatError("an identifying value is still in the copy")
    if [_facts(i) for i in parse_invoices(copy)] != [_facts(i) for i in invoices]:
        raise InvoiceFormatError("the copy does not have the same amounts and dates")
    if not schema_findings(original) and schema_findings(copy):
        raise InvoiceFormatError("the copy is not valid for the official schema")


def _facts(invoice: Invoice) -> tuple[object, ...]:
    """What anonymizing must not change."""
    return (
        invoice.tipo_documento,
        invoice.divisa,
        invoice.data,
        invoice.numero,
        invoice.importo_totale_cents,
        [(x.numero_linea, x.quantita, x.prezzo_unitario, x.prezzo_totale, x.aliquota_iva,
          x.natura) for x in invoice.lines],
        [(x.aliquota_iva, x.imponibile_cents, x.imposta_cents, x.natura)
         for x in invoice.vat_summary],
        [(x.modalita, x.data_scadenza, x.importo_cents) for x in invoice.payments],
    )


def _is_valid_or_foreign_vat(value: str) -> bool:
    value = value.strip()
    return partita_iva_is_valid(value) or not (len(value) == 11 and value.isdigit())


def _wrong_last_digit(value: str) -> str:
    return f"{value[:-1]}{(int(value[-1]) + 1) % 10}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Make anonymized copies of real invoices.")
    parser.add_argument("source", type=Path, help="folder with the original .xml / .xml.p7m")
    parser.add_argument("target", type=Path, help="folder for the anonymized copies")
    parser.add_argument("--key", type=Path, default=Path("data") / "anonymize.key")
    args = parser.parse_args(argv)
    if not args.source.is_dir():
        parser.error(f"{args.source} is not a folder")
    try:
        written, existing, errors = anonymize_folder(args.source, args.target, args.key)
    except ValueError as error:
        parser.error(str(error))
    print(f"Wrote {written} anonymized copies, {existing} already there.")
    for name, problem in errors:
        print(f"  skipped {name}: {problem}")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
