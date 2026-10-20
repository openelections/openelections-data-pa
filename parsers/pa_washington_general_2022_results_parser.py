#!/usr/bin/env python3
"""
Parse Washington County PA 2022 General Election precinct results.

Source: "Washington County Official Results Precinct Summary 2022
General.pdf" (Electionware Summary Results Report, Official Results,
1 Page 1 of 540, 180 precincts; ALL-CAPS precinct and office headers).

Contests: United States Senator, Governor and Lieutenant Governor,
Representative in Congress (CD-14 only), Senator in the General Assembly
(SD-46 only), Representative in the General Assembly - 39/40/46/48/50
District.

Usage:
    python parsers/pa_washington_general_2022_results_parser.py \\
        "<input.pdf>" "<output.csv>"

Uses the shared natural-pdf Electionware parser (electionware_precinct_np)
and normalizes offices/candidates to repo conventions, assigning the
districtless Congress/Senate headers by candidate roster.
"""

import re
import sys
from pathlib import Path

from electionware_precinct_np import (
    ElectionwareConfig,
    parse_pdf,
    write_csv,
)

CONFIG = ElectionwareConfig(
    county="Washington",
    skip_prefixes=(
        "Summary Results Report OFFICIAL RESULTS",
        "Summary Results Report UNOFFICIAL RESULTS",
        "2022 General Election",
        "November 8, 2022 Washington",
        "Precinct Summary - ",
        "Mail In",
        "Mail/Absentee",
        "Mail/Absent",
        "ee",
        "Election Mail",
        "Day tee",
        "In tee",
        "Voter Turnout - Total",
        "Vote For ",
        "Total Votes Cast",
        "Contest Totals",
        "Not Assigned",
        "Overvotes",
        "Undervotes",
    ),
    county_header_suffix="Washington",
    include_common_pleas=False,
    include_magisterial=False,
)

# --------------------------------------------------------------------------
# Post-processing.
# --------------------------------------------------------------------------

CONGRESS_DIST = "14"  # only the 14th Congressional District is on the ballot
SENATE_DIST = "46"  # only the 46th Senatorial District is on the ballot


def normalize(rows):
    out = []
    for r in rows:
        office = r["office"]
        cand = r["candidate"]
        dist = r["district"]
        party = r["party"]
        uo = office.upper().strip()
        if uo.startswith("UNITED STATES SENATOR"):
            office = "U.S. Senate"
        elif uo.startswith("GOVERNOR"):
            office = "Governor"
        elif uo.startswith("REPRESENTATIVE IN CONGRESS"):
            office, dist = "U.S. House", CONGRESS_DIST
        elif uo.startswith("SENATOR IN THE GENERAL ASSEMBLY"):
            office, dist = "State Senate", SENATE_DIST
        else:
            m = re.match(
                r"^Representative in the General Assembly - "
                r"(\d+)(?:ST|ND|RD|TH) District$",
                office,
                re.I,
            )
            if m:
                office, dist = "State House", m.group(1)
        if cand == "Write-ins":
            cand = "Write Ins"
        elif cand and cand.upper() == cand:
            # title() capitalizes after apostrophes (O'NEAL -> O'Neal) and
            # single-letter words get a trailing period handled below.
            words = []
            for w in cand.split():
                if w in ("JR", "SR", "II", "III"):
                    words.append(w)
                elif len(w) == 1:
                    words.append(w.capitalize() + ".")
                else:
                    words.append(w.title())
            cand = " ".join(words)
        out.append(
            {
                "county": r["county"],
                "precinct": r["precinct"],
                "office": office,
                "district": dist,
                "party": party,
                "candidate": cand,
                "votes": r["votes"],
                "election_day": r["election_day"],
                "mail": r["mail"],
                "provisional": r["provisional"],
            }
        )
    return out


def main():
    if len(sys.argv) != 3:
        sys.exit(f"Usage: {Path(sys.argv[0]).name} <input.pdf> <output.csv>")
    rows, _ = parse_pdf(Path(sys.argv[1]), CONFIG)
    write_csv(normalize(rows), Path(sys.argv[2]))


if __name__ == "__main__":
    main()