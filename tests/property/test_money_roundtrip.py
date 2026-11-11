from decimal import Decimal

from hypothesis import given
from hypothesis import strategies as st

from invoiceflow.money import from_cents, to_cents


@given(st.integers(min_value=-(10**13), max_value=10**13))
def test_cents_to_euro_and_back(cents: int) -> None:
    assert to_cents(from_cents(cents)) == cents


@given(st.decimals(min_value=-(10**11), max_value=10**11, places=2))
def test_euro_amounts_with_two_decimals_survive_the_roundtrip(amount: Decimal) -> None:
    assert from_cents(to_cents(amount)) == amount
