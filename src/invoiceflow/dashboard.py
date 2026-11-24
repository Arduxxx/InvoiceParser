"""Streamlit dashboard entry point (run with `make run`).

Reads the SQLite archive filled by the importer. A menu on the left, with icons and grouped in
sections, opens one page at a time:
an overview of spending (by month, supplier and category), payments coming due, invoices that
failed the checks, suspicious invoices (anti-fraud rules), invoices uploaded more than once and
supplier categories. Extra copies of a duplicated invoice are left out of every total, so
nothing is counted twice. Invoices that failed the checks stay in the totals, with a note: a
real cost should not vanish because of a warning.
"""

import sqlite3
from datetime import date, timedelta
from functools import partial
from pathlib import Path

import streamlit as st

from invoiceflow import reports, storage
from invoiceflow.antifraud import Flag, find_frauds
from invoiceflow.duplicates import ArchivedInvoice, find_duplicates, load_invoices

# What each anti-fraud rule means, in words a user understands: (title, icon, explanation)
FRAUD_RULES = {
    "iban_change": (
        "New bank account",
        ":material/account_balance:",
        "A known supplier asks to be paid to an IBAN it never used before: the classic scam where"
        " a fraudster copies a real supplier's invoice with their own bank account.",
    ),
    "clone_supplier": (
        "Look-alike supplier",
        ":material/group:",
        "A new supplier whose name is almost identical to a known one, but with a different VAT"
        " number: someone may be impersonating a supplier you trust.",
    ),
    "outlier_amount": (
        "Unusual amount",
        ":material/trending_up:",
        "The total is far above anything this supplier invoiced before.",
    ),
}


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
    st.sidebar.title("InvoiceFlow")

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
    flags = find_frauds(connection)

    with_errors = {problem.invoice_id for problem in problems}
    suspicious_count = len({flag.invoice_id for flag in flags})

    def titled(name: str, count: int = 0) -> str:
        """The page name, with how many items it lists when there are any."""
        return f"{name} ({count})" if count else name

    # Each page is a function; partial() hands it the data it needs. Material icon names:
    # https://fonts.google.com/icons
    menu = {
        "Spending": [
            st.Page(partial(overview, connection, extra_copies, with_errors), title="Overview",
                    icon=":material/dashboard:", url_path="overview", default=True),
            st.Page(partial(coming_due, connection, extra_copies), title="Coming due",
                    icon=":material/event:", url_path="coming-due"),
        ],
        "Alerts": [
            st.Page(partial(failed_checks, problems),
                    title=titled("Failed the checks", len(problems)),
                    icon=":material/error:", url_path="failed-checks"),
            st.Page(partial(suspicious, connection, flags),
                    title=titled("Suspicious invoices", suspicious_count),
                    icon=":material/gpp_maybe:", url_path="suspicious"),
            st.Page(partial(duplicates_page, duplicates),
                    title=titled("Duplicates", len(extra_copies)),
                    icon=":material/content_copy:", url_path="duplicates"),
        ],
        "Suppliers": [
            st.Page(partial(categories, connection, extra_copies), title="Supplier categories",
                    icon=":material/sell:", url_path="categories"),
        ],
    }
    page = st.navigation(menu)
    st.title(page.title)
    page.run()


