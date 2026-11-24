"""Reads Italian PDF invoices into the shared Invoice model.

Two kinds of PDF are understood:
- the official SdI view of an e-invoice ("foglio di stile SdI"), where every field has a fixed
  label, so item lines, payments and both parties are read exactly;
- other Italian invoices, where each field is found through common labels ("Fattura n.",
  "Partita IVA", "Imponibile", "Scadenza", "IBAN"...).
If a required field cannot be found the file is refused with a message: a wrong guess would be
worse than a skipped invoice.

Known limits:
- scanned PDFs have no text and are refused (no OCR);
- outside the SdI view item lines are not read, because tables vary too much. Each VAT rate
  becomes one summary line, so the check "quantity x price = line total" says nothing there;
- without a "Fornitore:" label, the first VAT number on the page is taken as the supplier's.
"""

import io
import re
from datetime import date
from decimal import Decimal

import pdfplumber

from invoiceflow.importers.xml_parser import InvoiceFormatError
from invoiceflow.model import Invoice, Line, Party, Payment, VatSummary
from invoiceflow.money import from_cents, to_cents

_FLAGS = re.IGNORECASE | re.MULTILINE
_AMOUNT = r"(\d{1,3}(?:\.\d{3})+,\d{2}|\d+,\d{2}|\d+\.\d{2})"  # 1.234,56 or 1234,56 or 1234.56
_RATE = r"(\d{1,2}(?:[.,]\d{1,2})?)\s*%"
_EURO = r"\s*:?\s*(?:EUR|€)?\s*:?\s*"
_DATE = r"(\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4})"

# contains a digit; may be written with spaces around a slash ("2026 / 30")
_NUMBER = r"((?=[A-Z0-9/_.-]*\d)[A-Z0-9][A-Z0-9/_.-]*(?:\s*/\s*[A-Z0-9]+)*)"

_NUMBER_AFTER_N = re.compile(
    r"\b(?:n|nr|num|numero)\b\s*[.°:]*\s*" + _NUMBER, _FLAGS
)
_NUMBER_AFTER_FATTURA = re.compile(
    r"(?<!data )\bfattura\b\s*:?\s*" + _NUMBER, _FLAGS
)
_ISSUE_DATE = re.compile(
    rf"\b(?:data(?:\s+(?:fattura|documento|emissione))?|del)\s*:?\s*{_DATE}", _FLAGS
)
_DUE_DATE = re.compile(rf"\bscadenza(?:\s+pagamento)?\s*:?\s*{_DATE}", _FLAGS)
_VAT_NUMBER = re.compile(r"(?:partita\s+iva|p\.?\s*iva)\s*[:.]?\s*(?:IT)?\s*(\d{11})\b", _FLAGS)
_SUPPLIER_LABEL = re.compile(r"\b(?:fornitore|cedente|prestatore|emittente)\b\s*:?\s*", _FLAGS)
_CUSTOMER_LABEL = re.compile(
    r"\b(?:spett\.?(?:le)?|cliente|destinatario|cessionario|committente)\b[.:]?\s*", _FLAGS
)
_TOTALS = tuple(
    re.compile(rf"\b{label}{_EURO}{_AMOUNT}", _FLAGS)
    for label in (r"totale\s+documento", r"totale\s+fattura", r"importo\s+totale", "totale")
)
_NET = re.compile(rf"\b(?:netto|totale)?\s*(?:a|da)\s+pagare{_EURO}{_AMOUNT}", _FLAGS)
_VAT_TABLE_ROW = re.compile(
    rf"^\s*{_RATE}(?:\s+(N\d(?:\.\d)?))?\s+{_AMOUNT}\s+{_AMOUNT}\s*$", _FLAGS
)
_VAT_ON_AMOUNT = re.compile(rf"\bIVA\s+(?:al\s+)?{_RATE}\s+su\s+{_AMOUNT}{_EURO}{_AMOUNT}", _FLAGS)
_VAT_SIMPLE = re.compile(rf"\bIVA\s+(?:al\s+)?{_RATE}{_EURO}{_AMOUNT}", _FLAGS)
_TAXABLE = re.compile(rf"\bimponibile{_EURO}{_AMOUNT}", _FLAGS)
# "Importo IVA € 0,00": the whole invoice charges no VAT (exempt, flat-rate scheme...)
_NO_VAT = re.compile(rf"\b(?:importo|totale)\s+iva{_EURO}0+[.,]00\b", _FLAGS)
_NATURA = re.compile(r"\bN[1-7](?:\.\d)?\b")
_WITHHOLDING = re.compile(
    r"\britenuta(?:\s+d['’]acconto)?(?:\s*\(?(?:al\s+)?\d{1,2}(?:[.,]\d{1,2})?\s*%\)?)?"
    rf"{_EURO}-?\s*(?:EUR|€)?\s*{_AMOUNT}",
    _FLAGS,
)
_SDI_MARK = re.compile(r"Dati\s+di\s+riepilogo\s+per\s+aliquota", _FLAGS)
_SDI_NUMBER = r"(-?\d+(?:\.\d+)?)"
_IBAN = re.compile(r"\bIT\s?\d{2}\s?[A-Z](?:\s?\d){10}(?:\s?[A-Z0-9]){12}\b")


