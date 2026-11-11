"""Ways to make a correct invoice wrong (errors), suspicious (frauds) or a re-upload (duplicates).

Errors change exactly ONE field, so the invoice breaks exactly one check. Amount changes are at
least 2 cents, beyond the 1-cent rounding tolerance the FatturaPA rules allow.
Frauds keep the invoice arithmetically correct: only the anti-fraud rules can catch them.
"""

import re
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from random import Random

from invoiceflow.generator.core import SupplierProfile, assemble_invoice, company_party, make_line
from invoiceflow.generator.ids import random_italian_iban
from invoiceflow.model import Invoice, Line
from invoiceflow.money import from_cents

ERROR_KINDS: tuple[str, ...] = (
    "line_total",             # one PrezzoTotale != Quantita x PrezzoUnitario
    "vat_amount",             # one Imposta != ImponibileImporto x AliquotaIVA
    "taxable_amount",         # one ImponibileImporto != sum of its lines
    "document_total",         # ImportoTotaleDocumento != imponibile + imposta
    "payment_amount",         # ImportoPagamento != ImportoTotaleDocumento
    "supplier_vat_number",    # one digit of the cedente's partita IVA changed
    "iban",                   # one digit of the IBAN changed
    "due_date_before_issue",  # DataScadenzaPagamento before Data
)

FRAUD_KINDS: tuple[str, ...] = (
    "iban_change",     # known supplier, new (valid) IBAN
    "clone_supplier",  # near-identical name to a known supplier, different VAT number and IBAN
    "unknown_vat",     # well-formed VAT number that is not registered (VIES would reject it)
    "outlier_amount",  # known supplier, amount far above its usual range
)


