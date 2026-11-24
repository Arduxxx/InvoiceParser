"""Makes an anonymized copy of a PDF invoice by rebuilding it from scratch.

Painting over text is not enough: the original usually stays in the file. So nothing of the
original file is reused. Its words are read with their positions and a new PDF is drawn with:
- facts, kept: amounts, dates, percentages, document codes, the invoice number;
- labels, kept: a fixed list of Italian invoice words ("Fattura", "Imponibile", "Totale"...);
- identities, replaced by the same fakes as the XML copies: VAT numbers, tax codes, IBANs and
  the names of parties known from the XML files;
- every other word, removed. An unknown surname or street is simply not on the allow-list.

Metadata, images, links, attachments, form fields and hidden layers are never copied. Before
the copy is returned it is read back: no identity may remain, and amounts and dates must match.
Line descriptions, addresses, and names not known from an XML file do not appear in the copy.
"""

import io
import re
from collections import Counter
from dataclasses import dataclass, replace
from typing import Any

import pdfplumber
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen import canvas

from invoiceflow.importers.xml_parser import InvoiceFormatError

_LABEL_WORDS = """
a al alla aliquota aliquote allegato anagrafica art articolo arrotondamento bancario
bollo bonifico cap cedente cessionario cf cliente codice codfisc committente condizioni
contributo cod data dati del della dello denominazione descrizione destinatario di
divisa documento e emissione esigibilita esigibilità eur euro fattura fiscale fornitore
importo imponibile imposta iban in iva il la modalita modalità n natura netto nr num
numero nota credito pagamento pagare pagina partita piva per prestatore prezzo
quantita quantità regime ritenuta riepilogo scadenza sconto spese spett spettle su
tipo tipologia totale totali unita unità unitario valore
ai alle anagrafici bene cognome dettaglio elettronica fini fornitura generali identificativo
importi linea linee nome operazione relativi sede servizio valuta
"""
_LABELS = frozenset(_LABEL_WORDS.split())
_NUMBER_LABELS = frozenset({"fattura", "documento", "n", "nr", "num", "credito", "linea"})
_AMOUNT = re.compile(r"[€]?-?(?:\d{1,3}(?:\.\d{3})+|\d+),\d{2,8}[€]?|-?\d+\.\d{2,8}")
_DATE = re.compile(r"\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4}|\d{4}-\d{2}-\d{2}")
_PERCENT = re.compile(r"\d{1,3}(?:[.,]\d{1,8})?%")
_CODE = re.compile(r"TD\d{2}|MP\d{2}|RF\d{2}|N\d(?:\.\d{1,2})?|FPR12|FPA12", re.IGNORECASE)
_INVOICE_NUMBER = re.compile(r"(?=[A-Za-z0-9/_.-]*\d)[A-Za-z0-9/_.-]{1,20}")
_IBAN = re.compile(
    r"(?<![A-Z0-9])(?:IT\s?\d{2}\s?[A-Z](?:\s?\d){10}(?:\s?[A-Z0-9]){12}"
    r"|(?!IT)[A-Z]{2}\d{2}[A-Z0-9]{11,30})(?![A-Z0-9])"
)
_TAX_CODE = re.compile(
    r"(?<![A-Z0-9])[A-Z]{6}\d{2}[A-Z]\d{2}[A-Z]\d{3}[A-Z](?![A-Z0-9])", re.IGNORECASE
)
_VAT = re.compile(r"(?<![A-Za-z0-9])(IT)?(\d{11})(?!\d)", re.IGNORECASE)
_LINE_TOLERANCE = 3  # points: words this close vertically are on the same line


class Fakes:
    """What the redactor needs from the XML anonymizer (see anonymize.Anonymizer)."""

    def vat_number(self, value: str) -> str:
        raise NotImplementedError

    def tax_code(self, value: str) -> str:
        raise NotImplementedError

    def iban(self, value: str) -> str:
        raise NotImplementedError


@dataclass(frozen=True)
class _Word:
    text: str
    x0: float
    x1: float
    top: float
    bottom: float
    size: float
    fixed: bool = False  # already replaced by a fake: keep as it is


