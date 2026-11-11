"""Command line: python -m invoiceflow.generator --out data/generated/run1 --n 1000 --seed 42"""

import argparse
from collections import Counter
from collections.abc import Sequence
from pathlib import Path

from invoiceflow.generator.dataset import DatasetConfig, generate_dataset
from invoiceflow.generator.output import INVOICES_DIR, write_dataset


def _rate(text: str) -> float:
    value = float(text)
    if not 0.0 <= value <= 1.0:
        raise argparse.ArgumentTypeError(f"must be between 0 and 1, got {text}")
    return value


def main(argv: Sequence[str] | None = None) -> int:
    defaults = DatasetConfig()
    parser = argparse.ArgumentParser(
        prog="python -m invoiceflow.generator",
        description="Generate fake FatturaPA invoices with ground-truth labels.",
    )
    parser.add_argument("--out", type=Path, default=Path("data/generated/default"),
                        help="output directory (must not already contain .xml files)")
    parser.add_argument("--n", type=int, default=defaults.n_invoices, help="number of invoices")
    parser.add_argument("--suppliers", type=int, default=defaults.n_suppliers)
    parser.add_argument("--seed", type=int, default=defaults.seed)
    parser.add_argument("--error-rate", type=_rate, default=defaults.error_rate)
    parser.add_argument("--duplicate-rate", type=_rate, default=defaults.duplicate_rate)
    parser.add_argument("--fraud-rate", type=_rate, default=defaults.fraud_rate)
    args = parser.parse_args(argv)

    out: Path = args.out
    if any(out.glob("*.xml")) or any((out / INVOICES_DIR).glob("*.xml")):
        parser.error(f"{out} already contains invoices; choose an empty directory")

    config = DatasetConfig(
        n_invoices=args.n, n_suppliers=args.suppliers, seed=args.seed,
        error_rate=args.error_rate, duplicate_rate=args.duplicate_rate,
        fraud_rate=args.fraud_rate,
    )
    dataset = generate_dataset(config)
    write_dataset(dataset, out)

    counts = Counter(item.label for item in dataset.invoices)
    summary = ", ".join(f"{label}={counts[label]}" for label in ("ok", "error", "duplicate",
                                                                 "fraud"))
    print(f"Wrote {len(dataset.invoices)} invoices to {out} ({summary})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