def _amount_delta(rng: Random, current: int) -> int:
    """A nonzero change of at least 2 cents that keeps the amount non-negative."""
    magnitude = rng.randint(2, max(2, min(50_000, current // 2 or 2)))
    if current - magnitude < 0 or rng.random() < 0.5:
        return magnitude
    return -magnitude


def _change_one_digit(value: str, rng: Random, start: int = 0) -> str:
    positions = [i for i in range(start, len(value)) if value[i].isdigit()]
    i = rng.choice(positions)
    new_digit = rng.choice([d for d in "0123456789" if d != value[i]])
    return value[:i] + new_digit + value[i + 1:]


def corrupt(invoice: Invoice, rng: Random, kind: str | None = None) -> tuple[Invoice, str, str]:
    """Alter one field of a correct invoice. Returns (invoice, kind, human-readable detail)."""
    kind = kind if kind is not None else rng.choice(ERROR_KINDS)
    if kind == "line_total":
        i = rng.randrange(len(invoice.lines))
        line = invoice.lines[i]
        new = line.prezzo_totale_cents + _amount_delta(rng, line.prezzo_totale_cents)
        lines = list(invoice.lines)
        lines[i] = replace(line, prezzo_totale_cents=new)
        detail = (f"line {line.numero_linea} PrezzoTotale {from_cents(line.prezzo_totale_cents)}"
                  f" -> {from_cents(new)}")
        return replace(invoice, lines=lines), kind, detail
    if kind in ("vat_amount", "taxable_amount"):
        i = rng.randrange(len(invoice.vat_summary))
        summary = invoice.vat_summary[i]
        summaries = list(invoice.vat_summary)
        if kind == "vat_amount":
            new = summary.imposta_cents + _amount_delta(rng, summary.imposta_cents)
            summaries[i] = replace(summary, imposta_cents=new)
            old = summary.imposta_cents
        else:
            new = summary.imponibile_cents + _amount_delta(rng, summary.imponibile_cents)
            summaries[i] = replace(summary, imponibile_cents=new)
            old = summary.imponibile_cents
        detail = f"riepilogo {summary.aliquota_iva}%: {from_cents(old)} -> {from_cents(new)}"
        return replace(invoice, vat_summary=summaries), kind, detail
    if kind == "document_total":
        old = invoice.importo_totale_cents or 0
        new = old + _amount_delta(rng, old)
        detail = f"ImportoTotaleDocumento {from_cents(old)} -> {from_cents(new)}"
        return replace(invoice, importo_totale_cents=new), kind, detail
    if kind == "payment_amount":
        payment = invoice.payments[0]
        old = payment.importo_cents or 0
        new = old + _amount_delta(rng, old)
        payments = [replace(payment, importo_cents=new), *invoice.payments[1:]]
        detail = f"ImportoPagamento {from_cents(old)} -> {from_cents(new)}"
        return replace(invoice, payments=payments), kind, detail
    if kind == "supplier_vat_number":
        old_vat = invoice.cedente.partita_iva
        new_vat = _change_one_digit(old_vat, rng)
        cedente = replace(invoice.cedente, partita_iva=new_vat, codice_fiscale=new_vat)
        return replace(invoice, cedente=cedente), kind, f"partita IVA {old_vat} -> {new_vat}"
    if kind == "iban":
        payment = invoice.payments[0]
        new_iban = _change_one_digit(payment.iban, rng, start=5)  # BBAN digits after the CIN
        payments = [replace(payment, iban=new_iban), *invoice.payments[1:]]
        return replace(invoice, payments=payments), kind, f"IBAN {payment.iban} -> {new_iban}"
    if kind == "due_date_before_issue":
        payment = invoice.payments[0]
        due = invoice.data - timedelta(days=rng.randint(1, 60))
        payments = [replace(payment, data_scadenza=due), *invoice.payments[1:]]
        detail = f"scadenza {due.isoformat()} before data {invoice.data.isoformat()}"
        return replace(invoice, payments=payments), kind, detail
    raise ValueError(f"unknown error kind {kind!r}")


def with_new_iban(invoice: Invoice, rng: Random) -> tuple[Invoice, str]:
    """Same invoice, paid to a different (valid) IBAN."""
    payment = invoice.payments[0]
    new_iban = random_italian_iban(rng)
    while new_iban == payment.iban:
        new_iban = random_italian_iban(rng)
    payments = [replace(payment, iban=new_iban), *invoice.payments[1:]]
    return replace(invoice, payments=payments), f"IBAN {payment.iban} -> {new_iban}"


def inflate_amount(invoice: Invoice, supplier: SupplierProfile, rng: Random) -> tuple[Invoice, str]:
    """Same supplier and products, quantities multiplied by 10-30x. Still arithmetically correct."""
    factor = rng.randint(10, 30)
    products = {p.description: p for p in supplier.products}
    lines: list[Line] = []
    for line in invoice.lines:
        quantity = (line.quantita or Decimal(1)) * factor
        lines.append(make_line(line.numero_linea, products[line.descrizione], quantity))
    payment = invoice.payments[0]
    assert payment.data_scadenza is not None
    inflated = assemble_invoice(
        cedente=invoice.cedente, cessionario=invoice.cessionario, data=invoice.data,
        numero=invoice.numero, lines=lines, iban=payment.iban, due_date=payment.data_scadenza,
    )
    return inflated, f"quantities x{factor}"


_LOOKALIKES = (("l", "I"), ("I", "l"), ("o", "0"), ("O", "0"), ("i", "1"), ("m", "rn"),
               ("S.r.l.", "Srl"), ("S.p.A.", "SpA"), ("S.n.c.", "Snc"), ("S.a.s.", "Sas"),
               (" & C.", " e C."))


def near_identical_name(name: str, rng: Random) -> str:
    """A name a human would confuse with `name`: look-alike letters, a doubled or swapped letter."""
    variants: set[str] = {name.replace(a, b, 1) for a, b in _LOOKALIKES if a in name}
    letters = [i for i, c in enumerate(name) if c.isalpha()]
    i = rng.choice(letters)
    variants.add(name[:i] + name[i] + name[i:])  # doubled letter
    for j in letters:
        if j + 1 < len(name) and name[j + 1].isalpha() and name[j] != name[j + 1]:
            variants.add(name[:j] + name[j + 1] + name[j] + name[j + 2:])  # swapped letters
            break
    variants.discard(name)
    return rng.choice(sorted(variants))


def make_clone(supplier: SupplierProfile, rng: Random) -> SupplierProfile:
    """A fake supplier impersonating `supplier`: look-alike name, own VAT number and IBAN."""
    party = company_party(rng, near_identical_name(supplier.name, rng))
    return replace(supplier, party=party, iban=random_italian_iban(rng))


def reformat_number(numero: str, rng: Random) -> str:
    """The same invoice number written differently (padding, separators, prefix, case)."""
    candidates = {
        numero.replace("/", "-"),
        numero.replace("-", "/"),
        numero.replace("/", " / "),
        re.sub(r"\d+", lambda m: m.group().lstrip("0") or "0", numero),
        re.sub(r"\d+", lambda m: m.group().zfill(6) if len(m.group()) < 4 else m.group(), numero),
        f"N. {numero}",
        numero.lower(),
    }
    candidates.discard(numero)
    return rng.choice(sorted(candidates))
