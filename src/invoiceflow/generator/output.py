"""Write a generated dataset to disk: one XML per invoice, plus ground-truth CSV files."""

import csv
from pathlib import Path

from invoiceflow.generator.dataset import Dataset
from invoiceflow.generator.xml_writer import invoice_to_xml
from invoiceflow.money import from_cents

LABELS_FILE = "labels.csv"
SUPPLIERS_FILE = "suppliers.csv"

LABEL_COLUMNS = ("file", "numero", "data", "supplier_name", "supplier_vat", "total",
                 "spend_category", "label", "kind", "detail", "duplicate_of")


def write_dataset(dataset: Dataset, out_dir: Path) -> None:
    """Write `<file_id>.xml` files, `labels.csv` and `suppliers.csv` into `out_dir`.

    `labels.csv` is the ground truth used to measure the checks; `suppliers.csv` lists the
    legitimate suppliers (a stand-in registry for VIES and for the usual IBAN of each supplier).
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    with open(out_dir / LABELS_FILE, "w", encoding="utf-8", newline="") as labels:
        writer = csv.writer(labels)
        writer.writerow(LABEL_COLUMNS)
        for item in dataset.invoices:
            file_name = f"{item.file_id}.xml"
            progressivo = item.file_id.rsplit("_", 1)[1]
            (out_dir / file_name).write_bytes(invoice_to_xml(item.invoice, progressivo))
            inv = item.invoice
            total = inv.importo_totale_cents
            writer.writerow((
                file_name, inv.numero, inv.data.isoformat(), inv.cedente.denominazione,
                inv.cedente.partita_iva, "" if total is None else f"{from_cents(total):.2f}",
                item.spend_category, item.label, item.kind, item.detail,
                f"{item.duplicate_of}.xml" if item.duplicate_of else "",
            ))
    with open(out_dir / SUPPLIERS_FILE, "w", encoding="utf-8", newline="") as suppliers:
        writer = csv.writer(suppliers)
        writer.writerow(("name", "partita_iva", "iban", "category"))
        for s in dataset.suppliers:
            writer.writerow((s.name, s.party.partita_iva, s.iban, s.category))
