"""Anti-fraud rules: invoices that add up correctly but look like a scam.

Unlike the checks, which look at one invoice at a time, these rules compare an invoice with the
history of the archive: the supplier's usual IBAN and amounts, the names of known suppliers.
Each rule lives in its own module with the precise rule written at the top, and every flag
explains why the invoice is suspicious.

    python -m invoiceflow.antifraud --db data/invoiceflow.sqlite     (or: make frauds)
"""

import sqlite3
from collections.abc import Callable

from invoiceflow.antifraud.clone_supplier import check_clone_suppliers
from invoiceflow.antifraud.history import Flag, Record, load_records, without_extra_copies
from invoiceflow.antifraud.iban_change import check_iban_changes
from invoiceflow.antifraud.outlier_amount import check_outlier_amounts

RULES: tuple[Callable[[list[Record]], list[Flag]], ...] = (
    check_iban_changes,
    check_clone_suppliers,
    check_outlier_amounts,
)


def find_frauds(connection: sqlite3.Connection) -> list[Flag]:
    """Every flag raised by any rule on the archive, ordered by invoice. Re-uploaded copies
    of an invoice are left out: the duplicate finder reports those."""
    records = without_extra_copies(load_records(connection))
    flags = [flag for rule in RULES for flag in rule(records)]
    return sorted(flags, key=lambda flag: flag.invoice_id)


__all__ = ["RULES", "Flag", "find_frauds"]
