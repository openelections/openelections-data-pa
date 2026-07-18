#!/usr/bin/env python3
"""Post-process county-level summary CSVs: keep only standard statewide/
legislative offices and normalize office-name variants (e.g. "Member Of The
Democratic State Committee" -> "Member of Democratic State Committee").
Also collapses internal whitespace in office names (Cumberland spreads
"MEMBER OF THE ... STATE COMMITTEE" across columns) and normalizes
"Write- In Totals" -> "Write-In Totals".

Usage:
    python parsers/filter_county_csv.py <input.csv> <output.csv>
"""

from __future__ import annotations

import csv
import re
import sys
from pathlib import Path

OFFICE_NORMALIZE = {
    "Governor": "Governor",
    "Lieutenant Governor": "Lieutenant Governor",
    "Attorney General": "Attorney General",
    "Auditor General": "Auditor General",
    "State Treasurer": "State Treasurer",
    "U.S. House": "U.S. House",
    "State Senate": "State Senate",
    "State House": "State House",
    "State Representative": "State House",
    "U.S. Senate": "U.S. Senate",
    "President": "President",
    "Member of Democratic State Committee": "Member of Democratic State Committee",
    "Member of Republican State Committee": "Member of Republican State Committee",
    "Member Of The Democratic State Committee": "Member of Democratic State Committee",
    "Member Of The Republican State Committee": "Member of Republican State Committee",
    "Member Of The Democratic State Comm": "Member of Democratic State Committee",
    "Member Of The Republican State Comm": "Member of Republican State Committee",
    "Member of the Democratic State Committee": "Member of Democratic State Committee",
    "Member of the Republican State Committee": "Member of Republican State Committee",
}

FIELDNAMES = [
    "county", "office", "district", "party", "candidate",
    "votes", "election_day", "mail", "provisional",
]

SKIP_CANDIDATES = {
    "TOTAL VOTES CAST", "CONTEST TOTALS", "OVERVOTES", "UNDERVOTES",
    "NOT ASSIGNED", "TIMES CAST", "REGISTERED VOTERS",
    "VOTER TURNOUT - TOTAL", "VOTER TURNOUT - DEMOCRATIC",
    "VOTER TURNOUT - REPUBLICAN",
}

# Per-precinct committee races ("Democratic Committeeman Adams Twp",
# "Republican Committeewoman Precinct 3") leak into Member of State
# Committee contests because their headers don't match
# PER_PRECINCT_COMMITTEE_RE in the parser. Drop candidate rows whose
# name contains a committee-person title — these are never statewide
# committee candidates.
COMMITTEE_PERSON_RE = re.compile(r"Committee(?:man|woman|person)?", re.IGNORECASE)


def main(argv: list[str]) -> None:
    if len(argv) != 3:
        sys.exit(f"Usage: {Path(argv[0]).name} <input.csv> <output.csv>")
    in_path = Path(argv[1])
    out_path = Path(argv[2])
    kept = 0
    dropped = 0
    with in_path.open() as fh:
        reader = csv.DictReader(fh)
        rows = list(reader)
    out_rows = []
    for r in rows:
        # Collapse internal whitespace so "Member  Of The ... State Committee"
        # matches the normalize map.
        office_raw = re.sub(r"\s+", " ", r["office"]).strip()
        office = OFFICE_NORMALIZE.get(office_raw)
        if office is None:
            dropped += 1
            continue
        cand = r["candidate"].strip()
        if cand.upper() in SKIP_CANDIDATES:
            dropped += 1
            continue
        if COMMITTEE_PERSON_RE.search(cand):
            dropped += 1
            continue
        r["office"] = office
        # Normalize "Write- In Totals" -> "Write-In Totals"
        if cand.lower().replace(" ", "") == "write-intotals":
            r["candidate"] = "Write-In Totals"
        out_rows.append(r)
        kept += 1
    with out_path.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDNAMES)
        w.writeheader()
        for r in out_rows:
            w.writerow(r)
    print(f"Kept {kept} rows, dropped {dropped} rows -> {out_path}")


if __name__ == "__main__":
    main(sys.argv)