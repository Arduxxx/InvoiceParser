"""Rule clone_supplier: a new supplier whose name is nearly identical to a known one's.

A fraudster registers "Rossl Informatica Srl" (with a lower-case L) to pass for "Rossi
Informatica S.r.l." and gets paid in its place. Suppliers are identified by VAT number, so the
clone is, for the archive, a different supplier with its own VAT number (and usually IBAN).

Rule: a supplier is a clone when its name is near-identical to the name of a supplier first
seen earlier. Every invoice of the clone is flagged. Two names are near-identical when, after
reducing them to a "skeleton" (see skeleton()), they have the same legal form and their cores
are equal or differ by one character (one added, removed or changed, or two swapped).

Speed: comparing every supplier with every other would be n x n work. Instead each core is put
in a dictionary under itself and under every way of deleting one of its characters
("rossi" -> "ossi", "rssi", ...). Two cores that differ by one character always share one of
these keys, so near-identical names land in the same dictionary entry.

A supplier whose Italian VAT number has a wrong check digit is skipped: that is a typing
mistake on a real supplier's invoice, already reported by the checks.
"""

import re

from invoiceflow.antifraud.history import Flag, Record
from invoiceflow.checks import partita_iva_is_valid

LEGAL_FORMS = ("srl", "spa", "snc", "sas")
MIN_CORE_LENGTH = 5  # very short names are too similar by chance
_LOOK_ALIKES = str.maketrans({"0": "o", "1": "l", "i": "l"})  # after lower case, I and l match


def skeleton(name: str) -> tuple[str, str]:
    """(core, legal form) of a company name, written so that look-alike names become equal.
    "Rossl  Informatica Srl" and "Rossi Informatica S.r.l." both give ("roslnformatlca", "srl")."""
    text = name.lower().replace("&", "e").translate(_LOOK_ALIKES).replace("rn", "m")
    text = re.sub(r"[^a-z0-9]", "", text)  # no spaces, dots or other punctuation
    text = re.sub(r"(.)\1+", r"\1", text)  # a doubled letter counts once
    for form in LEGAL_FORMS:
        if text.endswith(form) and len(text) > len(form):
            return text[: -len(form)], form
    return text, ""


def one_deletion_keys(core: str) -> set[str]:
    """The core itself and every string made by deleting one of its characters."""
    return {core} | {core[:i] + core[i + 1 :] for i in range(len(core))}


def check_clone_suppliers(records: list[Record]) -> list[Flag]:
    first_seen: dict[int, Record] = {}  # supplier id -> its first invoice (records: oldest first)
    ibans: dict[int, set[str]] = {}  # supplier id -> every IBAN it used
    for record in records:
        first_seen.setdefault(record.supplier_id, record)
        ibans.setdefault(record.supplier_id, set()).update(record.ibans)
    order = {supplier_id: n for n, supplier_id in enumerate(first_seen)}

    index: dict[tuple[str, str], set[int]] = {}  # (legal form, key) -> supplier ids
    for supplier_id, first in first_seen.items():
        if first.id_paese == "IT" and not partita_iva_is_valid(first.partita_iva):
            continue
        core, form = skeleton(first.supplier_name)
        if len(core) < MIN_CORE_LENGTH:
            continue
        for key in one_deletion_keys(core):
            index.setdefault((form, key), set()).add(supplier_id)

    clone_of: dict[int, int] = {}  # clone supplier id -> the known supplier it imitates
    for group in index.values():
        if len(group) > 1:
            genuine = min(group, key=order.__getitem__)  # the one seen first
            for supplier_id in sorted(group - {genuine}):
                clone_of.setdefault(supplier_id, genuine)

    flags = []
    for record in records:
        if record.supplier_id not in clone_of:
            continue
        known_id = clone_of[record.supplier_id]
        known = first_seen[known_id]
        other_iban = "" if ibans[known_id] & set(record.ibans) else " and a different IBAN"
        flags.append(
            Flag(
                record.id,
                "clone_supplier",
                f"{record.describe()}: the name is nearly identical to known supplier"
                f" {known.supplier_name} ({known.id_paese}{known.partita_iva}), but the VAT"
                f" number is {record.id_paese}{record.partita_iva}{other_iban}.",
            )
        )
    return flags
