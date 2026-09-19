#!/usr/bin/env python3
"""
Parse Mercer County PA 2022 General Election precinct results.

Source: Mercer PA PRECINCT.pdf (Electionware "Summary Results Report",
4 columns: TOTAL / Election Day / Absentee / Provisional), 90 precincts
x 2 pages. Parsed from the pdftotext -layout extract with the shared
text engine in ``electionware_txt`` (same family as the county's 2023
general parser; the 2022 report only carries 7 distinct contests).

Contest set (2022 federal general):
  UNITED STATES SENATOR                                   -> U.S. Senate
  GOVERNOR and LIEUTENANT GOVERNOR                        -> Governor
  REPRESENTATIVE IN CONGRESS DISTRICT 16                  -> U.S. House 16
  SENATOR IN THE GENERAL ASSEMBLY DISTRICT 50             -> State Senate 50
  REPRESENTATIVE IN THE GENERAL ASSEMBLY DISTRICT 7       -> State House 7
  REPRESENTATIVE IN THE GENERAL ASSEMBLY DISTRICT 17      -> State House 17
  City of Hermitage/Borough of Wheatland Question         -> Yes/No question

Mercer-2022 quirks:
  - The report prints the party prefix only on the minor-party rows
    (LIB/GRN/KEY) and on the Governor/State House contests' prefixed
    rows; the eight major-party rows (FETTERMAN, OZ, PASTORE, KELLY,
    CZECH, BROOKS, MCGONIGLE, BONNER) have NO prefix. Their parties are
    filled from the ENR-derived county totals file
    (2022/20221108__pa__general__county.csv) via an explicit map.
  - "KEY" (Keystone Party) rows are handled via ``extra_parties``.
  - State House headers duplicate the district number
    ("... DISTRICT 7 DISTRICT 7"); deduped.
  - "Write-In Totals" rows are renamed to the 2022 convention
    "Write Ins" with party="".

Usage:
    python parsers/pa_mercer_general_2022_results_parser.py \
        <input.pdf|input.txt> <output.csv>
"""

import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from electionware_txt import TxtConfig, parse_input, run_cli  # noqa: E402

EXTRA_JUNK_2022 = (r"^\s*2022\s+Federal\s*$",)


def normalize(line: str):
    s = " ".join(line.split())
    upper = s.upper()
    if upper == "UNITED STATES SENATOR":
        return ("U.S. Senate", "")
    if upper == "GOVERNOR AND LIEUTENANT GOVERNOR":
        return ("Governor", "")
    if upper.startswith("REPRESENTATIVE IN CONGRESS"):
        return ("U.S. House", s.rsplit(" ", 1)[-1])
    if upper.startswith("SENATOR IN THE GENERAL ASSEMBLY"):
        return ("State Senate", s.rsplit(" ", 1)[-1])
    if upper.startswith("REPRESENTATIVE IN THE GENERAL ASSEMBLY"):
        # "... DISTRICT 7 DISTRICT 7" -> first number only.
        district = s.rsplit(" ", 1)[-1]
        return ("State House", district)
    if upper.startswith("CITY OF HERMITAGE"):
        return ("City of Hermitage/Borough of Wheatland Question", "")
    raise ValueError(f"Unrecognized office header: {line!r}")


# Major-party candidates printed without a party prefix in the source;
# parties taken from the ENR-derived county file (unambiguous 2022 race).
UNPREFIXED_PARTY = {
    "JOHN FETTERMAN": "DEM",
    "MEHMET OZ": "REP",
    "DAN PASTORE": "DEM",
    "MIKE KELLY": "REP",
    "RIANNA CZECH": "DEM",
    "MICHELE BROOKS": "REP",
    "TIMOTHY M MCGONIGLE": "DEM",
    "TIM BONNER": "REP",
}

# Ballot-name cleanups matching the 2022 convention (title case, e.g.
# 2022/counties/20221108__pa__general__cumberland__precinct.csv).
NAME_MAP = {
    "JOHN FETTERMAN": "John Fetterman",
    "MEHMET OZ": "Mehmet Oz",
    "JOSH SHAPIRO": "Josh Shapiro",
    "DOUGLAS V MASTRIANO": "Douglas V. Mastriano",
    "MATT HACKENBURG": "Matt Hackenburg",
    "ERIK GERHARDT": "Erik Gerhardt",
    "CHRISTINA DIGIULIO": "Christina DiGiulio",
    "JOSEPH P SOLOSKI": "Joseph P Soloski",
    "RICHARD L WEISS": "Richard L. Weiss",
    "DANIEL WASSMER": "Daniel Wassmer",
    "DAN PASTORE": "Dan Pastore",
    "MIKE KELLY": "Mike Kelly",
    "RIANNA CZECH": "Rianna Czech",
    "MICHELE BROOKS": "Michele Brooks",
    "TIMOTHY M MCGONIGLE": "Timothy M McGonigle",
    "TIM BONNER": "Tim Bonner",
    "PARKE WENTLING": "Parke Wentling",
}

CONFIG = TxtConfig(
    county="Mercer",
    normalize_office=normalize,
    prettify_precinct=lambda s: s,
    party_optional=True,  # eight major-party rows print no prefix
    extra_parties=("KEY",),
    extra_junk=EXTRA_JUNK_2022,
)


def main(argv):
    if len(argv) != 3:
        sys.exit(f"Usage: {Path(argv[0]).name} <input.pdf|input.txt> <output.csv>")
    rows, precinct_count, warnings, unnamed = parse_input(argv[1], CONFIG)
    for row in rows:
        key = row["candidate"].upper()
        if not row["party"] and key in UNPREFIXED_PARTY:
            row["party"] = UNPREFIXED_PARTY[key]
        row["candidate"] = NAME_MAP.get(key, row["candidate"])
        if row["candidate"] == "Write-ins":
            row["candidate"] = "Write Ins"
    # 2022-convention column names: the Absentee column goes to early_voting.
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