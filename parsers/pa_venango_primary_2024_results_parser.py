#!/usr/bin/env python3
"""
Parse Venango County PA 2024 Primary precinct results.

Source: Venango PA 2024 Precinct Summary Report - with write-ins.pdf
(Electionware primary; 5-column candidate rows: total, vote%, election_day,
mail, provisional. A line_preprocessor strips the vote% token so the rows
match the standard 4-integer tail.)

District handling: delegate/alternate office headers carry "Nth Congressional
District" and U.S. House headers carry "Nth Congressional District"; the
shared engine only extracts district suffixes for listed statewide offices
("Nth DISTRICT"), so:
- a pre-parse pass rewrites "Nth Congressional District" -> "Nth District" so
  the engine's DISTRICT_ORDINAL_RE extracts the CD on U.S. House rows
  (Victory Twp spans CD 15/16; without this the two contests collide into
  districtless U.S. House rows and duplicate their zero-vote stat rows), and
- a post-parse pass moves the delegate-title suffix into the district column
  (repo convention: "Delegate to the X National Convention" + district=N,
  Adams/Washington 2024 style). State Senate/House titles print no district
  in this source and stay districtless.

Usage:
    python parsers/pa_venango_primary_2024_results_parser.py <input.pdf> <output.csv>
"""

import re
import sys
from pathlib import Path

from electionware_primary_np import (
    PrimaryConfig,
    parse_primary_pdf,
    write_primary_csv,
)
from electionware_precinct_np import prettify_all_caps_precinct


PCT_RE = re.compile(r"\s+\d+\.\d+%")

CONGRESSIONAL_DISTRICT_RE = re.compile(
    r"(\d+(?:ST|ND|RD|TH))\s+CONGRESSIONAL\s+DISTRICT", re.IGNORECASE
)

DELEGATE_DISTRICT_RE = re.compile(
    r"^((?:ALTERNATE )?DELEGATE TO THE .*?CONVENTION)"
    r"\s+(\d+)(?:ST|ND|RD|TH)\s+(?:CONGRESSIONAL\s+)?DISTRICT$",
    re.IGNORECASE,
)


def venango_strip_pct(line: str) -> str:
    line = CONGRESSIONAL_DISTRICT_RE.sub(r"\1 DISTRICT", line)
    return PCT_RE.sub("", line)


def move_delegate_district(rows: list[dict]) -> list[dict]:
    for r in rows:
        m = DELEGATE_DISTRICT_RE.match(r["office"])
        if m:
            r["office"] = m.group(1)
            r["district"] = str(int(m.group(2)))
    return rows


SKIP_PREFIXES = (
    "Precinct Summary Report",
    "Venango County General Primary 2024",
    "April 23, 2024 Venango County",
    "Primary Election",
    "Precinct Summary - ",
    "Report generated with Electionware",
    "Election Provisional",
    "TOTAL Mail Votes",
    "TOTAL VOTE %",
    "Day Votes",
    "Voter Turnout - Total",
    "Vote For ",
    "Total Votes Cast",
    "Contest Totals",
)


CONFIG = PrimaryConfig(
    county="Venango",
    skip_prefixes=SKIP_PREFIXES,
    county_header_suffix="Venango County",
    local_office_orientation="suffix",
    retention_style="retention",
    prettify_precinct=prettify_all_caps_precinct,
    line_preprocessor=venango_strip_pct,
)


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(f"Usage: {Path(sys.argv[0]).name} <input.pdf> <output.csv>")
    pdf_path = Path(sys.argv[1])
    out_path = Path(sys.argv[2])
    if not pdf_path.exists():
        sys.exit(f"Missing PDF: {pdf_path}")
    rows, precinct_count = parse_primary_pdf(pdf_path, CONFIG)
    rows = move_delegate_district(rows)
    write_primary_csv(rows, out_path)
    print(f"Wrote {len(rows)} rows across {precinct_count} precincts to {out_path}")
