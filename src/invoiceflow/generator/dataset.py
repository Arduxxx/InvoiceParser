"""A whole labelled dataset: one client, a pool of suppliers, a year of invoices.

Each invoice carries its ground truth (ok / error / duplicate / fraud, plus the kind and a
human-readable detail), which is what the final-report measurements compare against.
The same seed always gives the same dataset.
"""

from dataclasses import dataclass, replace
from datetime import date, timedelta
from random import Random
from typing import Literal

from invoiceflow.generator.core import (
    SupplierProfile,
    format_number,
    make_client,
    make_invoice,
    make_supplier,
)
from invoiceflow.generator.scenarios import (
    FRAUD_KINDS,
    corrupt,
    inflate_amount,
    make_clone,
    reformat_number,
    with_new_iban,
)
from invoiceflow.model import Invoice, Party

Label = Literal["ok", "error", "duplicate", "fraud"]

# A supplier needs this many earlier invoices before "usual IBAN" / "usual amount" make sense.
MIN_HISTORY_FOR_FRAUD = 3

_BASE36 = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"


@dataclass(frozen=True)
class DatasetConfig:
    n_invoices: int = 1000  # total, duplicates included
    n_suppliers: int = 30
    start: date = date(2026, 1, 1)
    days: int = 365
    error_rate: float = 0.05      # probability that an invoice gets one field corrupted
    duplicate_rate: float = 0.03  # share of the dataset that are re-uploads of earlier invoices
    fraud_rate: float = 0.05      # probability that an eligible invoice is fraudulent
    seed: int = 0


@dataclass(frozen=True)
class GeneratedInvoice:
    file_id: str          # SdI-style file name without extension, unique in the dataset
    invoice: Invoice
    spend_category: str   # e.g. "software", "utenze" — for the dashboard
    label: Label
    kind: str = ""        # error / fraud kind; "" for ok and duplicates
    detail: str = ""      # why it is wrong or suspicious, in words
    duplicate_of: str = ""  # file_id of the original, for duplicates


@dataclass(frozen=True)
class Dataset:
    client: Party
    suppliers: tuple[SupplierProfile, ...]  # the legitimate, registered suppliers
    invoices: tuple[GeneratedInvoice, ...]


def progressivo(index: int) -> str:
    """5-character alphanumeric ProgressivoInvio, as in SdI file names."""
    chars = []
    for _ in range(5):
        index, digit = divmod(index, 36)
        chars.append(_BASE36[digit])
    if index:
        raise ValueError("more than 36**5 invoices")
    return "".join(reversed(chars))


def file_id_for(invoice: Invoice, index: int) -> str:
    return f"{invoice.cedente.id_paese}{invoice.cedente.partita_iva}_{progressivo(index)}"


def generate_dataset(config: DatasetConfig) -> Dataset:
    if config.n_invoices < 0 or config.n_suppliers < 1 or config.days < 1:
        raise ValueError(f"invalid config: {config}")
    rng = Random(config.seed)
    client = make_client(rng)
    names: set[str] = set()
    suppliers: list[SupplierProfile] = []
    for _ in range(config.n_suppliers):
        supplier = make_supplier(rng, taken_names=names)
        names.add(supplier.name)
        suppliers.append(supplier)
    # Some suppliers invoice often (utilities, software), others rarely.
    weights = [rng.randint(1, 10) for _ in suppliers]

    n_duplicates = round(config.n_invoices * config.duplicate_rate)
    n_base = config.n_invoices - n_duplicates
    dates = sorted(config.start + timedelta(days=rng.randrange(config.days))
                   for _ in range(n_base))

    next_number = {s.party.partita_iva: 1 for s in suppliers}
    items: list[GeneratedInvoice] = []
    for index, issue_date in enumerate(dates):
        supplier = rng.choices(suppliers, weights)[0]
        key = supplier.party.partita_iva
        history = next_number[key] - 1
        numero = format_number(supplier.number_format, next_number[key], issue_date.year)
        next_number[key] += 1
        invoice = make_invoice(rng, supplier, client, issue_date, numero)

        label: Label = "ok"
        kind = detail = ""
        if history >= MIN_HISTORY_FOR_FRAUD and rng.random() < config.fraud_rate:
            label, kind = "fraud", rng.choice(FRAUD_KINDS)
            invoice, detail = _apply_fraud(kind, invoice, supplier, client, rng, names)
        elif rng.random() < config.error_rate:
            label = "error"
            invoice, kind, detail = corrupt(invoice, rng)
        items.append(GeneratedInvoice(
            file_id=file_id_for(invoice, index), invoice=invoice,
            spend_category=supplier.category, label=label, kind=kind, detail=detail,
        ))

    originals = [item for item in items if item.label == "ok"]
    for k in range(min(n_duplicates, len(originals))):
        original = rng.choice(originals)
        invoice = original.invoice
        if rng.random() < 0.5:
            invoice = replace(invoice, numero=reformat_number(invoice.numero, rng))
        if invoice.numero == original.invoice.numero:
            detail = "same invoice uploaded again under another file name"
        else:
            detail = f"same invoice, numero written {invoice.numero!r} instead of " \
                     f"{original.invoice.numero!r}"
        items.append(GeneratedInvoice(
            file_id=file_id_for(invoice, n_base + k), invoice=invoice,
            spend_category=original.spend_category, label="duplicate", detail=detail,
            duplicate_of=original.file_id,
        ))
    # Re-uploads land among the invoices of the same day; stable sort keeps originals first.
    items.sort(key=lambda item: item.invoice.data)
    return Dataset(client=client, suppliers=tuple(suppliers), invoices=tuple(items))


def _apply_fraud(kind: str, invoice: Invoice, supplier: SupplierProfile, client: Party,
                 rng: Random, names: set[str]) -> tuple[Invoice, str]:
    if kind == "iban_change":
        return with_new_iban(invoice, rng)
    if kind == "outlier_amount":
        return inflate_amount(invoice, supplier, rng)
    if kind == "clone_supplier":
        clone = make_clone(supplier, rng)
        numero = format_number(clone.number_format, rng.randint(1, 999), invoice.data.year)
        detail = (f"impersonates {supplier.name!r} ({supplier.party.partita_iva}) as "
                  f"{clone.name!r} ({clone.party.partita_iva})")
        return make_invoice(rng, clone, client, invoice.data, numero), detail
    if kind == "unknown_vat":
        stranger = make_supplier(rng, category=supplier.category, taken_names=names)
        numero = format_number(stranger.number_format, rng.randint(1, 999), invoice.data.year)
        detail = f"partita IVA {stranger.party.partita_iva} not in the supplier registry"
        return make_invoice(rng, stranger, client, invoice.data, numero), detail
    raise ValueError(f"unknown fraud kind {kind!r}")
