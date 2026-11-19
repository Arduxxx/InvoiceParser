"""Rule iban_change: a known supplier suddenly asks to be paid to a new bank account.

This is the classic "IBAN change" scam: a fraudster sends an invoice that looks exactly like a
real supplier's, but with their own IBAN.

Rule: the invoice is flagged when its supplier already has at least MIN_HISTORY earlier invoices
paid by IBAN, and the invoice uses a (valid) IBAN that none of them used.

- An IBAN with a wrong check digit is a typing mistake, already reported by the checks: it is
  ignored here.
- A flagged IBAN is not learned as "usual", so a second invoice to the same new IBAN is
  flagged too. A genuine change of bank therefore keeps being flagged: a way for the user to
  confirm it is not built yet.
"""

from invoiceflow.antifraud.history import Flag, Record
from invoiceflow.checks import iban_is_valid

# A supplier needs this many earlier invoices before its "usual" IBAN means anything.
MIN_HISTORY = 3


def check_iban_changes(records: list[Record]) -> list[Flag]:
    """records: oldest first. One pass, with the known IBANs of each supplier in a dictionary."""
    known: dict[int, set[str]] = {}  # supplier id -> IBANs of its earlier, unflagged invoices
    paid_before: dict[int, int] = {}  # supplier id -> how many of those invoices there are
    flags = []
    for record in records:
        ibans = [iban for iban in record.ibans if iban_is_valid(iban)]
        if not ibans:
            continue
        usual = known.setdefault(record.supplier_id, set())
        history = paid_before.get(record.supplier_id, 0)
        new = sorted(set(ibans) - usual)
        if history >= MIN_HISTORY and new:
            flags.append(
                Flag(
                    record.id,
                    "iban_change",
                    f"{record.describe()} asks to be paid to {', '.join(new)}, an IBAN this"
                    f" supplier never used before: its {history} earlier invoices were paid to"
                    f" {', '.join(sorted(usual))}.",
                )
            )
            continue
        usual.update(ibans)
        paid_before[record.supplier_id] = history + 1
    return flags
