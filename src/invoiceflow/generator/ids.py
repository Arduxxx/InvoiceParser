"""Random but well-formed Italian identifiers: partita IVA and IBAN.

Every identifier produced here passes its check-digit test, so the checks only fire on the
fields that the generator corrupts on purpose.
"""

from random import Random

_DIGITS = "0123456789"

# Third block of a partita IVA: provincial office code (001-100) or one of the special codes.
_VALID_OFFICE_CODES = (*range(1, 101), 120, 121, 888, 999)

# Values for characters in odd positions when computing the Italian IBAN CIN.
_CIN_ODD = (1, 0, 5, 7, 9, 13, 15, 17, 19, 21, 2, 4, 18, 20, 11, 3, 6, 8, 12, 14, 16, 10, 22, 25,
            24, 23)


def _is_digits(value: str) -> bool:
    return value != "" and all(c in _DIGITS for c in value)


def partita_iva_check_digit(first10: str) -> str:
    """Check digit (11th digit) of an Italian partita IVA, given its first 10 digits."""
    if len(first10) != 10 or not _is_digits(first10):
        raise ValueError(f"expected 10 digits, got {first10!r}")
    total = 0
    for i, ch in enumerate(first10):
        d = int(ch)
        if i % 2 == 1:  # 2nd, 4th, ... digit is doubled
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return str((10 - total % 10) % 10)


def random_partita_iva(rng: Random) -> str:
    """A random 11-digit partita IVA with a valid office code and check digit."""
    matricola = "".join(rng.choice(_DIGITS) for _ in range(7))
    first10 = matricola + f"{rng.choice(_VALID_OFFICE_CODES):03d}"
    return first10 + partita_iva_check_digit(first10)


def italian_cin(abi: str, cab: str, conto: str) -> str:
    """CIN (control letter) of an Italian BBAN made of ABI (5), CAB (5) and account (12)."""
    bban = (abi + cab + conto).upper()
    if len(abi) != 5 or len(cab) != 5 or len(conto) != 12 or not bban.isalnum():
        raise ValueError(f"malformed BBAN parts: {abi!r} {cab!r} {conto!r}")
    total = 0
    for i, ch in enumerate(bban):
        value = int(ch) if ch in _DIGITS else ord(ch) - ord("A")
        total += _CIN_ODD[value] if i % 2 == 0 else value
    return chr(ord("A") + total % 26)


def iban_check_digits(country: str, bban: str) -> str:
    """The two IBAN check digits (ISO 13616, mod 97) for a country code and BBAN."""
    rearranged = (bban + country + "00").upper()
    numeric = "".join(str(int(ch, 36)) for ch in rearranged)  # A=10, B=11, ..., Z=35
    return f"{98 - int(numeric) % 97:02d}"


def random_italian_iban(rng: Random) -> str:
    """A random 27-character Italian IBAN with valid CIN and mod-97 check digits."""
    abi = "".join(rng.choice(_DIGITS) for _ in range(5))
    cab = "".join(rng.choice(_DIGITS) for _ in range(5))
    conto = "".join(rng.choice(_DIGITS) for _ in range(12))
    bban = italian_cin(abi, cab, conto) + abi + cab + conto
    return "IT" + iban_check_digits("IT", bban) + bban