def redact_pdf(pdf: bytes, fakes: Fakes, names: dict[str, str]) -> tuple[bytes, str]:
    """(anonymized PDF, fake VAT number of the first party, or ""). names: real -> fake."""
    pages = _read_pages(pdf)
    if not any(words for _, _, words in pages):
        raise InvoiceFormatError("the PDF has no text (a scan?)")
    forbidden: set[str] = set()
    first_vat: list[str] = []
    patterns = [(re.compile(r"(?<!\w)" + r"\s+".join(map(re.escape, real.split())) + r"(?!\w)",
                            re.IGNORECASE), fake)
                for real, fake in sorted(names.items(), key=lambda item: -len(item[0]))]
    forbidden.update(_compact(real) for real in names if len(_compact(real)) >= 5)

    def vat(match: re.Match[str]) -> str:
        fake = fakes.vat_number(match.group(2))
        first_vat.append(fake)
        return f"{match.group(1) or ''}{fake}"

    def literal(fake: str) -> Any:
        return lambda _match: fake

    rebuilt = []
    for width, height, words in pages:
        kept: list[_Word] = []
        previous: list[_Word] = []
        for line in _lines(words):
            line = _replace(line, _IBAN, lambda m: fakes.iban(m.group()), forbidden)
            line = _replace(line, _TAX_CODE, lambda m: fakes.tax_code(m.group()), forbidden)
            line = _replace(line, _VAT, vat, forbidden)
            for pattern, fake in patterns:
                line = _replace(line, pattern, literal(fake), forbidden)
            safe = _allowed_words(line, previous)
            kept.extend(safe)
            previous = line
        rebuilt.append((width, height, kept))
    copy = _draw(rebuilt)
    _verify(pages, copy, forbidden)
    return copy, first_vat[0] if first_vat else ""


def _read_pages(pdf: bytes) -> list[tuple[float, float, list[_Word]]]:
    try:
        with pdfplumber.open(io.BytesIO(pdf)) as document:
            return [
                (float(page.width), float(page.height), _words(page))
                for page in document.pages
            ]
    except Exception as error:  # the message of a PDF library could quote file content
        raise InvoiceFormatError(f"not a readable PDF ({type(error).__name__})") from error


def _words(page: Any) -> list[_Word]:
    return [
        _Word(w["text"], w["x0"], w["x1"], w["top"], w["bottom"], float(w["size"]))
        for w in page.extract_words(extra_attrs=["size"])
        if w.get("upright", True)  # rotated text is usually a watermark or a margin note
    ]


def _lines(words: list[_Word]) -> list[list[_Word]]:
    lines: list[list[_Word]] = []
    for word in sorted(words, key=lambda w: (w.top, w.x0)):
        if lines and abs(lines[-1][0].top - word.top) <= _LINE_TOLERANCE:
            lines[-1].append(word)
        else:
            lines.append([word])
    return [sorted(line, key=lambda w: w.x0) for line in lines]


def _replace(
    line: list[_Word],
    pattern: re.Pattern[str],
    fake_for: Any,
    forbidden: set[str],
) -> list[_Word]:
    """Replaces every match in the line, even one spread over several words."""
    while True:
        text, starts = _joined(line)
        match = next(
            (m for m in pattern.finditer(text) if not _touches_fixed(m, line, starts)), None
        )
        if match is None:
            return line
        covered = [
            i for i, word in enumerate(line)
            if starts[i] < match.end() and starts[i] + len(word.text) > match.start()
        ]
        first, last = line[covered[0]], line[covered[-1]]
        before = first.text[: match.start() - starts[covered[0]]]
        after = last.text[match.end() - starts[covered[-1]] :]
        forbidden.add(_compact(match.group()))
        fake = f"{_safe(before)}{fake_for(match)}{_safe(after)}"
        merged = replace(first, text=fake, x1=last.x1, fixed=True)
        line = [*line[: covered[0]], merged, *line[covered[-1] + 1 :]]


