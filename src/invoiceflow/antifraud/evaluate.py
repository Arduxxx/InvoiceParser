"""Measures the anti-fraud rules on a generated dataset, using its labels.csv as the answer key.

    python -m invoiceflow.antifraud.evaluate data/generated/run1

The invoices are imported into a temporary archive and the rules run on it. For every fraud
kind it counts how many planted frauds are flagged by their rule, and it counts false alarms:
invoices that are not frauds (correct ones, errors, duplicates) but are flagged anyway.
"""

import argparse
import csv
import tempfile
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from invoiceflow import storage
from invoiceflow.antifraud import find_frauds
from invoiceflow.importers.ingest import ingest_folder


@dataclass
class FraudEvaluation:
    planted: Counter[str] = field(default_factory=Counter)  # frauds per kind
    caught: Counter[str] = field(default_factory=Counter)  # of which flagged by their rule
    regular: int = 0  # invoices that are not frauds
    false_alarms: list[tuple[str, str]] = field(default_factory=list)  # (file, rule)


def flagged_files(dataset_dir: Path) -> dict[str, set[str]]:
    """File name -> rules that flagged the invoice in it."""
    with tempfile.TemporaryDirectory() as tmp:
        connection = storage.connect(Path(tmp) / "archive.sqlite")
        try:
            ingest_folder(dataset_dir / "invoices", connection)
            paths = dict(connection.execute("SELECT id, source_path FROM invoices").fetchall())
            flags = find_frauds(connection)
        finally:
            connection.close()  # before the folder is deleted (needed on Windows)
    flagged: dict[str, set[str]] = {}
    for flag in flags:
        flagged.setdefault(Path(paths[flag.invoice_id]).name, set()).add(flag.rule)
    return flagged


def evaluate(dataset_dir: Path) -> FraudEvaluation:
    flagged = flagged_files(dataset_dir)
    result = FraudEvaluation()
    with open(dataset_dir / "labels.csv", encoding="utf-8", newline="") as labels:
        for row in csv.DictReader(labels):
            name = Path(row["file"]).name
            rules = flagged.get(name, set())
            if row["label"] == "fraud":
                result.planted[row["kind"]] += 1
                if row["kind"] in rules:
                    result.caught[row["kind"]] += 1
            else:
                result.regular += 1
                result.false_alarms.extend((name, rule) for rule in sorted(rules))
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Measure the anti-fraud rules.")
    parser.add_argument("dataset", type=Path, help="folder with labels.csv and invoices/")
    args = parser.parse_args(argv)
    result = evaluate(args.dataset)

    print(f"{'fraud kind':<20}{'caught':>8}{'planted':>9}")
    for kind in sorted(result.planted):
        print(f"{kind:<20}{result.caught[kind]:>8}{result.planted[kind]:>9}")
    alarms = len(result.false_alarms)
    per_100 = f"{100 * alarms / result.regular:.2f}" if result.regular else "0"
    print(f"false alarms: {alarms} on {result.regular} regular invoices ({per_100} per 100)")
    for name, rule in result.false_alarms[:10]:
        print(f"  {name}: {rule}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
