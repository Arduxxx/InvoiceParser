"""Streamlit dashboard entry point (run with `make run`).

Reads the SQLite archive filled by the importer. A menu on the left, with icons and grouped in
sections, opens one page at a time:
an overview of spending (by month, supplier and category), payments coming due, invoices that
failed the checks, suspicious invoices (anti-fraud rules), invoices uploaded more than once, one
invoice in detail and supplier categories.
New invoices (.xml, .xml.p7m, .pdf) can also be dropped in the sidebar: they are imported and
checked on the spot, and a box at the top of the page says what was found in each one.
Extra copies of a duplicated invoice are left out of every total, so nothing is counted twice.
Invoices that failed the checks stay in the totals, with a note: a real cost should not vanish
because of a warning.
"""

import sqlite3
from collections.abc import Callable
from datetime import date, timedelta
from functools import partial
from pathlib import Path

import streamlit as st

from invoiceflow import reports, storage
from invoiceflow.antifraud import Flag, find_frauds
from invoiceflow.checks.finding import Finding
from invoiceflow.duplicates import ArchivedInvoice, find_duplicates, load_invoices
from invoiceflow.importers.ingest import IngestReport, ingest_file

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
    connection = storage.connect(db_path)  # an archive that does not exist yet starts empty
    try:
        upload(connection)
        if connection.execute("SELECT COUNT(*) FROM invoices").fetchone() == (0,):
            st.info(
                "The archive is empty. Drop some invoices in the sidebar, or import a folder,"
                " e.g.\n\n`make import IN=data/generated/run1/invoices`"
            )
            return
        show(connection)
    finally:
        connection.close()


def upload(connection: sqlite3.Connection) -> None:
    """Sidebar form: the files dropped here are imported and checked like a folder would be.
    The ids of the new invoices are remembered, so the page can say what was found in them."""
    with st.sidebar.form("upload", clear_on_submit=True):
        files = st.file_uploader(
            "Add invoices", type=["xml", "p7m", "pdf"], accept_multiple_files=True
        )
        submitted = st.form_submit_button("Import", type="primary")
    if not submitted or not files:
        return
    last_id = connection.execute("SELECT COALESCE(MAX(id), 0) FROM invoices").fetchone()[0]
    report = IngestReport()
    for file in files:
        ingest_file(Path(file.name), file.getvalue(), connection, report)
    new_ids = connection.execute("SELECT id FROM invoices WHERE id > ?", (last_id,)).fetchall()
    st.session_state["just_imported"] = [row[0] for row in new_ids]
    if report.already_present:
        st.sidebar.info(f"{report.already_present} invoices were already in the archive.")
    for name, problem in report.errors:
        st.sidebar.error(f"Could not read {name}: {problem}")


def show(connection: sqlite3.Connection) -> None:
    duplicates = find_duplicates(load_invoices(connection))
    extra_copies = {invoice.id for group in duplicates for invoice in group[1:]}
    problems = storage.load_problems(connection)
    flags = find_frauds(connection)

    with_errors = {problem.invoice_id for problem in problems}
    suspicious_count = len({flag.invoice_id for flag in flags})

    # what was found in each invoice, for the "just imported" box and the details page
    original_of = {copy.id: group[0] for group in duplicates for copy in group[1:]}
    findings_of = {problem.invoice_id: problem.findings for problem in problems}
    flags_of: dict[int, list[Flag]] = {}
    for flag in flags:
        flags_of.setdefault(flag.invoice_id, []).append(flag)
    ids = [row[0] for row in connection.execute("SELECT id FROM invoices")]
    summaries = reports.invoice_summaries(connection, set(ids))
    names = {
        invoice_id: f"{s.supplier} · invoice {s.numero} · {s.data} · {reports.euros(s.total_cents)}"
        for invoice_id, s in sorted(summaries.items(), key=lambda item: item[1].data, reverse=True)
    }

    def outcome(invoice_id: int) -> None:
        show_outcome(
            names[invoice_id],
            findings_of.get(invoice_id, ()),
            flags_of.get(invoice_id, []),
            original_of.get(invoice_id),
        )

    just_imported = [i for i in st.session_state.get("just_imported", []) if i in names]

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
        "Invoices": [
            st.Page(partial(invoice_details, connection, names, just_imported, outcome),
                    title="Invoice details", icon=":material/receipt_long:",
                    url_path="invoice"),
        ],
        "Suppliers": [
            st.Page(partial(categories, connection, extra_copies), title="Supplier categories",
                    icon=":material/sell:", url_path="categories"),
        ],
    }
    page = st.navigation(menu)
    st.title(page.title)
    if just_imported:
        with st.container(border=True):
            title, hide = st.columns([5, 1])
            title.subheader(f"Just imported: {len(just_imported)} invoices")
            if hide.button("Hide"):
                del st.session_state["just_imported"]
                st.rerun()
            for invoice_id in just_imported:
                outcome(invoice_id)
    page.run()


def show_outcome(
    name: str, findings: tuple[Finding, ...], flags: list[Flag], original: ArchivedInvoice | None
) -> None:
    """What was found in one invoice, in one coloured box per kind of problem."""
    if original is not None:
        st.warning(
            f"**{name}** is a duplicate of invoice {original.numero} already in the archive"
            f" (file {Path(original.source_path).name}).",
            icon=":material/content_copy:",
        )
    for flag in flags:
        title = FRAUD_RULES[flag.rule][0] if flag.rule in FRAUD_RULES else flag.rule
        st.error(
            f"**{name}** looks suspicious ({title}): {flag.message}", icon=":material/gpp_maybe:"
        )
    if findings:
        reasons = "\n".join(f"- {finding.message}" for finding in findings)
        st.warning(f"**{name}** failed the checks:\n\n{reasons}", icon=":material/error:")
    if original is None and not flags and not findings:
        st.success(f"**{name}** passed every check.", icon=":material/check_circle:")


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
    rows = [
        {"date": problem.data, "supplier": problem.supplier, "invoice": problem.numero,
         "check": finding.code, "problem": finding.message}
        for problem in problems
        for finding in problem.findings
    ]
    kinds = sorted({row["check"] for row in rows})
    chosen = st.multiselect("Show only these checks", kinds, placeholder="all checks")
    st.dataframe([row for row in rows if not chosen or row["check"] in chosen], hide_index=True)


def invoice_details(
    connection: sqlite3.Connection,
    names: dict[int, str],
    just_imported: list[int],
    outcome: Callable[[int], None],
) -> None:
    ids = list(names)  # newest first
    invoice_id = st.selectbox(
        "Invoice (type to search)",
        ids,
        index=ids.index(just_imported[0]) if just_imported else 0,
        format_func=lambda i: names[i],
    )
    if invoice_id is None:
        return
    outcome(invoice_id)
    detail = reports.invoice_detail(connection, invoice_id)
    left, right = st.columns(2)
    items = list(detail.header.items())
    for column, part in ((left, items[:3]), (right, items[3:])):
        for label, value in part:
            column.markdown(f"**{label}:** {value}")
    st.subheader("Lines")
    st.dataframe(detail.lines, hide_index=True)
    vat, payments = st.columns(2)
    with vat:
        st.subheader("VAT summary")
        st.dataframe(detail.vat_summary, hide_index=True)
    with payments:
        st.subheader("Payments")
        st.dataframe(detail.payments, hide_index=True)


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
