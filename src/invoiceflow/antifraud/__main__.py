"""Command line: python -m invoiceflow.antifraud --db data/invoiceflow.sqlite"""

import argparse
from pathlib import Path

from invoiceflow import storage
from invoiceflow.antifraud import find_frauds


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="List suspicious invoices in the archive.")
    parser.add_argument("--db", type=Path, default=Path("data") / "invoiceflow.sqlite")
    args = parser.parse_args(argv)
    connection = storage.connect(args.db)
    try:
        flags = find_frauds(connection)
    finally:
        connection.close()
    print(f"{len(flags)} suspicious invoices found.")
    for flag in flags:
        print(f"  [{flag.rule}] {flag.message}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
