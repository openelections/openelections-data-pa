#!/usr/bin/env python3
"""
Parse Mifflin County PA 2022 General Election precinct results.

Source: Mifflin PA 2022 November Election Offical Precinct Summary Results.pdf
(Electionware "Summary Results Report", 4 columns TOTAL / Election Day /
Mail Votes / Provisional, 26 precincts x 3 pages). Parsed from the
pdftotext -layout extract with the shared text engine in
``electionware_txt`` (same family as the county's 2022 Mercer parser).

Contest set (2022 federal/legislative general):
  UNITED STATES SENATOR                                   -> U.S. Senate
  GOVERNOR AND LIEUTENANT GOVERNOR                        -> Governor
  REPRESENTATIVE IN CONGRESS 13TH CONG. DIST.             -> U.S. House 13
  SENATOR IN THE GENERAL ASSEMBLY 30TH SEN. DIST.         -> State Senate 30
  REPRESENTATIVE IN THE GENERAL ASSEMBLY 171ST LEG. DIST. -> State House 171
  REPRESENTATIVE IN THE GENERAL ASSEMBLY 85TH LEG. DIST.  -> State House 85

Mifflin-2022 quirks:
  - Every candidate row carries a party prefix (DEM/REP/LIB/GRN/KEY).
  - "KEY" (Keystone Party) rows are handled via ``extra_parties``.
  - Statistic labels sit BELOW their numbers-only line (the reverse of
    the usual Electionware order); the shared engine already handles
    this via ``last_numeric``.
  - Registered Voters prints as 0 for every precinct (also 0 in the
    county's 2023/2025 reports).
  - Overvotes/Undervotes rows are kept (same convention as the Adams
    and Venango 2022 files, which use the same header layout).
  - "Write-In Totals" rows are renamed to the 2022 convention
    "Write Ins" with party=""; named write-in detail rows
    ("Write-In: Mary Forzani") are skipped (they are included in the
    Write-In Totals line).
  - Precinct names are ALL-CAPS with a hyphenated ward/precinct suffix
    ("ARMAGH-EAST", "BROWN-CHURCH HILL"); title-cased preserving
    punctuation and the Mc prefix.

Usage:
    python parsers/pa_mifflin_general_2022_results_parser.py \
        <input.pdf|input.txt> <output.csv>
"""

import csv
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from electionware_txt import TxtConfig, parse_input  # noqa: E402
from electionware_precinct_np import prettify_huntingdon_precinct  # noqa: E402

EXTRA_JUNK_2022 = (
    r"^\s*General Election\s*$",
    r"^\s*November\s+8,\s*2022\s*$",
)


def normalize(line: str):
    s = " ".join(line.split())
    upper = s.upper()
    if upper == "UNITED STATES SENATOR":
        return ("U.S. Senate", "")
    if upper == "GOVERNOR AND LIEUTENANT GOVERNOR":
        return ("Governor", "")
    if upper.startswith("REPRESENTATIVE IN CONGRESS"):
        # "... 13TH CONG. DIST." -> 13
        m = re.search(r"(\d+)", s.split("CONGRESS", 1)[1])
        return ("U.S. House", m.group(1))
    if upper.startswith("SENATOR IN THE GENERAL ASSEMBLY"):
        # "... 30TH SEN. DIST." -> 30
        m = re.search(r"(\d+)", s.split("ASSEMBLY", 1)[1])
        return ("State Senate", m.group(1))
    if upper.startswith("REPRESENTATIVE IN THE GENERAL ASSEMBLY"):
        # "... 171ST LEG. DIST." -> 171
        m = re.search(r"(\d+)", s.split("ASSEMBLY", 1)[1])
        return ("State House", m.group(1))
    raise ValueError(f"Unrecognized office header: {line!r}")


NAME_MAP = {
    "JOHN FETTERMAN": "John Fetterman",
    "MEHMET OZ": "Mehmet Oz",
    "JOSH SHAPIRO": "Josh Shapiro",
    "DOUGLAS V MASTRIANO": "Douglas V. Mastriano",
    "MATT HACKENBURG": "Matt Hackenburg",
    "ERIK GERHARDT": "Erik Gerhardt",
    "CHRISTINA DIGIULIO": "Christina DiGiulio",
    "JOE SOLOSKI": "Joe Soloski",
    "RICHARD L WEISS": "Richard L. Weiss",
    "DANIEL WASSMER": "Daniel Wassmer",
    "JOHN JOYCE": "John Joyce",
    "CAROL TAYLOR": "Carol Taylor",
    "JUDY WARD": "Judy Ward",
    "KERRY A BENNINGHOFF": "Kerry A Benninghoff",
    "ROBERT ZEIGLER": "Robert Zeigler",
    "DAVID H ROWE": "David Hummer Rowe",
}


CONFIG = TxtConfig(
    county="Mifflin",
    normalize_office=normalize,
    prettify_precinct=prettify_huntingdon_precinct,
    party_optional=False,  # every candidate row prints a party prefix
    extra_parties=("KEY",),
    extra_junk=EXTRA_JUNK_2022,
)


def main(argv):
    if len(argv) != 3:
        sys.exit(f"Usage: {Path(argv[0]).name} <input.pdf|input.txt> <output.csv>")
    rows, precinct_count, warnings, unnamed = parse_input(argv[1], CONFIG)
    for row in rows:
        key = row["candidate"].upper()
        row["candidate"] = NAME_MAP.get(key, row["candidate"])
        if row["candidate"] == "Write-ins":
            row["candidate"] = "Write Ins"
    # 2022-convention column names: the Mail Votes column goes to
    # early_voting.
    out_path = Path(argv[2])
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=[
                "county", "precinct", "office", "district", "party",
                "candidate", "votes", "election_day", "early_voting",
                "provisional",
            ],
        )
        writer.writeheader()
        for row in rows:
            writer.writerow({
                "county": row["county"],
                "precinct": row["precinct"],
                "office": row["office"],
                "district": row["district"],
                "party": row["party"],
                "candidate": row["candidate"],
                "votes": row["votes"],
                "election_day": row["election_day"],
                "early_voting": row["mail"],
                "provisional": row["provisional"],
            })
    print(
        f"Wrote {len(rows)} rows across {precinct_count} precincts to {out_path}"
    )
    if unnamed:
        print(f"Skipped {unnamed} unnamed Statistics segment(s)")
    if warnings:
        print(f"WARNING: {len(warnings)} unmatched lines:")
        for w in warnings[:40]:
            print("  " + w)


if __name__ == "__main__":
    main(sys.argv)