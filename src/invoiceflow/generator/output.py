"""Write a generated dataset to disk: one XML per invoice, plus ground-truth CSV files."""

import csv
from pathlib import Path

from invoiceflow.generator.dataset import Dataset
from invoiceflow.generator.xml_writer import invoice_to_xml
from invoiceflow.money import from_cents

LABELS_FILE = "labels.csv"
SUPPLIERS_FILE = "suppliers.csv"
INVOICES_DIR = "invoices"

LABEL_COLUMNS = ("file", "numero", "data", "supplier_name", "supplier_vat", "total",
                 "spend_category", "label", "kind", "detail", "duplicate_of")


def invoice_path(file_id: str) -> str:
    """Path of an invoice's XML relative to the dataset directory, e.g. 'invoices/IT..._00000.xml'.

    Always written with '/' so labels.csv is the same on Windows and Linux.
    """
    return f"{INVOICES_DIR}/{file_id}.xml"


def write_dataset(dataset: Dataset, out_dir: Path) -> None:
    """Write `invoices/<file_id>.xml` files, `labels.csv` and `suppliers.csv` into `out_dir`.

    The XMLs are kept apart from the CSVs so that the importer can read `invoices/` without
    ever seeing the ground truth. `labels.csv` is the ground truth used to measure the checks;
    `suppliers.csv` lists the legitimate suppliers (a stand-in registry for VIES and for the
    usual IBAN of each supplier). Paths in `labels.csv` are relative to `out_dir`.
    """
    (out_dir / INVOICES_DIR).mkdir(parents=True, exist_ok=True)
    with open(out_dir / LABELS_FILE, "w", encoding="utf-8", newline="") as labels:
        writer = csv.writer(labels)
        writer.writerow(LABEL_COLUMNS)
        for item in dataset.invoices:
            path = invoice_path(item.file_id)
            progressivo = item.file_id.rsplit("_", 1)[1]
            (out_dir / path).write_bytes(invoice_to_xml(item.invoice, progressivo))
            inv = item.invoice
            total = inv.importo_totale_cents
            writer.writerow((
                path, inv.numero, inv.data.isoformat(), inv.cedente.denominazione,
                inv.cedente.partita_iva, "" if total is None else f"{from_cents(total):.2f}",
                item.spend_category, item.label, item.kind, item.detail,
                invoice_path(item.duplicate_of) if item.duplicate_of else "",
            ))
    with open(out_dir / SUPPLIERS_FILE, "w", encoding="utf-8", newline="") as suppliers:
        writer = csv.writer(suppliers)
        writer.writerow(("name", "partita_iva", "iban", "category"))
        for s in dataset.suppliers:
            writer.writerow((s.name, s.party.partita_iva, s.iban, s.category))
