from pathlib import Path
from random import Random

import pytest

from invoiceflow import storage
from invoiceflow.antifraud import find_frauds
from invoiceflow.antifraud.__main__ import main
from invoiceflow.antifraud.clone_supplier import check_clone_suppliers, skeleton
from invoiceflow.antifraud.evaluate import evaluate
from invoiceflow.antifraud.evaluate import main as evaluate_main
from invoiceflow.antifraud.history import Record, without_extra_copies
from invoiceflow.antifraud.iban_change import check_iban_changes
from invoiceflow.antifraud.outlier_amount import check_outlier_amounts
from invoiceflow.generator import DatasetConfig, generate_dataset, write_dataset
from invoiceflow.generator.ids import random_italian_iban, random_partita_iva

IBAN = "IT60X0542811101000000123456"
NEW_IBAN = random_italian_iban(Random(1))
VAT = "01234567897"
OTHER_VAT = random_partita_iva(Random(1))


def record(
    n: int,
    *,
    supplier: int = 1,
    total: int = 10_000,
    ibans: tuple[str, ...] = (IBAN,),
    name: str = "Rossi Informatica S.r.l.",
    vat: str = VAT,
    tipo: str = "TD01",
) -> Record:
    """Invoice n of the test archive, dated n days into 2026, so n also gives the order."""
    return Record(
        id=n, supplier_id=supplier, id_paese="IT", partita_iva=vat, supplier_name=name,
        tipo_documento=tipo, numero=str(n), data=f"2026-01-{n:02d}", total_cents=total,
        ibans=ibans,
    )


def flagged(flags: list) -> list[int]:  # type: ignore[type-arg]
    return [flag.invoice_id for flag in flags]


# ---- iban_change ----------------------------------------------------------------------------


def test_a_new_iban_after_three_invoices_is_flagged() -> None:
    records = [record(1), record(2), record(3), record(4, ibans=(NEW_IBAN,))]
    [flag] = check_iban_changes(records)
    assert (flag.invoice_id, flag.rule) == (4, "iban_change")
    assert NEW_IBAN in flag.message and IBAN in flag.message and "3 earlier" in flag.message


def test_two_earlier_invoices_are_not_enough_history() -> None:
    assert check_iban_changes([record(1), record(2), record(3, ibans=(NEW_IBAN,))]) == []


def test_every_invoice_to_the_new_iban_is_flagged() -> None:
    records = [record(1), record(2), record(3), record(4, ibans=(NEW_IBAN,)),
               record(5, ibans=(NEW_IBAN,)), record(6)]
    assert flagged(check_iban_changes(records)) == [4, 5]


def test_typos_and_missing_ibans_are_left_to_the_checks() -> None:
    typo = IBAN[:-1] + "7"  # wrong check digits: the IBAN check reports it
    records = [record(1), record(2), record(3), record(4, ibans=(typo,)), record(5, ibans=())]
    assert check_iban_changes(records) == []


def test_each_supplier_has_its_own_iban() -> None:
    records = [record(1), record(2), record(3),
               record(4, supplier=2, ibans=(NEW_IBAN,), vat=OTHER_VAT, name="Bianchi Srl")]
    assert check_iban_changes(records) == []


# ---- outlier_amount -------------------------------------------------------------------------


def history(n: int = 4) -> list[Record]:
    return [record(i, total=10_000) for i in range(1, n + 1)]  # 100.00 each


def test_more_than_two_and_a_half_times_the_largest_is_flagged() -> None:
    [flag] = check_outlier_amounts([*history(), record(5, total=25_001)])
    assert (flag.invoice_id, flag.rule) == (5, "outlier_amount")
    assert "€ 250.01" in flag.message and "2.5 times" in flag.message and "€ 100.00" in flag.message
    assert check_outlier_amounts([*history(), record(5, total=25_000)]) == []  # exactly 2.5x


def test_three_earlier_invoices_are_not_enough_history() -> None:
    assert check_outlier_amounts([*history(3), record(4, total=99_999)]) == []


def test_a_flagged_amount_does_not_become_usual() -> None:
    records = [*history(), record(5, total=100_000), record(6, total=90_000)]
    assert flagged(check_outlier_amounts(records)) == [5, 6]


