"""Measures the checks on a generated dataset, using its labels.csv as the answer key.

    python -m invoiceflow.checks.evaluate data/generated/run1

For every kind of error the generator plants, it counts how many invoices the checks catch
(the expected rule is among the findings), and it counts false alarms: correct invoices, and
frauds or duplicates (which are arithmetically correct), that the checks report anyway.
"""

import argparse
import csv
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from invoiceflow.checks import check_invoice
from invoiceflow.importers.ingest import read_invoices


@dataclass
class Evaluation:
    planted: Counter[str] = field(default_factory=Counter)  # errors per kind
    caught: Counter[str] = field(default_factory=Counter)  # of which reported by the checks
    clean: int = 0  # invoices that should pass (ok, duplicate, fraud)
    false_alarms: list[tuple[str, str]] = field(default_factory=list)  # (file, rule)


def evaluate(dataset_dir: Path) -> Evaluation:
    result = Evaluation()
    with open(dataset_dir / "labels.csv", encoding="utf-8", newline="") as labels:
        for row in csv.DictReader(labels):
            path = dataset_dir / row["file"]
            [invoice] = read_invoices(path, path.read_bytes())
            codes = {finding.code for finding in check_invoice(invoice)}
            if row["label"] == "error":
                result.planted[row["kind"]] += 1
                if row["kind"] in codes:
                    result.caught[row["kind"]] += 1
            else:
                result.clean += 1
                result.false_alarms.extend((row["file"], code) for code in sorted(codes))
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Measure the checks on a generated dataset.")
    parser.add_argument("dataset", type=Path, help="folder with labels.csv and invoices/")
    args = parser.parse_args(argv)
    result = evaluate(args.dataset)

    print(f"{'error kind':<24}{'caught':>8}{'planted':>9}")
    for kind in sorted(result.planted):
        print(f"{kind:<24}{result.caught[kind]:>8}{result.planted[kind]:>9}")
    total_caught, total_planted = sum(result.caught.values()), sum(result.planted.values())
    print(f"{'all errors':<24}{total_caught:>8}{total_planted:>9}")
    print(f"false alarms: {len(result.false_alarms)} on {result.clean} invoices that should pass")
    for file, code in result.false_alarms[:10]:
        print(f"  {file}: {code}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
