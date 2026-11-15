"""Checks that the dates of an invoice are consistent.

Rule due_date_before_issue: every DataScadenzaPagamento is on or after the invoice Data
(a payment cannot fall due before the invoice exists). A missing due date is allowed.
"""

from invoiceflow.checks.finding import Finding
from invoiceflow.model import Invoice


def check_due_dates(invoice: Invoice) -> list[Finding]:
    findings = []
    for number, payment in enumerate(invoice.payments, start=1):
        if payment.data_scadenza is not None and payment.data_scadenza < invoice.data:
            findings.append(
                Finding(
                    "due_date_before_issue",
                    f"DettaglioPagamento[{number}]/DataScadenzaPagamento",
                    f"payment due on {payment.data_scadenza.isoformat()}, before the invoice "
                    f"date {invoice.data.isoformat()}",
                )
            )
    return findings
