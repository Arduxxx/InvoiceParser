"""What a check reports when an invoice breaks one of its rules."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Finding:
    """One broken rule, with the reason in plain words.

    code: the rule's name; for the rules the generator can break it is the same name as the
          generator's error kind (e.g. "line_total"), so results can be compared with labels.csv.
    field: where the problem is, as a FatturaPA path (e.g. "DettaglioLinee[2]/PrezzoTotale").
    message: why the invoice breaks the rule, with the numbers involved.
    """

    code: str
    field: str
    message: str