def parse_pdf_invoice(pdf: bytes, *, source_path: str = "", source_sha256: str = "") -> Invoice:
    """The invoice in a PDF file (one invoice per file)."""
    return invoice_from_text(_text_of(pdf), source_path, source_sha256)


def invoice_from_text(text: str, source_path: str = "", source_sha256: str = "") -> Invoice:
    if _SDI_MARK.search(text):
        return _sdi_invoice(text, source_path, source_sha256)
    supplier_vat, customer_vat = _vat_numbers(text)
    summaries = _vat_summaries(text)
    total = _total(text)
    net = _NET.search(text)
    iban = _IBAN.search(text)
    due = _DUE_DATE.search(text)
    payment = Payment(
        modalita="MP05" if iban else "",
        data_scadenza=_date(due.group(1)) if due else None,
        importo_cents=_cents(net.group(1)) if net else total,
        iban=re.sub(r"\s", "", iban.group()) if iban else "",
    )
    return Invoice(
        tipo_documento="TD04" if re.search(r"nota\s+di\s+credito", text, _FLAGS) else "TD01",
        divisa="EUR",
        data=_issue_date(text),
        numero=_number(text),
        importo_totale_cents=total,
        ritenuta_cents=_withholding(text),
        cedente=Party(
            denominazione=_supplier_name(text), id_paese="IT", partita_iva=supplier_vat
        ),
        cessionario=Party(
            denominazione=_customer_name(text),
            id_paese="IT" if customer_vat else "",
            partita_iva=customer_vat,
        ),
        lines=[_summary_line(number, s) for number, s in enumerate(summaries, start=1)],
        vat_summary=summaries,
        payments=[payment],
        source_path=source_path,
        source_sha256=source_sha256,
    )


def _sdi_invoice(text: str, source_path: str, source_sha256: str) -> Invoice:
    """The official SdI view: one labelled field per line, in fixed sections."""
    supplier, customer, general = (
        _section_start(text, heading)
        for heading in (r"Dati\s+del\s+cedente", r"Dati\s+del\s+cessionario",
                        r"Dati\s+generali\s+del\s+documento")
    )
    lines_at, summary_at = (
        _section_start(text, heading)
        for heading in (r"Dati\s+relativi\s+alle\s+linee", r"Dati\s+di\s+riepilogo")
    )
    head = text[general:lines_at]
    total = _field(head, rf"Importo\s+totale\s+documento:\s*{_SDI_NUMBER}")
    after_summary = text[summary_at:]
    payments_at = re.search(r"Dati\s+relativi\s+al\s+pagamento", after_summary, _FLAGS)
    summary_text = after_summary[: payments_at.start()] if payments_at else after_summary
    payment_text = after_summary[payments_at.start() :] if payments_at else ""
    cedente = _sdi_party(text[supplier:customer])
    if not cedente.partita_iva:
        raise InvoiceFormatError("supplier VAT number not found in the PDF")
    return Invoice(
        tipo_documento=_required_field(head, r"Tipologia\s+documento:\s*(TD\w{2})", "type"),
        divisa=_field(head, r"Valuta\s+importi:\s*([A-Z]{3})") or "EUR",
        data=date.fromisoformat(
            _required_field(head, r"Data\s+documento:\s*(\d{4}-\d{2}-\d{2})", "date")
        ),
        numero=_required_field(head, r"Numero\s+documento:[ \t]*(.+)", "number").strip(),
        importo_totale_cents=None if total is None else to_cents(Decimal(total)),
        ritenuta_cents=sum(
            abs(to_cents(Decimal(amount)))
            for amount in re.findall(rf"Importo\s+ritenuta:\s*{_SDI_NUMBER}", head, _FLAGS)
        ),
        cedente=cedente,
        cessionario=_sdi_party(text[customer:general]),
        lines=_sdi_lines(text[lines_at:summary_at]),
        vat_summary=_sdi_summaries(summary_text),
        payments=_sdi_payments(payment_text),
        source_path=source_path,
        source_sha256=source_sha256,
    )


def _section_start(text: str, heading: str) -> int:
    found = re.search(heading, text, _FLAGS)
    if found is None:
        raise InvoiceFormatError("a section of the SdI view was not found in the PDF")
    return found.start()


def _field(text: str, pattern: str) -> str | None:
    found = re.search(pattern, text, _FLAGS)
    return found.group(1).strip() if found else None


