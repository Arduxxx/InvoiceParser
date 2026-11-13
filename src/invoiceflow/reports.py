"""The numbers shown by the dashboard, computed from the SQLite archive.

Kept apart from dashboard.py so that they can be tested without Streamlit.
All amounts are integer cents.
"""

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date

from invoiceflow.money import from_cents

UNCATEGORISED = "(no category)"
CREDIT_NOTE = "TD04"  # nota di credito: money coming back, so it lowers the spend


@dataclass(frozen=True)
class Spend:
    """One archived invoice, seen as an expense."""

    invoice_id: int
    data: str  # ISO date, YYYY-MM-DD
    supplier: str
    category: str
    amount_cents: int


@dataclass(frozen=True)
class DuePayment:
    invoice_id: int
    due: str  # ISO date
    supplier: str
    numero: str
    amount_cents: int
    iban: str


def load_spend(connection: sqlite3.Connection, skip_ids: set[int]) -> list[Spend]:
    """Every invoice except those in skip_ids (e.g. the extra copies of a duplicate).
    The amount is ImportoTotaleDocumento, or, when the invoice omits it (it is optional),
    the sum of taxable amount and VAT of its summaries."""
    rows = connection.execute(
        "SELECT i.id, i.data, c.nome, sc.category, i.tipo_documento,"
        " COALESCE(i.importo_totale_cents,"
        "   (SELECT SUM(v.imponibile_cents + v.imposta_cents)"
        "    FROM vat_summary v WHERE v.invoice_id = i.id), 0)"
        " FROM invoices i JOIN cedenti c ON c.id = i.cedente_id"
        " LEFT JOIN supplier_categories sc ON sc.cedente_id = c.id"
        " ORDER BY i.data"
    ).fetchall()
    spends = []
    for invoice_id, data, supplier, category, tipo, amount in rows:
        if invoice_id in skip_ids:
            continue
        sign = -1 if tipo == CREDIT_NOTE else 1
        spends.append(Spend(invoice_id, data, supplier, category or UNCATEGORISED, sign * amount))
    return spends


def total_by(spends: list[Spend], key: Callable[[Spend], str]) -> dict[str, int]:
    """Sum the amounts grouped by key, e.g. total_by(spends, lambda s: s.supplier)."""
    totals: dict[str, int] = {}
    for spend in spends:
        totals[key(spend)] = totals.get(key(spend), 0) + spend.amount_cents
    return totals


def by_month(spend: Spend) -> str:
    return spend.data[:7]  # "2026-03-15" -> "2026-03"


def coming_due(
    connection: sqlite3.Connection, start: date, end: date, skip_ids: set[int]
) -> list[DuePayment]:
    """Payments whose due date (DataScadenzaPagamento) falls between start and end."""
    rows = connection.execute(
        "SELECT i.id, p.data_scadenza, c.nome, i.numero, COALESCE(p.importo_cents, 0), p.iban"
        " FROM payments p JOIN invoices i ON i.id = p.invoice_id"
        " JOIN cedenti c ON c.id = i.cedente_id"
        " WHERE p.data_scadenza BETWEEN ? AND ? ORDER BY p.data_scadenza",
        (start.isoformat(), end.isoformat()),
    ).fetchall()
    return [DuePayment(*row) for row in rows if row[0] not in skip_ids]


def euros(cents: int) -> str:
    """For display: 123456 -> "€ 1,234.56"."""
    return f"€ {from_cents(cents):,.2f}"


def whole_euros(cents: int) -> int:
    """For charts, which need plain numbers: cents rounded to whole euros, without float."""
    return (cents + 50) // 100
