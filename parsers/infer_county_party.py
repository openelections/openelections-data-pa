#!/usr/bin/env python3
"""Post-process a county-level 2026 primary CSV: fill in the ``party``
column for statewide-office + U.S. House rows whose party is blank, using
a candidate->party map derived from the other already-parsed 2026
primary precinct CSVs.

State Senate and State House rows are left blank (the source PDF for some
counties omits party prefixes on legislative contest headers, and the
candidate may overlap between parties in primary cross-filing scenarios).

Usage:
    python parsers/infer_county_party.py <input.csv> [precinct_csv_dir]
"""

from __future__ import annotations

import csv
import glob
import sys
from collections import defaultdict, Counter
from pathlib import Path

STATEWIDE_OFFICES = {
    "Governor", "Lieutenant Governor", "Attorney General",
    "Auditor General", "State Treasurer", "U.S. Senate", "U.S. House",
}
SKIP_CANDS = {
    "Write-In Totals", "Overvotes", "Undervotes", "Not Assigned",
    "WRITE-IN1", "Write-in",
}


def build_map(precinct_dir: Path, skip_county: str) -> dict[str, str]:
    cand_party: defaultdict[str, Counter] = defaultdict(Counter)
    for f in sorted(glob.glob(str(precinct_dir / "20260519__pa__primary__*__precinct.csv"))):
        if skip_county.lower() in f.lower():
            continue
        with open(f) as fh:
            r = csv.DictReader(fh)
            for row in r:
                if row["office"] not in STATEWIDE_OFFICES:
                    continue
                p = row["party"]
                c = row["candidate"]
                if not p or not c or c in SKIP_CANDS:
                    continue
                cand_party[c][p] += 1
    return {c: cnt.most_common(1)[0][0] for c, cnt in cand_party.items() if cnt}


def main(argv: list[str]) -> None:
    if len(argv) < 2:
        sys.exit(f"Usage: {Path(argv[0]).name} <input.csv> [precinct_csv_dir]")
    src = Path(argv[1])
    precinct_dir = Path(argv[2]) if len(argv) > 2 else src.parent
    county = src.stem.split("__")[-2]  # 20260519__pa__primary__juniata__county.csv -> juniata
    mapping = build_map(precinct_dir, county)

    rows: list[dict] = []
    filled = 0
    with src.open() as fh:
        r = csv.DictReader(fh)
        fields = r.fieldnames
        for row in r:
            if row["office"] in STATEWIDE_OFFICES and not row["party"]:
                c = row["candidate"]
                if c not in SKIP_CANDS and c in mapping:
                    row["party"] = mapping[c]
                    filled += 1
            rows.append(row)
    with src.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        for row in rows:
            w.writerow(row)
    print(f"Filled party for {filled} rows in {src}")


if __name__ == "__main__":
    main(sys.argv)