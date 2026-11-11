"""Compare two complete exports and write a reproducibility report."""
import argparse
import csv
import hashlib
import json
from pathlib import Path


def inspect(directory):
    expected = json.loads((directory / "checksums.json").read_text(encoding="utf-8"))
    checksums = {}
    counts = {}
    for filename, digest in sorted(expected.items()):
        path = directory / filename
        with path.open("rb") as handle:
            actual = hashlib.file_digest(handle, "sha256").hexdigest()
        if digest != actual:
            raise ValueError(f"Checksum mismatch in {path}")
        checksums[filename] = actual
        if path.suffix == ".csv":
            with path.open(newline="", encoding="utf-8") as handle:
                reader = csv.reader(handle)
                next(reader)
                counts[path.stem] = sum(1 for _ in reader)
    summary = json.loads((directory / "validation.json").read_text(encoding="utf-8"))
    if counts != summary["rows"]:
        raise ValueError(f"Row counts differ from validation in {directory}")
    with (directory / "seed.jsonl").open(encoding="utf-8") as handle:
        jsonl_rows = sum(1 for _ in handle)
    if jsonl_rows != sum(counts.values()) - counts["ground_truth"]:
        raise ValueError(f"JSON Lines seed row count differs in {directory}")
    return {"configuration": summary["configuration"], "rows": counts, "sha256": checksums}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("first", type=Path)
    parser.add_argument("second", type=Path)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    first, second = inspect(args.first), inspect(args.second)
    if first != second:
        raise SystemExit("Reproducibility check failed.")
    report = json.dumps({"identical": True, **first}, indent=2, sort_keys=True)
    if args.report:
        args.report.write_text(report + "\n", encoding="utf-8")
    print(report)


if __name__ == "__main__":
    main()