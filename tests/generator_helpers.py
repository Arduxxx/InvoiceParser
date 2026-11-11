"""Independent re-implementations of the invoice rules, used to test the generator.

Written separately from the generator on purpose, so a bug in one is not hidden by the other.
"""

from decimal import ROUND_HALF_UP, Decimal

from invoiceflow.model import Invoice


def iban_is_valid(iban: str) -> bool:
    rearranged = iban[4:] + iban[:4]
    digits = "".join(str(int(c, 36)) for c in rearranged)
    return int(digits) % 97 == 1


def partita_iva_is_valid(vat: str) -> bool:
    if len(vat) != 11 or not vat.isdigit():
        return False
    total = 0
    for i, ch in enumerate(vat[:10]):
        d = int(ch) * (2 if i % 2 else 1)
        total += d // 10 + d % 10
    return (10 - total % 10) % 10 == int(vat[10])


def _round_cents(value: Decimal) -> int:
    return int((value * 100).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def broken_rules(invoice: Invoice) -> set[str]:
    """Names of the rules this invoice breaks (empty set = correct invoice)."""
    broken: set[str] = set()
    for line in invoice.lines:
        assert line.quantita is not None
        if _round_cents(line.quantita * line.prezzo_unitario) != line.prezzo_totale_cents:
            broken.add("line_total")
    for s in invoice.vat_summary:
        lines_sum = sum(ln.prezzo_totale_cents for ln in invoice.lines
                        if (ln.aliquota_iva, ln.natura) == (s.aliquota_iva, s.natura))
        if lines_sum != s.imponibile_cents:
            broken.add("taxable_amount")
        if _round_cents(Decimal(s.imponibile_cents) / 100 * s.aliquota_iva / 100) \
                != s.imposta_cents:
            broken.add("vat_amount")
    total = sum(s.imponibile_cents + s.imposta_cents for s in invoice.vat_summary)
    if total != invoice.importo_totale_cents:
        broken.add("document_total")
    for p in invoice.payments:
        if p.importo_cents != invoice.importo_totale_cents:
            broken.add("payment_amount")
        if not iban_is_valid(p.iban):
            broken.add("iban")
        if p.data_scadenza is None or p.data_scadenza < invoice.data:
            broken.add("due_date_before_issue")
    if not partita_iva_is_valid(invoice.cedente.partita_iva):
        broken.add("supplier_vat_number")
    return broken