def _required_field(text: str, pattern: str, name: str) -> str:
    value = _field(text, pattern)
    if value is None:
        raise InvoiceFormatError(f"invoice {name} not found in the PDF")
    return value


def _sdi_party(block: str) -> Party:
    vat = re.search(r"fini\s+IVA:\s*([A-Z]{2})\s*([A-Z0-9]+)", block, re.MULTILINE)
    return Party(
        denominazione=_field(block, r"^Denominazione:[ \t]*(.+)") or "",
        nome=_field(block, r"^Nome:[ \t]*(.+)") or "",
        cognome=_field(block, r"^Cognome:[ \t]*(.+)") or "",
        id_paese=vat.group(1) if vat else "",
        partita_iva=vat.group(2) if vat else "",
        codice_fiscale=_field(block, r"^Codice\s+fiscale:[ \t]*(\S+)") or "",
    )


def _sdi_lines(block: str) -> list[Line]:
    pieces = re.split(r"Nr\.\s*linea:\s*(\d+)", block, flags=_FLAGS)
    lines = []
    for number, body in zip(pieces[1::2], pieces[2::2], strict=True):
        quantity = _field(body, rf"Quantit[àa]:\s*{_SDI_NUMBER}")
        lines.append(
            Line(
                numero_linea=int(number),
                descrizione=_field(body, r"Descrizione\s+bene/servizio:[ \t]*(.+)") or "",
                quantita=None if quantity is None else Decimal(quantity),
                prezzo_unitario=Decimal(
                    _required_field(body, rf"Valore\s+unitario:\s*{_SDI_NUMBER}", "unit price")
                ),
                prezzo_totale=Decimal(
                    _required_field(body, rf"Valore\s+totale:\s*{_SDI_NUMBER}", "line total")
                ),
                aliquota_iva=Decimal(
                    _required_field(body, rf"IVA\s*\(%\)\s*:?\s*{_SDI_NUMBER}", "line VAT rate")
                ),
                natura=_field(body, r"\b(N[1-7](?:\.\d+)?)\b") or "",
            )
        )
    if not lines:
        raise InvoiceFormatError("invoice lines not found in the PDF")
    return lines


def _sdi_summaries(block: str) -> list[VatSummary]:
    pattern = (
        rf"Aliquota\s+IVA\s*\(%\)\s*:?\s*{_SDI_NUMBER}(.*?)"
        rf"Totale\s+imponibile/importo:\s*{_SDI_NUMBER}.*?Totale\s+imposta:\s*{_SDI_NUMBER}"
    )
    summaries = [
        VatSummary(
            aliquota_iva=Decimal(rate),
            imponibile_cents=to_cents(Decimal(taxable)),
            imposta_cents=to_cents(Decimal(vat)),
            natura=_field(between, r"\b(N[1-7](?:\.\d+)?)\b") or "",
        )
        for rate, between, taxable, vat in re.findall(pattern, block, _FLAGS | re.DOTALL)
    ]
    if not summaries:
        raise InvoiceFormatError("VAT summary not found in the PDF")
    return summaries


def _sdi_payments(block: str) -> list[Payment]:
    payments = []
    for body in re.split(r"Dettaglio\s+pagamento", block, flags=_FLAGS)[1:]:
        due = _field(body, r"Data\s+scadenza(?:\s+pagamento)?\s*:?\s*(\d{4}-\d{2}-\d{2})")
        amount = _field(body, rf"\bImporto:\s*{_SDI_NUMBER}")
        iban = _IBAN.search(body)
        payments.append(
            Payment(
                modalita=_field(body, r"\b(MP\d{2})\b") or "",
                data_scadenza=date.fromisoformat(due) if due else None,
                importo_cents=None if amount is None else to_cents(Decimal(amount)),
                iban=re.sub(r"\s", "", iban.group()) if iban else "",
            )
        )
    return payments


def _text_of(pdf: bytes) -> str:
    try:
        with pdfplumber.open(io.BytesIO(pdf)) as document:
            text = "\n".join(page.extract_text() or "" for page in document.pages)
    except Exception as error:  # pdfplumber raises several types for malformed files
        raise InvoiceFormatError(f"not a readable PDF: {error}") from error
    if not text.strip():
        raise InvoiceFormatError("the PDF has no text (a scan?); OCR is not supported")
    return text


def _number(text: str) -> str:
    for line in text.splitlines():
        if re.search(r"\bfattura\b|nota\s+di\s+credito", line, re.IGNORECASE):
            found = _NUMBER_AFTER_N.search(line) or _NUMBER_AFTER_FATTURA.search(line)
            if found and not re.fullmatch(_DATE, found.group(1)):
                return found.group(1).rstrip(".")
    raise InvoiceFormatError("invoice number not found in the PDF")


