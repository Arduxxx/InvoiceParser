"""Streamlit dashboard entry point (run with `make run`).

Reads the SQLite archive filled by the importer and shows: spend by month, supplier and
category; payments coming due; invoices that failed the checks; suspicious invoices (anti-fraud
rules); invoices uploaded more than once. Extra copies of a duplicated invoice are left out of
every total, so nothing is counted twice. Invoices that failed the checks stay in the totals,
with a note: a real cost should not vanish because of a warning.
"""

import sqlite3
from datetime import date, timedelta
from pathlib import Path

import streamlit as st

from invoiceflow import reports, storage
from invoiceflow.antifraud import find_frauds
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
    problems = storage.load_problems(connection)
    with_errors = {problem.invoice_id for problem in problems}
    spends = reports.load_spend(connection, skip_ids=extra_copies)
    flags = find_frauds(connection)

    invoices, total, errors, copies, suspicious = st.columns(5)
    invoices.metric("Invoices", len(spends))
    total.metric("Total spend", reports.euros(sum(s.amount_cents for s in spends)))
    errors.metric("Failed the checks", len(with_errors))
    copies.metric("Duplicates found", len(extra_copies))
    suspicious.metric("Suspicious invoices", len({flag.invoice_id for flag in flags}))
    counted_with_errors = len(with_errors - extra_copies)
    if counted_with_errors:
        st.caption(
            f"Totals include {counted_with_errors} invoices that failed the checks:"
            " see the list below."
        )

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

    st.header("Invoices that failed the checks")
    if not problems:
        st.write("Every invoice passes the checks.")
    else:
        st.caption("Counted in the spend: check these amounts with the supplier.")
    for problem in problems:
        reasons = "\n".join(f"- {finding.message}" for finding in problem.findings)
        st.warning(
            f"**{problem.supplier}**, invoice {problem.numero} of {problem.data}:\n\n{reasons}"
        )

    st.header("Suspicious invoices")
    if not flags:
        st.write("No invoice looks suspicious.")
    for flag in flags:
        st.error(f"**{flag.rule.replace('_', ' ')}**: {flag.message}")

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
