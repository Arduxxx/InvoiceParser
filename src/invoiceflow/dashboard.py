"""Streamlit dashboard entry point (run with `make run`).

Reads the SQLite archive filled by the importer and shows: spend by month, supplier and
category; payments coming due; invoices uploaded more than once. Extra copies of a
duplicated invoice are left out of every total, so nothing is counted twice.
"""

import sqlite3
from datetime import date, timedelta
from pathlib import Path

import streamlit as st

from invoiceflow import reports, storage
from invoiceflow.duplicates import explain, find_duplicates, load_invoices


def bar_chart(totals: dict[str, int], label: str, biggest_first: bool) -> None:
    """One bar per key of totals (amounts in cents), in whole euros."""
    st.bar_chart(
        {label: list(totals), "€": [reports.whole_euros(c) for c in totals.values()]},
        x=label,
        y="€",
        sort="-€" if biggest_first else True,
    )


def main() -> None:
    st.set_page_config(page_title="InvoiceFlow", layout="wide")
    st.title("InvoiceFlow")

    db_path = Path(st.sidebar.text_input("Archive", str(Path("data") / "invoiceflow.sqlite")))
    if not db_path.is_file():
        st.info(
            f"No archive at {db_path}. Import some invoices first, e.g.\n\n"
            "`make import IN=data/generated/run1/invoices`"
        )
        return
    connection = storage.connect(db_path)
    try:
        show(connection)
    finally:
        connection.close()


def show(connection: sqlite3.Connection) -> None:
    duplicates = find_duplicates(load_invoices(connection))
    extra_copies = {invoice.id for group in duplicates for invoice in group[1:]}
    spends = reports.load_spend(connection, skip_ids=extra_copies)

    left, middle, right = st.columns(3)
    left.metric("Invoices", len(spends))
    middle.metric("Total spend", reports.euros(sum(s.amount_cents for s in spends)))
    right.metric("Duplicates found", len(extra_copies))

    st.header("Spend by month")
    bar_chart(reports.total_by(spends, reports.by_month), "month", biggest_first=False)

    by_supplier, by_category = st.columns(2)
    with by_supplier:
        st.header("Spend by supplier")
        bar_chart(reports.total_by(spends, lambda s: s.supplier), "supplier", True)
    with by_category:
        st.header("Spend by category")
        bar_chart(reports.total_by(spends, lambda s: s.category), "category", True)

    st.header("Coming due")
    start = st.date_input("From", date.today())
    days = st.slider("Days ahead", 1, 180, 30)
    if isinstance(start, date):
        due = reports.coming_due(connection, start, start + timedelta(days=days), extra_copies)
        st.write(f"{len(due)} payments, {reports.euros(sum(p.amount_cents for p in due))}")
        st.dataframe(
            [
                {"due": p.due, "supplier": p.supplier, "invoice": p.numero,
                 "amount": reports.euros(p.amount_cents), "IBAN": p.iban}
                for p in due
            ],
            hide_index=True,
        )

    st.header("Duplicates")
    if not duplicates:
        st.write("No invoice was uploaded twice.")
    for group in duplicates:
        st.warning(explain(group))

    st.header("Supplier categories")
    names = dict(connection.execute("SELECT partita_iva, nome FROM cedenti ORDER BY nome"))
    if names:
        vat = st.selectbox("Supplier", list(names), format_func=lambda vat: names[vat])
        category = st.text_input("Category", placeholder="e.g. software, utilities, travel")
        if st.button("Save category") and vat and category.strip():
            storage.set_category(connection, vat, category)
            st.rerun()

main()
