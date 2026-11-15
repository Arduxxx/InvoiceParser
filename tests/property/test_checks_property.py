"""Property tests: on thousands of random invoices, the checks say exactly what they should."""

from datetime import date, timedelta
from random import Random

from hypothesis import assume, given, settings
from hypothesis import strategies as st

from invoiceflow.checks import check_invoice, iban_is_valid, partita_iva_is_valid
from invoiceflow.generator import ERROR_KINDS, corrupt, make_client, make_invoice, make_supplier
from invoiceflow.generator.ids import random_italian_iban, random_partita_iva
from invoiceflow.model import Invoice


@st.composite
def invoices(draw: st.DrawFn) -> Invoice:
    rng = Random(draw(st.integers(min_value=0, max_value=2**32)))
    issue = date(2026, 1, 1) + timedelta(days=draw(st.integers(0, 364)))
    return make_invoice(rng, make_supplier(rng), make_client(rng), issue, str(rng.randint(1, 999)))


@settings(max_examples=1000, deadline=None)
@given(invoices())
def test_correct_invoices_pass_every_check(invoice: Invoice) -> None:
    assert check_invoice(invoice) == []


@settings(max_examples=1000, deadline=None)
@given(invoices(), st.sampled_from(ERROR_KINDS), st.integers(min_value=0))
def test_every_planted_error_is_caught_by_its_rule(invoice: Invoice, kind: str, seed: int) -> None:
    corrupted, _, _ = corrupt(invoice, Random(seed), kind)
    assert kind in {finding.code for finding in check_invoice(corrupted)}


@given(st.integers(min_value=0), st.integers(min_value=0, max_value=10), st.integers(0, 9))
def test_any_single_wrong_digit_in_a_partita_iva_is_caught(seed: int, pos: int, d: int) -> None:
    vat = random_partita_iva(Random(seed))
    assume(vat[pos] != str(d))
    assert partita_iva_is_valid(vat)
    assert not partita_iva_is_valid(vat[:pos] + str(d) + vat[pos + 1 :])


@given(st.integers(min_value=0), st.integers(min_value=5, max_value=26), st.integers(0, 9))
def test_any_single_wrong_digit_in_an_iban_is_caught(seed: int, pos: int, d: int) -> None:
    iban = random_italian_iban(Random(seed))
    assume(iban[pos].isdigit() and iban[pos] != str(d))
    assert iban_is_valid(iban)
    assert not iban_is_valid(iban[:pos] + str(d) + iban[pos + 1 :])
