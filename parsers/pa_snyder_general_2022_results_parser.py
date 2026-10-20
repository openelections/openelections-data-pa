#!/usr/bin/env python3
"""
Parser for Snyder County, PA 2022 General Election precinct results.

Source: Snyder PA Precinct-Summary.pdf.pdf
(Electionware "Summary Results Report" format, two pages per precinct:
a STATISTICS/US-Senate/Governor page and a Congress/General-Assembly page).
Parsed from the layout-preserving extract via the shared text engine in
``electionware_txt`` (mirroring the Adams 2022 parser; the natural_pdf
engine in ``electionware_precinct_np`` is not needed here).

Usage:
    python parsers/pa_snyder_general_2022_results_parser.py \
        "<input.txt|input.pdf>" "<output.csv>"

Snyder 2022 conventions:
  - Precinct names print as "ADAMS TOWNSHIP"; they are title-cased
    ("Adams Township") and numbered boroughs keep their number
    ("Selinsgrove Borough 1"), matching the Mifflin/Potter 2022 files.
  - Offices printed:
      "UNITED STATES SENATOR"                          -> U.S. Senate
      "GOVERNOR and LIEUTENANT GOVERNOR"               -> Governor
      "REPRESENTATIVE IN CONGRESS 15TH DISTRICT"       -> U.S. House (15)
      "REPRESENTATIVE IN THE GENERAL ASSEMBLY 85TH DISTRICT"
                                                       -> State House (85)
  - The report already prints GRN (no GNP remap needed).
  - Output uses the 2022 convention header with ``early_voting`` (the
    report's Mail Votes column) and write-in rows labelled "Write Ins".
"""

import csv
import re
import sys
from pathlib import Path

from electionware_txt import TxtConfig, parse_input
from electionware_precinct_np import title_case


def prettify_precinct(name: str) -> str:
    # "MONROE TOWNSHIP #1" -> "Monroe Township 1"
    return re.sub(r"\s*#\s*(\d+)\s*$", r" \1", title_case(name.strip()))


# "MCCLURE BOROUGH" title-cases to "Mcclure"; restore the conventional form.
PRECINCT_FIXUPS = {"Mcclure": "McClure"}


US_SENATOR_RE = re.compile(r"^United States Senator$", re.IGNORECASE)
GOVERNOR_RE = re.compile(r"^Governor and Lieutenant Governor$", re.IGNORECASE)
US_CONGRESS_RE = re.compile(r"^Representative in Congress\s+(\d+)(?:st|nd|rd|th)?\s+District$", re.IGNORECASE)
STATE_ASSEMBLY_RE = re.compile(
    r"^Representative in the General Assembly\s+(\d+)(?:st|nd|rd|th)?\s+District$",
    re.IGNORECASE,
)


def normalize_office(line: str):
    line = re.sub(r"\s+", " ", line).strip()
    if US_SENATOR_RE.match(line):
        return ("U.S. Senate", "")
    if GOVERNOR_RE.match(line):
        return ("Governor", "")
    m = US_CONGRESS_RE.match(line)
    if m:
        return ("U.S. House", m.group(1))
    m = STATE_ASSEMBLY_RE.match(line)
    if m:
        return ("State House", m.group(1))
    # Local offices / referendum: keep as printed.
    return (line, "")


CONFIG = TxtConfig(
    county="Snyder",
    normalize_office=normalize_office,
    prettify_precinct=lambda n: " ".join(
        PRECINCT_FIXUPS.get(w, w) for w in prettify_precinct(n).split(" ")
    ),
    # KEY (Keystone) is not in the shared PARTY_CODES for 2022 reports.
    extra_parties=("KEY",),
)

FIELDNAMES = [
    "county", "precinct", "office", "district", "party", "candidate",
    "votes", "election_day", "early_voting", "provisional",
]


def main(argv):
    if len(argv) != 3:
        sys.exit(f"Usage: {Path(argv[0]).name} <input.txt|input.pdf> <output.csv>")
    rows, precinct_count, warnings, unnamed = parse_input(argv[1], CONFIG)
    out_path = Path(argv[2])
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDNAMES)
        writer.writeheader()
        for row in rows:
            out = {
                "county": row["county"],
                "precinct": row["precinct"],
                "office": row["office"],
                "district": row["district"],
                "party": row["party"],
                "candidate": "Write Ins" if row["candidate"] == "Write-ins" else row["candidate"],
                "votes": row["votes"],
                "election_day": row["election_day"],
                "early_voting": row["mail"],
                "provisional": row["provisional"],
            }
            writer.writerow(out)
    print(f"Wrote {len(rows)} rows across {precinct_count} precincts to {out_path}")
    if unnamed:
        print(f"Skipped {unnamed} unnamed (county-summary) Statistics segment(s)")
    if warnings:
        print(f"WARNING: {len(warnings)} unmatched lines:")
        for w in warnings[:40]:
            print("  " + w)
        if len(warnings) > 40:
            print("  ...")


if __name__ == "__main__":
    main(sys.argv)