def overview(connection: sqlite3.Connection, extra_copies: set[int], with_errors: set[int]) -> None:
    spends = reports.load_spend(connection, skip_ids=extra_copies)
    invoices, total = st.columns(2)
    invoices.metric("Invoices", len(spends))
    total.metric("Total spend", reports.euros(sum(s.amount_cents for s in spends)))
    counted_with_errors = len(with_errors - extra_copies)
    if counted_with_errors:
        st.caption(
            f"Totals include {counted_with_errors} invoices that failed the checks:"
            " see that page in the menu."
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


def coming_due(connection: sqlite3.Connection, extra_copies: set[int]) -> None:
    start = st.date_input("From", date.today())
    days = st.slider("Days ahead", 1, 180, 30)
    if not isinstance(start, date):
        return
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


def failed_checks(problems: list[storage.InvoiceProblems]) -> None:
    if not problems:
        st.success("Every invoice passes the checks.")
        return
    st.caption("Counted in the spend: check these amounts with the supplier.")
    for problem in problems:
        reasons = "\n".join(f"- {finding.message}" for finding in problem.findings)
        st.warning(
            f"**{problem.supplier}**, invoice {problem.numero} of {problem.data}:\n\n{reasons}"
        )


def suspicious(connection: sqlite3.Connection, flags: list[Flag]) -> None:
    if not flags:
        st.success("No invoice looks suspicious.")
        return
    st.caption("These invoices add up correctly but look like a scam. Click one to see why.")
    invoices = reports.invoice_summaries(connection, {flag.invoice_id for flag in flags})
    by_rule: dict[str, list[Flag]] = {}
    for flag in flags:
        by_rule.setdefault(flag.rule, []).append(flag)
    rules = [rule for rule in FRAUD_RULES if rule in by_rule]
    tabs = st.tabs([f"{FRAUD_RULES[rule][0]} ({len(by_rule[rule])})" for rule in rules])
    for tab, rule in zip(tabs, rules, strict=True):
        title, icon, explanation = FRAUD_RULES[rule]
        with tab:
            st.info(explanation, icon=icon)
            for flag in sorted(by_rule[rule], key=lambda f: invoices[f.invoice_id].data):
                invoice = invoices[flag.invoice_id]
                label = (f"{invoice.data} · {invoice.supplier} · invoice {invoice.numero}"
                         f" · {reports.euros(invoice.total_cents)}")
                with st.expander(label, icon=icon):
                    st.write(flag.message)


def duplicates_page(duplicates: list[list[ArchivedInvoice]]) -> None:
    if not duplicates:
        st.success("No invoice was uploaded twice.")
        return
    st.info(
        "The same invoice was imported more than once: same supplier, date and number, even if"
        " the number is written differently. Only the first copy counts in the totals.",
        icon=":material/content_copy:",
    )
    for group in sorted(duplicates, key=lambda g: g[0].data):
        first = group[0]
        label = (f"{first.data} · {first.supplier_name} · invoice {first.numero}"
                 f" · {len(group)} copies")
        with st.expander(label, icon=":material/content_copy:"):
            st.dataframe(
                [
                    {
                        "copy": "original (counted)" if n == 0 else "extra copy (not counted)",
                        "number as written": invoice.numero,
                        "total": "—" if invoice.total_cents is None
                        else reports.euros(invoice.total_cents),
                        "file": Path(invoice.source_path).name,
                    }
                    for n, invoice in enumerate(group)
                ],
                hide_index=True,
            )


def categories(connection: sqlite3.Connection, extra_copies: set[int]) -> None:
    suppliers = reports.supplier_summary(connection, skip_ids=extra_copies)
    if not suppliers:
        st.write("No suppliers yet.")
        return
    by_vat = {supplier.partita_iva: supplier for supplier in suppliers}

    st.header("All suppliers")
    missing = sum(not supplier.category for supplier in suppliers)
    if missing:
        st.caption(f"{missing} of {len(suppliers)} suppliers have no category yet.")
    st.dataframe(
        [
            {"supplier": s.name, "VAT number": s.partita_iva, "category": s.category or "—",
             "invoices": s.invoices, "spend": reports.euros(s.spend_cents)}
            for s in sorted(suppliers, key=lambda s: s.spend_cents, reverse=True)
        ],
        hide_index=True,
    )

    st.header("Set a category")
    vat = st.selectbox(
        "Supplier", list(by_vat), format_func=lambda v: by_vat[v].name, key="set_supplier"
    )
    category = st.text_input("Category", placeholder="e.g. software, utilities, travel")
    if st.button("Save category") and vat and category.strip():
        storage.set_category(connection, vat, category)
        st.rerun()

    st.header("Remove a category")
    with_category = [s.partita_iva for s in suppliers if s.category]
    if not with_category:
        st.caption("No supplier has a category yet.")
        return
    chosen = st.selectbox(
        "Supplier",
        with_category,
        format_func=lambda v: f"{by_vat[v].name} ({by_vat[v].category})",
        key="remove_supplier",
    )
    if st.button("Remove category") and chosen:
        storage.remove_category(connection, chosen)
        st.rerun()


main()