def _joined(line: list[_Word]) -> tuple[str, list[int]]:
    starts, position = [], 0
    for word in line:
        starts.append(position)
        position += len(word.text) + 1
    return " ".join(word.text for word in line), starts


def _touches_fixed(match: re.Match[str], line: list[_Word], starts: list[int]) -> bool:
    return any(
        word.fixed and start < match.end() and start + len(word.text) > match.start()
        for word, start in zip(line, starts, strict=True)
    )


def _safe(piece: str) -> str:
    return piece if not piece or _is_label(piece) or _is_fact(piece) else ""


def _allowed_words(line: list[_Word], previous: list[_Word]) -> list[_Word]:
    kept = []
    for index, word in enumerate(line):
        allowed = word.fixed or _is_label(word.text) or _is_fact(word.text)
        if allowed or _is_invoice_number(word, line[:index], previous):
            kept.append(word)
    return kept


def _is_invoice_number(word: _Word, before: list[_Word], above: list[_Word]) -> bool:
    if not _INVOICE_NUMBER.fullmatch(_strip(word.text)):
        return False
    if before and _normal(before[-1].text) in _NUMBER_LABELS:
        return True
    # a table: "Numero documento" in the header, the value right below it
    return any(
        _normal(header.text) == "numero"
        and _normal(following.text) == "documento"
        and not following.text.endswith(":")
        and header.x0 < word.x1
        and word.x0 < header.x1
        for header, following in zip(above, above[1:], strict=False)
    )


def _strip(token: str) -> str:
    return token.strip("()[]:;,")


def _normal(token: str) -> str:
    return _strip(token).replace(".", "").replace("°", "").casefold()


def _is_label(token: str) -> bool:
    parts = [_normal(part) for part in re.split(r"[/\-]", token)]
    return all(not part or part in _LABELS for part in parts) and not re.search(r"\d", token)


def _is_fact(token: str) -> bool:
    text = _strip(token)
    return (
        not re.search(r"\w", text)
        or any(p.fullmatch(text) for p in (_AMOUNT, _DATE, _PERCENT, _CODE))
    )


def _draw(pages: list[tuple[float, float, list[_Word]]]) -> bytes:
    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, invariant=1, pageCompression=0)
    pdf.setTitle("Anonymized invoice")
    pdf.setAuthor("")
    pdf.setSubject("")
    pdf.setCreator("")
    pdf.setProducer("")
    for width, height, words in pages:
        pdf.setPageSize((width, height))
        for index, word in enumerate(words):
            text = word.text.encode("cp1252", "ignore").decode("cp1252")
            natural = stringWidth(text, "Helvetica", word.size) or 1
            label = pdf.beginText(word.x0, height - word.bottom + word.size * 0.2)
            label.setFont("Helvetica", word.size)
            label.setHorizScale(min(100, 100 * max(word.x1 - word.x0, 1) / natural))
            following = words[index + 1] if index + 1 < len(words) else None
            close = following is not None and abs(following.top - word.top) <= _LINE_TOLERANCE
            # a real space, or two words a narrow gap apart would be read back as one
            label.textOut(f"{text} " if close else text)
            pdf.drawText(label)
        pdf.showPage()
    pdf.save()
    return buffer.getvalue()


def _verify(
    original: list[tuple[float, float, list[_Word]]], copy: bytes, forbidden: set[str]
) -> None:
    pages = _read_pages(copy)
    text = _compact(" ".join(word.text for _, _, words in pages for word in words))
    if any(value in text for value in forbidden):
        raise InvoiceFormatError("an identifying value is still in the PDF copy")
    if _facts(original) != _facts(pages):
        raise InvoiceFormatError("the PDF copy does not have the same amounts and dates")


def _facts(pages: list[tuple[float, float, list[_Word]]]) -> Counter[str]:
    tokens = (_strip(word.text) for _, _, words in pages for word in words)
    return Counter(t for t in tokens if _AMOUNT.fullmatch(t) or _DATE.fullmatch(t))


def _compact(text: str) -> str:
    return re.sub(r"\s", "", text).casefold()
