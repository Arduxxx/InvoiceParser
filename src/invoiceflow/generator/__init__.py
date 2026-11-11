"""Fake-invoice generator: correct invoices plus labelled errors, duplicates and frauds.

    from invoiceflow.generator import DatasetConfig, generate_dataset

    dataset = generate_dataset(DatasetConfig(n_invoices=1000, seed=42))

Command line: `python -m invoiceflow.generator --help`.
"""

from invoiceflow.generator.core import SupplierProfile, make_client, make_invoice, make_supplier
from invoiceflow.generator.dataset import (
    Dataset,
    DatasetConfig,
    GeneratedInvoice,
    generate_dataset,
)
from invoiceflow.generator.output import write_dataset
from invoiceflow.generator.scenarios import ERROR_KINDS, FRAUD_KINDS, corrupt
from invoiceflow.generator.xml_writer import invoice_to_xml

__all__ = [
    "ERROR_KINDS",
    "FRAUD_KINDS",
    "Dataset",
    "DatasetConfig",
    "GeneratedInvoice",
    "SupplierProfile",
    "corrupt",
    "generate_dataset",
    "invoice_to_xml",
    "make_client",
    "make_invoice",
    "make_supplier",
    "write_dataset",
]