def test_credit_notes_are_skipped() -> None:
    assert check_outlier_amounts([*history(), record(5, total=99_999, tipo="TD04")]) == []


# ---- clone_supplier -------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("look_alike", "original"),
    [
        ("Rossl Informatica Srl", "Rossi Informatica S.r.l."),  # l for i, Srl for S.r.l.
        ("R0ssi Informatica S.r.l.", "Rossi Informatica S.r.l."),  # zero for o
        ("Forrnazione Mariani S.n.c.", "Formazione Mariani S.n.c."),  # rn for m
        ("Rosssi Informatica S.r.l.", "Rossi Informatica S.r.l."),  # doubled letter
        ("Ufficio Bruno e C. S.a.s.", "Ufficio Bruno & C. S.a.s."),
    ],
)
def test_look_alike_names_have_the_same_skeleton(look_alike: str, original: str) -> None:
    assert skeleton(look_alike) == skeleton(original)


def test_skeleton_separates_the_legal_form() -> None:
    assert skeleton("Rossi Informatica S.p.A.") == ("roslnformatlca", "spa")
    assert skeleton("Studio Rossi") == ("studlorosl", "")  # no legal form


def clone_records(clone_name: str, clone_vat: str = OTHER_VAT) -> list[Record]:
    return [record(1), record(2),
            record(3, supplier=2, name=clone_name, vat=clone_vat, ibans=(NEW_IBAN,)),
            record(4, supplier=2, name=clone_name, vat=clone_vat, ibans=(NEW_IBAN,))]


def test_every_invoice_of_a_clone_is_flagged() -> None:
    flags = check_clone_suppliers(clone_records("Rossl Informatica Srl"))
    assert flagged(flags) == [3, 4]
    message = flags[0].message
    assert "Rossi Informatica S.r.l. (IT01234567897)" in message
    assert f"IT{OTHER_VAT}" in message and "a different IBAN" in message


@pytest.mark.parametrize(
    "name",
    [
        "Rossi Infromatica S.r.l.",  # two letters swapped
        "Rossi Informatca S.r.l.",  # one letter missing
        "Rossa Informatica S.r.l.",  # one letter changed
    ],
)
def test_names_one_character_apart_are_clones(name: str) -> None:
    assert flagged(check_clone_suppliers(clone_records(name))) == [3, 4]


@pytest.mark.parametrize(
    "name",
    [
        "Rossi Informatica S.p.A.",  # another legal form: another company
        "Bianchi Informatica S.r.l.",
        "Rossi Software S.r.l.",
    ],
)
def test_different_names_are_not_clones(name: str) -> None:
    assert check_clone_suppliers(clone_records(name)) == []


def test_a_mistyped_vat_number_is_left_to_the_checks() -> None:
    assert check_clone_suppliers(clone_records("Rossi Informatica S.r.l.", "01234567890")) == []


def test_very_short_names_are_ignored() -> None:
    records = [record(1, name="Abc"), record(2, supplier=2, name="Abd", vat=OTHER_VAT)]
    assert check_clone_suppliers(records) == []


# ---- archive, command line, measurement ---------------------------------------------------


def test_re_uploads_are_not_history() -> None:
    copy = record(1)
    assert without_extra_copies([record(1), copy, record(2)]) == [record(1), record(2)]


def test_on_a_generated_dataset(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    write_dataset(generate_dataset(DatasetConfig(n_invoices=600, seed=11)), tmp_path)
    result = evaluate(tmp_path)
    for kind in ("iban_change", "clone_supplier"):
        assert result.planted[kind] > 0
        assert result.caught[kind] == result.planted[kind]
    assert result.caught["outlier_amount"] * 4 >= result.planted["outlier_amount"] * 3  # >= 75%
    assert len(result.false_alarms) * 100 <= result.regular  # at most 1 per 100 invoices
    assert evaluate_main([str(tmp_path)]) == 0
    assert "false alarms:" in capsys.readouterr().out


def test_command_line(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--db", str(tmp_path / "empty.sqlite")]) == 0
    assert "0 suspicious invoices found." in capsys.readouterr().out
    connection = storage.connect(tmp_path / "empty.sqlite")
    assert find_frauds(connection) == []
    connection.close()
