"""CSV Plotter (example tool): summarise a measurement CSV and draw a text bar chart."""

import argparse
import csv
import statistics
import sys

from tabulate import tabulate


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, help="CSV file with a header row")
    parser.add_argument("--column", help="numeric column to chart (default: the first one)")
    parser.add_argument("--width", type=int, default=40, help="bar width in characters")
    parser.add_argument("--sort", action="store_true", help="sort rows by value")
    args = parser.parse_args()

    with open(args.input, newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        print("The CSV file has no data rows.", file=sys.stderr)
        return 1

    label_col = next(iter(rows[0]))
    numeric = [c for c in rows[0] if c != label_col and _is_number(rows[0][c])]
    column = args.column or (numeric[0] if numeric else None)
    if column not in rows[0]:
        print(f"Column not found: {column}", file=sys.stderr)
        return 1

    values = [(r[label_col], float(r[column])) for r in rows]
    if args.sort:
        values.sort(key=lambda v: v[1], reverse=True)
    numbers = [v for _, v in values]
    print(tabulate([[column, len(numbers), min(numbers), max(numbers),
                     round(statistics.mean(numbers), 3)]],
                   headers=["column", "rows", "min", "max", "mean"]))
    print()
    peak = max(abs(n) for n in numbers) or 1
    for label, value in values:
        bar = "#" * round(abs(value) / peak * args.width)
        print(f"{label:>12} | {bar} {value:g}")
    return 0


def _is_number(text: str) -> bool:
    try:
        float(text)
        return True
    except ValueError:
        return False


if __name__ == "__main__":
    sys.exit(main())
