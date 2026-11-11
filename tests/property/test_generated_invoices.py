"""Property tests: thousands of random generated invoices all respect the FatturaPA rules."""

from datetime import date, timedelta
from random import Random

from hypothesis import given, settings
from hypothesis import strategies as st

from invoiceflow.generator import ERROR_KINDS, corrupt, make_client, make_invoice, make_supplier
from invoiceflow.model import Invoice
from tests.generator_helpers import broken_rules


@st.composite
def invoices(draw: st.DrawFn) -> Invoice:
    rng = Random(draw(st.integers(min_value=0, max_value=2**32)))
    issue = date(2026, 1, 1) + timedelta(days=draw(st.integers(0, 364)))
    supplier = make_supplier(rng)
    return make_invoice(rng, supplier, make_client(rng), issue, str(rng.randint(1, 999)))


@settings(max_examples=2000, deadline=None)
@given(invoices())
def test_generated_invoices_are_correct(invoice: Invoice) -> None:
    assert broken_rules(invoice) == set()


@settings(max_examples=1000, deadline=None)
@given(invoices(), st.sampled_from(ERROR_KINDS), st.integers(min_value=0))
def test_every_corruption_is_detectable(invoice: Invoice, kind: str, seed: int) -> None:
    corrupted, _, _ = corrupt(invoice, Random(seed), kind)
    assert kind in broken_rules(corrupted)
