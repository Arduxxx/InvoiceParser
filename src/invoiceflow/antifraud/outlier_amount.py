"""Rule outlier_amount: an amount far above what this supplier usually invoices.

Rule: the invoice is flagged when its supplier has at least MIN_HISTORY earlier invoices and its
total is more than FACTOR (2.5) times the largest of them.

How the numbers were chosen, on 10 generated datasets (about 9,600 regular invoices, 101
planted outliers): with 4 earlier invoices and 2.5 x the largest, 1 false alarm and 89 outliers
caught. A factor of 2 gave 8 false alarms (91 caught), 3 gave none (82 caught); 3 earlier
invoices gave 9 false alarms, because the first few amounts of a supplier can be all small.
Amounts vary much more than IBANs, so this rule needs one more invoice of history than
iban_change. Real invoices may need other values.

- Credit notes (TD04) are not expenses and are skipped.
- A flagged invoice does not raise the "largest" amount, so it cannot hide a later one.
- Fraction keeps the comparison exact: money never goes through float.
"""

from decimal import Decimal
from fractions import Fraction

from invoiceflow.antifraud.history import Flag, Record
from invoiceflow.money import from_cents

MIN_HISTORY = 4
FACTOR = Fraction(5, 2)
CREDIT_NOTE = "TD04"
_FACTOR_TEXT = str(Decimal(FACTOR.numerator) / FACTOR.denominator)  # "2.5"


def check_outlier_amounts(records: list[Record]) -> list[Flag]:
    """records: oldest first. One pass, keeping each supplier's largest amount so far."""
    largest: dict[int, int] = {}  # supplier id -> largest total of its earlier, unflagged invoices
    count: dict[int, int] = {}  # supplier id -> how many of those invoices there are
    flags = []
    for record in records:
        if record.tipo_documento == CREDIT_NOTE:
            continue
        history = count.get(record.supplier_id, 0)
        biggest = largest.get(record.supplier_id, 0)
        if history >= MIN_HISTORY and record.total_cents > FACTOR * biggest:
            flags.append(
                Flag(
                    record.id,
                    "outlier_amount",
                    f"{record.describe()} totals {record.total()}, more than {_FACTOR_TEXT}"
                    f" times the largest of its {history} earlier invoices"
                    f" (€ {from_cents(biggest):,.2f}).",
                )
            )
            continue
        largest[record.supplier_id] = max(biggest, record.total_cents)
        count[record.supplier_id] = history + 1
    return flags
