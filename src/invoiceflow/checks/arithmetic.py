"""Checks that the amounts of an invoice add up.

The rules, from the FatturaPA specification (fields of DatiBeniServizi and
DatiGeneraliDocumento):

1. line_total      PrezzoTotale = Quantita x PrezzoUnitario (Quantita missing = 1),
                   within LINE_TOLERANCE.
2. taxable_amount  for every DatiRiepilogo, ImponibileImporto = the sum of PrezzoTotale of the
                   lines with the same AliquotaIVA and Natura, rounded half up to the cent;
                   every (AliquotaIVA, Natura) used by a line has its DatiRiepilogo.
3. vat_amount      Imposta = ImponibileImporto x AliquotaIVA / 100, rounded half up to the cent,
                   within VAT_TOLERANCE_CENTS.
4. document_total  ImportoTotaleDocumento = sum of ImponibileImporto + sum of Imposta
                   (only when ImportoTotaleDocumento is present: it is optional).
5. payment_amount  the ImportoPagamento of all instalments add up to ImportoTotaleDocumento.

The SdI applies its own, sometimes looser, tolerances; ours are tighter on purpose, because a
correct invoice adds up to the cent. Known limits: withholding tax (DatiRitenuta), stamp duty
(DatiBollo), discounts (ScontoMaggiorazione) and Arrotondamento are not in the shared model yet,
so an invoice that uses them can be reported by rules 1, 4 or 5.
"""

from decimal import Decimal

from invoiceflow.checks.finding import Finding
from invoiceflow.model import Invoice
from invoiceflow.money import from_cents, to_cents

LINE_TOLERANCE = Decimal("0.01")  # euro
VAT_TOLERANCE_CENTS = 1


def check_line_totals(invoice: Invoice) -> list[Finding]:
    findings = []
    for line in invoice.lines:
        quantity = Decimal(1) if line.quantita is None else line.quantita
        expected = quantity * line.prezzo_unitario
        if abs(expected - line.prezzo_totale) > LINE_TOLERANCE:
            findings.append(
                Finding(
                    "line_total",
                    f"DettaglioLinee[{line.numero_linea}]/PrezzoTotale",
                    f"line {line.numero_linea}: {quantity} x {line.prezzo_unitario} = "
                    f"{expected}, but PrezzoTotale is {line.prezzo_totale}",
                )
            )
    return findings


def check_taxable_amounts(invoice: Invoice) -> list[Finding]:
    findings = []
    sums: dict[tuple[Decimal, str], Decimal] = {}
    for line in invoice.lines:
        key = (line.aliquota_iva, line.natura)
        sums[key] = sums.get(key, Decimal(0)) + line.prezzo_totale
    summarised = set()
    for summary in invoice.vat_summary:
        key = (summary.aliquota_iva, summary.natura)
        summarised.add(key)
        expected = to_cents(sums.get(key, Decimal(0)))
        if expected != summary.imponibile_cents:
            findings.append(
                Finding(
                    "taxable_amount",
                    f"DatiRiepilogo[{_rate(key)}]/ImponibileImporto",
                    f"the lines at {_rate(key)} add up to {from_cents(expected)}, "
                    f"but ImponibileImporto is {from_cents(summary.imponibile_cents)}",
                )
            )
    for key in sums.keys() - summarised:
        findings.append(
            Finding(
                "taxable_amount",
                "DatiRiepilogo",
                f"there are lines at {_rate(key)} but no DatiRiepilogo for that rate",
            )
        )
    return findings


def check_vat_amounts(invoice: Invoice) -> list[Finding]:
    findings = []
    for summary in invoice.vat_summary:
        expected = to_cents(from_cents(summary.imponibile_cents) * summary.aliquota_iva / 100)
        if abs(expected - summary.imposta_cents) > VAT_TOLERANCE_CENTS:
            key = (summary.aliquota_iva, summary.natura)
            findings.append(
                Finding(
                    "vat_amount",
                    f"DatiRiepilogo[{_rate(key)}]/Imposta",
                    f"{from_cents(summary.imponibile_cents)} x {summary.aliquota_iva}% = "
                    f"{from_cents(expected)}, but Imposta is {from_cents(summary.imposta_cents)}",
                )
            )
    return findings


def check_document_total(invoice: Invoice) -> list[Finding]:
    if invoice.importo_totale_cents is None:
        return []
    expected = sum(s.imponibile_cents + s.imposta_cents for s in invoice.vat_summary)
    if expected == invoice.importo_totale_cents:
        return []
    return [
        Finding(
            "document_total",
            "DatiGeneraliDocumento/ImportoTotaleDocumento",
            f"taxable amounts plus VAT add up to {from_cents(expected)}, but "
            f"ImportoTotaleDocumento is {from_cents(invoice.importo_totale_cents)}",
        )
    ]


def check_payment_amounts(invoice: Invoice) -> list[Finding]:
    amounts = [p.importo_cents for p in invoice.payments if p.importo_cents is not None]
    if not amounts or invoice.importo_totale_cents is None:
        return []
    paid = sum(amounts)
    if paid == invoice.importo_totale_cents:
        return []
    return [
        Finding(
            "payment_amount",
            "DatiPagamento/DettaglioPagamento/ImportoPagamento",
            f"the payments add up to {from_cents(paid)}, but ImportoTotaleDocumento is "
            f"{from_cents(invoice.importo_totale_cents)}",
        )
    ]


def _rate(key: tuple[Decimal, str]) -> str:
    rate, natura = key
    return f"{rate}%" if not natura else f"{rate}% {natura}"
