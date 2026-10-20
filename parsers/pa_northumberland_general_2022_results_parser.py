#!/usr/bin/env python3
"""
Parse Northumberland County PA 2022 General Election precinct results.

Source: "Northumberland PA 2022 General Official Precinct Results.pdf"
(Electionware Summary Results Report, UNOFFICIAL RESULTS, 74 precincts;
STATISTICS + 5 offices per precinct: United States Senator,
Governor & Lieutenant Governor, Representative in Congress 9th District,
Representative in the General Assembly 107th/108th District).

Usage:
    python parsers/pa_northumberland_general_2022_results_parser.py \\
        "<input.pdf>" "<output.csv>"

Uses the shared natural-pdf Electionware parser in
``electionware_precinct_np`` with a minimal config (the 2022 general is a
state-office-only ballot: no locals, retentions, or questions), then
normalizes offices/candidates to repo conventions.
"""

import re

from electionware_precinct_np import (
    ElectionwareConfig,
    parse_pdf,
    write_csv,
)

CONFIG = ElectionwareConfig(
    county="Northumberland",
    skip_prefixes=(
        "Summary Results Report UNOFFICIAL RESULTS",
        "Summary Results Report OFFICIAL RESULTS",
        "2022 General Election",
        "November 8, 2022 Northumberland County",
        "Precinct Summary - ",
        "Mail/Absentee",
        "Mail/Absent",
        "ee",  # wrapped "Mail/Absentee" header fragment
        "Election Mail/Absen",  # wrapped column header row 1
        "Day tee",  # wrapped column header row 2
        "Voter Turnout - Total",
        "Vote For ",
        "Total Votes Cast",
        "Contest Totals",
        "Not Assigned",
        "Overvotes",
        "Undervotes",
    ),
    county_header_suffix="Northumberland County",
    include_common_pleas=False,
    include_magisterial=False,
)

# --------------------------------------------------------------------------
# Post-processing: repo-standard office/candidate names.
# --------------------------------------------------------------------------

_NAME_PART_RE = re.compile(
    r"^(?P<first>[A-Z])(?P<rest>[a-z']*)(?P<tail>\b.*)$"
)


def _title(name: str) -> str:
    """Title-case an ALL-CAPS candidate name, preserving Mc/Mac/van/der."""

    def fix(m: "re.Match[str]") -> str:
        first, rest, tail = m.group("first"), m.group("rest"), m.group("tail")
        return first + rest + " " + tail

    words = []
    for w in name.split():
        if w in ("JR", "SR", "II", "III", "IV"):
            words.append(w)
            continue
        if len(w) == 1:  # single middle initial -> "V." per repo convention
            words.append(w.capitalize() + ".")
            continue
        words.append(w.capitalize())
    return " ".join(words)


GOV_SUFFIX = ", Governor"


def normalize(rows):
    out = []
    for r in rows:
        office = r["office"]
        cand = r["candidate"]
        dist = r["district"]
        party = r["party"]
        if office == "Governor & Lieutenant Governor":
            office = "Governor"
            if cand.endswith(GOV_SUFFIX):
                cand = cand[: -len(GOV_SUFFIX)].strip()
        m = re.match(
            r"^Representative in Congress (\d+)(?:st|nd|rd|th) District$",
            office,
        )
        if m:
            office, dist = "U.S. House", m.group(1)
        m = re.match(
            r"^Representative in the General Assembly "
            r"(\d+)(?:st|nd|rd|th) District$",
            office,
        )
        if m:
            office, dist = "State House", m.group(1)
        if cand == "Write-ins":
            cand = "Write Ins"
        elif cand and cand.upper() == cand and not cand.replace(
            " ", ""
        ).isdigit():
            cand = _title(cand)
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


def main(argv=None):
    import sys
    from pathlib import Path

    argv = list(sys.argv) if argv is None else argv
    if len(argv) != 3:
        sys.exit(f"Usage: {Path(argv[0]).name} <input.pdf> <output.csv>")
    rows, _n = parse_pdf(Path(argv[1]), CONFIG)
    write_csv(normalize(rows), Path(argv[2]))


if __name__ == "__main__":
    main()