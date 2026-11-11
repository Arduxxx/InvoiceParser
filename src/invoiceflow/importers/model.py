"""The data of an imported invoice, as read from a FatturaPA file.

Amounts that end up in totals are integer cents (see money.py). Quantities, unit prices
and VAT rates stay Decimal: the format allows up to 8 decimals for them, and turning a
unit price like 0.12345 euro into cents would change it.
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal


@dataclass(frozen=True)
class Address:
    street: str  # Indirizzo (with NumeroCivico appended when present)
    cap: str
    city: str  # Comune
    province: str | None  # Provincia, only for Italian addresses
    country: str  # Nazione, e.g. "IT"


@dataclass(frozen=True)
class Party:
    """Supplier (CedentePrestatore) or customer (CessionarioCommittente)."""

    name: str  # Denominazione, or "Nome Cognome" for a person
    vat_country: str | None  # IdFiscaleIVA/IdPaese
    vat_number: str | None  # IdFiscaleIVA/IdCodice (the partita IVA for Italy)
    fiscal_code: str | None  # CodiceFiscale
    address: Address


@dataclass(frozen=True)
class Line:
    number: int  # NumeroLinea
    description: str
    quantity: Decimal | None  # optional in the format
    unit_price: Decimal
    total_cents: int  # PrezzoTotale
    vat_rate: Decimal  # AliquotaIVA, e.g. Decimal("22.00")
    nature: str | None  # Natura, e.g. "N2.2" when no VAT is charged


@dataclass(frozen=True)
class VatSummary:
    """One DatiRiepilogo block: there is one per VAT rate (and nature)."""

    vat_rate: Decimal
    nature: str | None
    taxable_cents: int  # ImponibileImporto
    tax_cents: int  # Imposta


@dataclass(frozen=True)
class Payment:
    """One DettaglioPagamento: an invoice can be paid in several instalments."""

    method: str  # ModalitaPagamento, e.g. "MP05" = bank transfer
    amount_cents: int
    due_date: date | None
    iban: str | None


@dataclass(frozen=True)
class Invoice:
    document_type: str  # TipoDocumento, e.g. "TD01" = invoice
    currency: str
    issue_date: date
    number: str
    total_cents: int | None  # ImportoTotaleDocumento is optional in the format
    supplier: Party
    customer: Party
    lines: tuple[Line, ...]
    vat_summaries: tuple[VatSummary, ...]
    payments: tuple[Payment, ...]
