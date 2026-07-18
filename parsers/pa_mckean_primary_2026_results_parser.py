#!/usr/bin/env python3
"""Parse McKean County PA 2026 Primary precinct results.

Source: McKean County Precinct summary results pri gen 26.pdf
(Electionware precinct summary). Uses the shared ``electionware_primary_np``
engine with a county-specific ``PrimaryConfig``.

McKean's PDF reports 7-integer vote tails (TOTAL, Election Day, Mail Votes,
Provisional Votes, Spare 1, Spare 2, Spare 3); the Spare columns are
always zero and ignored.

Usage:
    uv run python parsers/pa_mckean_primary_2026_results_parser.py <input.pdf> <output.csv>
"""

import re
import sys
from pathlib import Path

from electionware_primary_np import (
    PrimaryConfig,
    parse_primary_pdf,
    write_primary_csv,
)


# McKean's PDF omits district ordinals on U.S. House / State House headers.
# Hardcode based on McKean's 2024 precinct CSV (PA-15, State House 67).
DISTRICT_FIXES = {"U.S. House": "15", "State House": "67"}


# 7-integer vote tail: TOTAL, ED, Mail, Prov, Spare1, Spare2, Spare3.
McKEAN_VOTE_TAIL_RE = re.compile(
    r"^(.*?)\s+(\d[\d,]*)\s+(\d[\d,]*)\s+(\d[\d,]*)\s+(\d[\d,]*)"
    r"\s+(\d[\d,]*)\s+(\d[\d,]*)\s+(\d[\d,]*)$"
)


SKIP_PREFIXES = (
    "Precinct Summary Results Report",
    "General Primary",
    "May 19, 2026",
    "Precinct Summary - ",
    "Report generated with Electionware",
    "Vote For ",
    "Election Day",
    "Day Votes",
    "Mail Votes",
    "Provisional",
    "TOTAL Mail Votes",
    "TOTAL Election",
    "TOTAL Provisional",
    "Election Mail VotesProvisional",
    "Voter Turnout - Total",
    "Voter Turnout - DEMOCRATIC",
    "Voter Turnout - REPUBLICAN",
    "Total Votes Cast",
    "Contest Totals",
    "Overvotes",
    "Undervotes",
    "Not Assigned",
)


CONFIG = PrimaryConfig(
    county="McKean",
    skip_prefixes=SKIP_PREFIXES,
    county_header_suffix="McKEAN COUNTY, PENNSYLVANIA",
    vote_tail_re=McKEAN_VOTE_TAIL_RE,
)


if __name__ == "__main__":
    argv = sys.argv
    if len(argv) != 3:
        sys.exit(f"Usage: {Path(argv[0]).name} <input.pdf> <output.csv>")
    pdf_path = Path(argv[1])
    out_path = Path(argv[2])
    if not pdf_path.exists():
        sys.exit(f"Missing PDF: {pdf_path}")
    rows, precinct_count = parse_primary_pdf(pdf_path, CONFIG)
    for r in rows:
        if not r["district"] and r["office"] in DISTRICT_FIXES:
            r["district"] = DISTRICT_FIXES[r["office"]]
    write_primary_csv(rows, out_path)
    print(
        f"Wrote {len(rows)} rows across {precinct_count} precincts to {out_path}"
    )