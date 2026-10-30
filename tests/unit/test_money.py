from decimal import Decimal

from invoiceflow.money import from_cents, to_cents


def test_to_cents_rounds_half_up() -> None:
    assert to_cents(Decimal("1.005")) == 101


def test_roundtrip() -> None:
    assert to_cents(from_cents(12345)) == 12345
