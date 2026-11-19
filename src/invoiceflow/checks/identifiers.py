"""Checks the check digits of the identifiers in an invoice.

Rules:
1. supplier_vat_number / customer_vat_number: an Italian partita IVA (IdPaese "IT") has 11 digits
   and its last digit is the check digit of the first ten (Luhn-like algorithm: digits in even
   positions are doubled, minus 9 if above 9; the check digit takes the sum to a multiple of 10).
   Foreign VAT numbers follow other rules and are not checked here.
2. iban: two letters, two check digits, then 11-30 letters or digits (27 characters in total for
   Italy); moving the first four characters to the end and turning letters into numbers
   (A=10 ... Z=35) must give a number whose remainder divided by 97 is 1 (ISO 13616).

These rules catch typing mistakes: any single wrong digit is always caught. They cannot tell
whether a valid number belongs to the right company: that is a job for the anti-fraud rules.
"""

import re

from invoiceflow.checks.finding import Finding
from invoiceflow.model import Invoice, Party

_IBAN = re.compile(r"[A-Z]{2}[0-9]{2}[A-Z0-9]{11,30}")
_IBAN_LENGTH = {"IT": 27}  # countries with a known length; add more when needed


def partita_iva_is_valid(code: str) -> bool:
    if not re.fullmatch(r"[0-9]{11}", code):
        return False
    total = 0
    for position, char in enumerate(code[:10], start=1):
        digit = int(char)
        if position % 2 == 0:
            digit *= 2
            if digit > 9:
                digit -= 9
        total += digit
    return (10 - total % 10) % 10 == int(code[10])


def iban_is_valid(iban: str) -> bool:
    iban = iban.replace(" ", "").upper()
    if not _IBAN.fullmatch(iban):
        return False
    if len(iban) != _IBAN_LENGTH.get(iban[:2], len(iban)):
        return False
    rearranged = iban[4:] + iban[:4]
    return int("".join(str(int(char, 36)) for char in rearranged)) % 97 == 1


def check_vat_numbers(invoice: Invoice) -> list[Finding]:
    findings = []
    for party, code, element in (
        (invoice.cedente, "supplier_vat_number", "CedentePrestatore"),
        (invoice.cessionario, "customer_vat_number", "CessionarioCommittente"),
    ):
        if _italian_vat(party) and not partita_iva_is_valid(party.partita_iva):
            findings.append(
                Finding(
                    code,
                    f"{element}/DatiAnagrafici/IdFiscaleIVA/IdCodice",
                    f"partita IVA {party.partita_iva!r} has a wrong check digit or format",
                )
            )
    return findings


def check_ibans(invoice: Invoice) -> list[Finding]:
    findings = []
    for number, payment in enumerate(invoice.payments, start=1):
        if payment.iban and not iban_is_valid(payment.iban):
            findings.append(
                Finding(
                    "iban",
                    f"DettaglioPagamento[{number}]/IBAN",
                    f"IBAN {payment.iban!r} fails the mod-97 check or has the wrong length",
                )
            )
    return findings


def _italian_vat(party: Party) -> bool:
    return party.id_paese == "IT" and party.partita_iva != ""