def _issue_date(text: str) -> date:
    found = _ISSUE_DATE.search(text)
    if found is None:
        raise InvoiceFormatError("invoice date not found in the PDF")
    return _date(found.group(1))


def _vat_numbers(text: str) -> tuple[str, str]:
    """(supplier, customer). With a supplier label, its VAT number is the first one after it."""
    found = list(_VAT_NUMBER.finditer(text))
    if not found:
        raise InvoiceFormatError("supplier VAT number not found in the PDF")
    label = _SUPPLIER_LABEL.search(text)
    after_label = [m.group(1) for m in found if label and m.start() > label.start()]
    supplier = after_label[0] if after_label else found[0].group(1)
    others = [m.group(1) for m in found if m.group(1) != supplier]
    return supplier, others[0] if others else ""


def _supplier_name(text: str) -> str:
    for line in text.splitlines():
        label = _SUPPLIER_LABEL.search(line)
        if label and line[label.end() :].strip():
            return _before_customer(line[label.end() :])
    for line in text.splitlines():
        name = _before_customer(line)
        if re.search(r"[A-Za-z]{2}", name) and not re.search(r"fattura|nota\s+di", name, _FLAGS):
            return name
    raise InvoiceFormatError("supplier name not found in the PDF")


def _before_customer(line: str) -> str:
    label = _CUSTOMER_LABEL.search(line)
    return (line[: label.start()] if label else line).strip()


def _customer_name(text: str) -> str:
    lines = text.splitlines()
    for number, line in enumerate(lines):
        label = _CUSTOMER_LABEL.search(line)
        if label:
            name = line[label.end() :].strip()
            if name:
                return name
            return lines[number + 1].strip() if number + 1 < len(lines) else ""
    return ""


def _total(text: str) -> int:
    for pattern in _TOTALS:
        found = pattern.search(text)
        if found:
            return _cents(found.group(1))
    net = _NET.search(text)
    if net:
        return _cents(net.group(1))
    raise InvoiceFormatError("invoice total not found in the PDF")


def _withholding(text: str) -> int:
    """The withholding tax, if the invoice shows one. Only the first mention counts, because
    the same amount is often repeated in a summary."""
    found = _WITHHOLDING.search(text)
    return _cents(found.group(1)) if found else 0


def _vat_summaries(text: str) -> list[VatSummary]:
    """Accepts a table ("22% 100,00 22,00"), "IVA 22% su 100,00: 22,00", "Imponibile 100,00"
    with "IVA 22% 22,00" when the invoice has a single rate, or "Imponibile 100,00" with
    "Importo IVA 0,00" when the invoice charges no VAT at all."""
    rows = [(m[1], m[2] or "", m[3], m[4]) for m in _VAT_TABLE_ROW.finditer(text)]
    rows = rows or [(m[1], "", m[2], m[3]) for m in _VAT_ON_AMOUNT.finditer(text)]
    if not rows:
        taxable = _TAXABLE.search(text)
        vat = _VAT_SIMPLE.findall(text)
        if taxable and len(vat) == 1:
            rows = [(vat[0][0], "", taxable.group(1), vat[0][1])]
        elif taxable and not vat and _NO_VAT.search(text):
            # no VAT at all: the summary table, if any, has no percentage to anchor on, so the
            # whole taxable amount becomes one summary at 0%
            rows = [("0", "", taxable.group(1), "0,00")]
    if not rows:
        raise InvoiceFormatError("VAT summary not found in the PDF")
    natura = _NATURA.search(text)
    return [
        VatSummary(
            aliquota_iva=(value := _rate(rate)),
            imponibile_cents=_cents(taxable_text),
            imposta_cents=_cents(vat_text),
            natura=code or (natura.group() if natura and value == 0 else ""),
        )
        for rate, code, taxable_text, vat_text in rows
    ]


def _summary_line(number: int, summary: VatSummary) -> Line:
    amount = from_cents(summary.imponibile_cents)
    return Line(
        numero_linea=number,
        descrizione=f"Riepilogo IVA {summary.aliquota_iva}% (righe non lette dal PDF)",
        prezzo_unitario=amount,
        prezzo_totale=amount,
        aliquota_iva=summary.aliquota_iva,
        natura=summary.natura,
    )


def _cents(text: str) -> int:
    if "," in text:
        text = text.replace(".", "").replace(",", ".")
    return to_cents(Decimal(text))


def _rate(text: str) -> Decimal:
    return Decimal(text.replace(",", ".")).quantize(Decimal("0.01"))


def _date(text: str) -> date:
    day, month, year = (int(part) for part in re.split(r"[/.-]", text))
    try:
        return date(year + 2000 if year < 100 else year, month, day)
    except ValueError as error:
        raise InvoiceFormatError(f"{text!r} is not a date") from error
