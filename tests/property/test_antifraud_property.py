"""Property tests for the clone-supplier rule, on thousands of random supplier names."""

from random import Random

from hypothesis import given, settings
from hypothesis import strategies as st

from invoiceflow.antifraud.clone_supplier import one_deletion_keys, skeleton
from invoiceflow.generator import make_supplier
from invoiceflow.generator.scenarios import near_identical_name


def near_identical(a: str, b: str) -> bool:
    (core_a, form_a), (core_b, form_b) = skeleton(a), skeleton(b)
    return form_a == form_b and bool(one_deletion_keys(core_a) & one_deletion_keys(core_b))


@settings(max_examples=2000, deadline=None)
@given(st.integers(min_value=0, max_value=2**32))
def test_every_look_alike_name_of_the_generator_is_recognised(seed: int) -> None:
    rng = Random(seed)
    name = make_supplier(rng).name
    assert near_identical(near_identical_name(name, rng), name)


@given(st.text(min_size=1, max_size=30), st.text(min_size=1, max_size=30))
def test_near_identical_is_symmetric(a: str, b: str) -> None:
    assert near_identical(a, b) == near_identical(b, a)
