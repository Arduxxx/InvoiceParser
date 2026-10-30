"""Money helpers: amounts are integer cents or Decimal, never float."""

from decimal import ROUND_HALF_UP, Decimal

CENT = Decimal("0.01")


def to_cents(amount: Decimal) -> int:
    """Convert a Decimal euro amount to integer cents, rounding half up."""
    return int((amount / CENT).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def from_cents(cents: int) -> Decimal:
    """Convert integer cents to a Decimal euro amount."""
    return Decimal(cents) * CENT
