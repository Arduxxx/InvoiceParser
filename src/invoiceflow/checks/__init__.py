"""Correctness checks: does an invoice add up, are its identifiers valid, are its dates sane?

check_invoice() runs every rule and returns what is wrong; an empty list means the invoice
passes all of them. Each rule lives in its own module, with the precise rule written at the top.
"""

from collections.abc import Callable

from invoiceflow.checks.arithmetic import (
    check_document_total,
    check_line_totals,
    check_payment_amounts,
    check_taxable_amounts,
    check_vat_amounts,
)
from invoiceflow.checks.dates import check_due_dates
from invoiceflow.checks.finding import Finding
from invoiceflow.checks.identifiers import (
    check_ibans,
    check_vat_numbers,
    iban_is_valid,
    partita_iva_is_valid,
)
from invoiceflow.model import Invoice

RULES: tuple[Callable[[Invoice], list[Finding]], ...] = (
    check_line_totals,
    check_taxable_amounts,
    check_vat_amounts,
    check_document_total,
    check_payment_amounts,
    check_vat_numbers,
    check_ibans,
    check_due_dates,
)


def check_invoice(invoice: Invoice) -> list[Finding]:
    """Every rule broken by the invoice (empty list: the invoice passes all checks)."""
    return [finding for rule in RULES for finding in rule(invoice)]


__all__ = [
    "RULES",
    "Finding",
    "check_invoice",
    "iban_is_valid",
    "partita_iva_is_valid",
]
