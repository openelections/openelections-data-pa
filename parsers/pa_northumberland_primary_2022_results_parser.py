#!/usr/bin/env python3
"""
Parse Northumberland County PA 2022 General Primary Election precinct results.

Source: "Northumberland PA 2022 Primary Official Precinct Results.pdf"
(Electionware Summary Results Report, UNOFFICIAL REPORTS dated 05/20/2022,
74 precincts; STATISTICS + per-contest tables per precinct). Same report
family as the 2022 general ("pa_northumberland_general_2022_results_parser.py"),
but the primary ballot adds partisan contest headers ("DEM Governor",
"REP United States Senator", ...) and local committee races (DEM
"Committeeperson - Male/Female <committee>", REP "County Committeeperson
<committee>").

The shared engine in ``electionware_precinct_np`` handles the precinct-block
extraction (STATISTICS boundaries) and the string helpers, but its row
parser assigns party only from row-level prefixes -- primary candidate rows
are plain ("ALEX KHALIL 8 7 1 0") and take their party from the contest
header. It also does not emit per-party Registered Voters/Ballots Cast rows
(this source reports RV/BC for Total/DEM/REP/NONPARTISAN). This parser
therefore reuses ``extract_precinct_blocks`` and implements its own row
pass.

NOTE: the report is UNOFFICIAL and pre-recount (generated 2022-05-20). The
Republican U.S. Senate contest was recounted in June 2022; certified (ENR)
totals differ from this PDF for Oz/McCormick/Barnette/Bartos/Sands. Rows are
kept as printed; the residual is documented in the CSV PR.

Usage:
    python parsers/pa_northumberland_primary_2022_results_parser.py \\
        "<input.pdf>" "<output.csv>"
"""

import re
import sys
from pathlib import Path

from electionware_precinct_np import (
    ElectionwareConfig,
    SINGLE_TAIL_RE,
    VOTE_TAIL_RE,
    extract_precinct_blocks,
    prettify_huntingdon_precinct,
)

CONFIG = ElectionwareConfig(
    county="Northumberland",
    skip_prefixes=(
        "Summary Results Report UNOFFICIAL RESULTS",
        "GENERAL PRIMARY ELECTION",
        "May 17, 2022",
        "Precinct Summary - ",
        "Election Summary - ",
        "Report generated",
        "Mail/Absent",
        "Election Mail/Absent",
        "ee",  # wrapped "Mail/Absentee" header fragment
        "TOTAL",  # wrapped column header rows
        "Day",  # wrapped "Election Day" header fragment
        "Vote For ",
        "Total Votes Cast",
        "Contest Totals",
        "Election Day Precincts Reporting",
        "Precincts Complete",
        "Precincts Partially Reported",
        "Absentee/ Early Precincts Reporting",
        "Voter Turnout - ",
        "Not Assigned",
    ),
    county_header_suffix="Northumberland",
    include_common_pleas=False,
    include_magisterial=False,
)

# ---------------------------------------------------------------------------
# Contest (office) normalization: strip the leading DEM/REP code.
# ---------------------------------------------------------------------------

CONTEST_HEADER_RE = re.compile(r"^(DEM|REP)\s+([^0-9].*)$")
STATE_HOUSE_RE = re.compile(
    r"^Representative in the General Assembly (\d+)(?:st|nd|rd|th) District$"
)
CONGRESS_RE = re.compile(
    r"^Representative in Congress (\d+)(?:st|nd|rd|th) District$"
)

EXACT_OFFICES = {
    "United States Senator": "U.S. Senate",
    "Governor": "Governor",
    "Lieutenant Governor": "Lieutenant Governor",
    "Member of Democratic State Committee": "Member of Democratic State Committee",
    "Member of Republican State Committee": "Member of Republican State Committee",
}

SUFFIX_OFFICES = (
    "Committeeperson - Male",
    "Committeeperson - Female",
    "County Committeeperson",
)

RV_PARTIES = {
    "Registered Voters - Total": "",
    "Registered Voters - DEMOCRATIC PARTY": "DEM",
    "Registered Voters - REPUBLICAN PARTY": "REP",
    "Registered Voters - NONPARTISAN": "NONPARTISAN",
}
BC_PARTIES = {
    "Ballots Cast - Total": "",
    "Ballots Cast - DEMOCRATIC PARTY": "DEM",
    "Ballots Cast - REPUBLICAN PARTY": "REP",
    "Ballots Cast - NONPARTISAN": "NONPARTISAN",
}


def normalize_contest(rest: str) -> tuple[str, str]:
    """(office, district) for the text after the DEM/REP prefix."""
    line = rest.strip()
    if line in EXACT_OFFICES:
        return EXACT_OFFICES[line], ""
    m = STATE_HOUSE_RE.match(line)
    if m:
        return "State House", m.group(1)
    m = CONGRESS_RE.match(line)
    if m:
        return "U.S. House", m.group(1)
    for prefix in SUFFIX_OFFICES:
        if line.startswith(prefix):
            tail = line[len(prefix):].strip()
            return prefix, prettify_huntingdon_precinct(tail) if tail else ""
    # Fallback: as printed (title-cased).
    return prettify_huntingdon_precinct(line), ""


# ---------------------------------------------------------------------------
# Candidate-name normalization (matches the 2022 general parser of the county).
# ---------------------------------------------------------------------------

_SUFFIXES = {"JR", "SR", "II", "III", "IV"}


def candidate_name(raw: str) -> str:
    """ALL-CAPS Electionware name -> repo title case, initials dotted."""
    words = []
    for w in raw.split():
        if w in _SUFFIXES:
            words.append(w)
        elif len(w) == 1:
            words.append(w.capitalize() + ".")
        else:
            words.append(w.capitalize())
    return " ".join(words)


# ---------------------------------------------------------------------------
# Row parsing (custom: party from contest header, per-party RV/BC rows).
# ---------------------------------------------------------------------------


def parse_rows(precinct: str, text: str, county: str):
    lines = [ln.strip() for ln in text.split("\n") if ln.strip()]

    office, district, party = "", "", ""
    rows = []

    def emit(o, d, p, cand, votes, ed, mail, prov):
        rows.append(
            {
                "county": county,
                "precinct": precinct,
                "office": o,
                "district": d,
                "party": p,
                "candidate": cand,
                "votes": votes,
                "election_day": ed,
                "mail": mail,
                "provisional": prov,
            }
        )

    def ints(vm):
        return parse_ints([vm.group(n) for n in (2, 3, 4, 5)])

    i = 0
    while i < len(lines):
        line = lines[i]
        i += 1

        if line.startswith(CONFIG.skip_prefixes):
            continue
        if line in ("STATISTICS", "Statistics"):
            continue

        m = CONTEST_HEADER_RE.match(line)
        if m:
            rest = m.group(2).strip()
            # Wrapped local-office header: the committee area (municipality)
            # sits on its own line, e.g. "Committeeperson - Male" /
            # "Zerbe Township" / "Vote For 1".
            while rest in SUFFIX_OFFICES and i < len(lines):
                if lines[i].startswith("Vote For"):
                    break
                rest = f"{rest} {lines[i]}"
                i += 1
            if i < len(lines) and lines[i].startswith("Vote For"):
                party = m.group(1)
                office, district = normalize_contest(rest)
                continue
            office = party = district = ""
            sys.stderr.write(
                f"WARNING {precinct}: contest header not followed by "
                f"'Vote For': {line!r}\n"
            )
            continue

        # STATISTICS rows. Labels and values share one line
        # ("Ballots Cast - Total 143 111 32 0"); the per-party rows
        # carry a party, Blank does not.
        if line.startswith("Registered Voters - "):
            sm = SINGLE_TAIL_RE.match(line)
            if sm and sm.group(1).strip() in RV_PARTIES:
                emit("Registered Voters", "", RV_PARTIES[sm.group(1).strip()],
                     "", int(sm.group(2).replace(",", "")), "", "", "")
            else:
                sys.stderr.write(
                    f"WARNING {precinct}: unmatched statistics: {line!r}\n"
                )
            continue
        if line.startswith("Ballots Cast - "):
            vm = VOTE_TAIL_RE.match(line)
            head = vm.group(1).strip() if vm else None
            if vm and head == "Ballots Cast - Blank":
                emit("Ballots Cast - Blank", "", "", "", *ints(vm))
            elif vm and head in BC_PARTIES:
                emit("Ballots Cast", "", BC_PARTIES[head], "", *ints(vm))
            else:
                sys.stderr.write(
                    f"WARNING {precinct}: unmatched statistics: {line!r}\n"
                )
            continue

        vm = VOTE_TAIL_RE.match(line)
        if vm is None:
            sys.stderr.write(
                f"WARNING {precinct}: unmatched line: {line!r}\n"
            )
            continue

        vals = ints(vm)
        head = vm.group(1).strip()

        if not office:
            sys.stderr.write(
                f"WARNING {precinct}: votes before contest header: {line!r}\n"
            )
            continue

        if head == "Write-In Totals":
            emit(office, district, party, "Write Ins", *vals)
            continue

        emit(office, district, party, candidate_name(head), *vals)

    return rows


def parse_ints(tokens):
    return [int(t.replace(",", "")) for t in tokens]


def main(argv=None):
    import natural_pdf as npdf

    argv = list(sys.argv) if argv is None else argv
    if len(argv) != 3:
        sys.exit(f"Usage: {Path(argv[0]).name} <input.pdf> <output.csv>")

    pdf = npdf.PDF(str(Path(argv[1])))
    rows = []
    precincts = 0
    for precinct_name, text in extract_precinct_blocks(pdf, CONFIG):
        precincts += 1
        rows.extend(parse_rows(precinct_name, text, CONFIG.county))

    import csv

    fieldnames = [
        "county", "precinct", "office", "district", "party", "candidate",
        "votes", "election_day", "mail", "provisional",
    ]
    with Path(argv[2]).open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    print(f"Wrote {len(rows)} rows across {precincts} precincts to {argv[2]}")


if __name__ == "__main__":
    main()