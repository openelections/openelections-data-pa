#!/usr/bin/env python3
"""
Parser for Schuylkill County, PA 2024 General Primary (April 23, 2024)
precinct results.

Source: "Schuylkill PA 23-April-2024-Primary-Official-Precinct-Summary.pdf"
(Electionware "Summary Results Report", JasperReports, 1810 pages,
125 precincts).  Verification anchor: the paired county-wide
"Official Election Summary" PDF for the same county.

Format notes (same Electionware family as Schuylkill's 2022 primary
report, but this vintage has a STATISTICS block on each precinct's first
page and a 4-column vote breakdown: TOTAL, Election Day, Mail-In,
Provisional):

  STATISTICS (first page of each precinct):
      Registered Voters - Total <n>            (Dem/Rep/NONPARTISAN rows: skipped,
                                                repo convention carries total RV only)
      Ballots Cast - Total    <num line above> (per-party rows: skipped)
      Ballots Cast - Blank    <num line above>
      Voter Turnout                                    (skipped)

  Contest headers carry the ballot party as a prefix:
      "DEM President of the United States" / "REP Representative in the
      General Assembly 107th District"
  and are repeated on each continuation page (page splits occur
  mid-contest), each followed by "Vote For <N>".

  Candidate rows: <name> <total> <election_day> <mail_in> <provisional>
  "Write-In Totals" row -> candidate "Write-In Totals" (with contest party).
  Detail rows "Write-In: <name>", "Not Assigned", "Overvotes",
  "Undervotes" are skipped (repo convention, cf. 2024-primary siblings).

Office mapping (district filled from the contest title where printed;
U.S. House=9 and State Senate=29 follow the county's own
pa_schuylkill_primary_2020 parser precedent - Schuylkill lies wholly in
CD-9 and SD-29 and the report prints a single contest for each):
  "(DEM|REP) President of the United States"                -> President
  "(DEM|REP) United States Senator"                         -> U.S. Senate
  "(DEM|REP) Attorney General"                              -> Attorney General
  "(DEM|REP) Auditor General"                               -> Auditor General
  "(DEM|REP) State Treasurer"                               -> State Treasurer
  "(DEM|REP) Representative in Congress"                    -> U.S. House (9)
  "(DEM|REP) Senator in the General Assembly"               -> State Senate (29)
  "(DEM|REP) Representative in the General Assembly <N>th District"
                                                            -> State House (N)
  "DEM Delegate to the Democratic National Convention"      -> as printed
  "REP Delagate to the Republican National Convention"      -> "Delegate to
                                             the Republican National Convention"
  "REP Alternate to the Republican National Convention"     -> "Alternate
                            Delegate to the Republican National Convention"

Column mapping: TOTAL -> votes, Election Day -> election_day,
Mail-In -> mail, Provisional -> provisional.

Usage:
    python parsers/pa_schuylkill_primary_2024_results_parser.py \
        "<input.pdf|input.txt>" "<output.csv>"
"""

import csv
import re
import subprocess
import sys
import tempfile
from pathlib import Path

COUNTY = "Schuylkill"

FIELDNAMES = [
    "county", "precinct", "office", "district", "party", "candidate",
    "votes", "election_day", "mail", "provisional",
]

US_HOUSE_DISTRICT = "9"
STATE_SENATE_DISTRICT = "29"

# Page / header junk.
JUNK_PREFIXES = (
    "Summary Results Report",
    "23 April 2024 General Primary",
    "May 10, 2024",
    "Schuylkill County",
    "Election Summary - ",
    "Precinct Summary - ",
    "Report generated with Electionware",
)

# Column-header fragment lines (wrapped header words).
COL_HEADER_RE = re.compile(r"^(?:TOTAL|Election|Day|Mail-In|Provisional|\s)+$")

# Turnout percentages (printed above their labels or inline).
TURNOUT_RE = re.compile(r"^(?=[^\s]*%$)[\d,.%\s]+$")

# Contest headers: party prefix + title.
OFFICE_PREFIX_RE = re.compile(r"^(DEM|REP)\s+(.+)$")

# Candidate / result lines: <name> <4 numbers>.
VOTE_TAIL_RE = re.compile(
    r"^(.+?)\s+([\d,]+)\s+([\d,]+)\s+([\d,]+)\s+([\d,]+)\s*$"
)
# Numeric-only lines (STATISTICS numbers printed above their labels).
NUMBERS_ONLY_RE = re.compile(
    r"^([\d,]+)\s+([\d,]+)\s+([\d,]+)\s+([\d,]+)\s*$"
)

VOTE_FOR_RE = re.compile(r"^Vote For\s+(\d+)")

META_CANDIDATES = {"Not Assigned", "Overvotes", "Undervotes"}

RV_RE = re.compile(r"^Registered Voters - Total\s+([\d,]+)\s*$")
BC_LABELS = {
    "Ballots Cast - Total": "Ballots Cast",
    "Ballots Cast - Blank": "Ballots Cast Blank",
}
PERPARTY_STATS_RE = re.compile(
    r"^(Registered Voters|Ballots Cast) - (Democrat|Republican|NONPARTISAN)"
    r"(?:\s+[\d,.%]+)*$"
)
BC_INLINE_RE = re.compile(
    r"^Ballots Cast - (Total|Blank)"
    r"(?:\s+([\d,]+))?(?:\s+([\d,]+))?(?:\s+([\d,]+))?(?:\s+([\d,]+))?$"
)

OFFICE_MAP = {
    "president of the united states": ("President", ""),
    "united states senator": ("U.S. Senate", ""),
    "attorney general": ("Attorney General", ""),
    "auditor general": ("Auditor General", ""),
    "state treasurer": ("State Treasurer", ""),
    "representative in congress": ("U.S. House", US_HOUSE_DISTRICT),
    "senator in the general assembly": ("State Senate", STATE_SENATE_DISTRICT),
    "delegate to the democratic national convention": (
        "Delegate to the Democratic National Convention", ""),
    "delagate to the republican national convention": (
        "Delegate to the Republican National Convention", ""),
    "alternate to the republican national convention": (
        "Alternate Delegate to the Republican National Convention", ""),
}

STATE_HOUSE_TITLE_RE = re.compile(
    r"^Representative in the General Assembly\s+(\d+)[A-Za-z]*\s+District$"
)


def normalize_office(title):
    """Return (office, district) for a contest title (party prefix removed)."""
    t = re.sub(r"\s+", " ", title).strip()
    m = STATE_HOUSE_TITLE_RE.match(t)
    if m:
        return ("State House", m.group(1))
    hit = OFFICE_MAP.get(t.lower())
    if hit:
        return hit
    return (t, "")


def pdf_to_text(pdf_path):
    with tempfile.TemporaryDirectory() as td:
        subprocess.run(
            ["pdftotext", "-layout", str(pdf_path), str(Path(td) / "out.txt")],
            check=True,
        )
        return (Path(td) / "out.txt").read_text(errors="replace")


def iter_pages(text):
    """Yield one list of stripped, junk-free lines per PDF page."""
    for page in text.split("\f"):
        lines = []
        for raw in page.split("\n"):
            ln = raw.strip()
            if not ln:
                continue
            if any(ln.startswith(p) for p in JUNK_PREFIXES):
                continue
            if COL_HEADER_RE.match(ln):
                continue
            lines.append(ln)
        if lines:
            yield lines


def parse_stream(lines_iter, out_rows):
    """
    Lines iterator -> appends row dicts to out_rows.

    Returns the list of precinct names seen (order of first appearance).
    """
    current_precinct = None
    known_precincts = []
    current_office = None
    current_district = ""
    current_party = ""
    pending_numbers = None  # STATISTICS numbers line preceding a label row

    for lines in lines_iter:
        first = lines[0]
        # Each page of this report starts with (junk headers and) the
        # precinct name.  A name is recognised if it is followed by
        # STATISTICS (defining page) or already known.
        if len(lines) >= 2 and lines[1] == "STATISTICS":
            if first not in known_precincts:
                known_precincts.append(first)
        if first in known_precincts:
            if current_precinct != first:
                current_precinct = first
                current_office = None
                current_district = ""
                current_party = ""
                pending_numbers = None
        elif current_precinct is None:
            raise RuntimeError(f"Unrecognized page header: {first!r}")
        else:
            raise RuntimeError(
                f"Page does not start with a precinct name: {first!r}"
            )

        for i, line in enumerate(lines[1:], start=1):  # line 0 = precinct name
            pending = pending_numbers
            pending_numbers = None

            # -- STATISTICS blocks ------------------------------------
            if line == "STATISTICS":
                continue
            rvm = RV_RE.match(line)
            if rvm:
                out_rows.append({
                    "county": COUNTY, "precinct": current_precinct,
                    "office": "Registered Voters", "district": "",
                    "party": "", "candidate": "",
                    "votes": int(rvm.group(1).replace(",", "")),
                    "election_day": "", "mail": "", "provisional": "",
                })
                continue
            bim = BC_INLINE_RE.match(line)
            if bim:
                extra = [g for g in bim.groups()[1:] if g is not None]
                if len(extra) == 4:
                    total, ed, mail, prov = (
                        int(v.replace(",", "")) for v in extra
                    )
                else:
                    if pending is None:
                        raise RuntimeError(
                            f"Ballots Cast label without preceding numbers: "
                            f"{current_precinct} {line}"
                        )
                    total, ed, mail, prov = pending
                office = BC_LABELS["Ballots Cast - " + bim.group(1)]
                out_rows.append({
                    "county": COUNTY, "precinct": current_precinct,
                    "office": office, "district": "",
                    "party": "", "candidate": "",
                    "votes": total, "election_day": ed, "mail": mail,
                    "provisional": prov,
                })
                continue
            if PERPARTY_STATS_RE.match(line) or line.startswith("Voter Turnout") \
                    or TURNOUT_RE.match(line):
                continue

            # -- Contest headers --------------------------------------
            om = OFFICE_PREFIX_RE.match(line)
            if om and (i + 1 >= len(lines)
                       or VOTE_FOR_RE.match(lines[i + 1])):
                office, district = normalize_office(om.group(2))
                current_office = office
                current_district = district
                current_party = om.group(1)
                continue

            if VOTE_FOR_RE.match(line):
                continue

            # -- Vote rows --------------------------------------------
            nom = NUMBERS_ONLY_RE.match(line)
            if nom:
                pending_numbers = tuple(
                    int(g.replace(",", "")) for g in nom.groups()
                )
                continue

            vm = VOTE_TAIL_RE.match(line)
            if vm:
                head = vm.group(1).strip()
                total, ed, mail, prov = (
                    int(vm.group(k).replace(",", "")) for k in (2, 3, 4, 5)
                )
                if head.startswith("Write-In:"):
                    continue
                if head in META_CANDIDATES:
                    continue
                if head.startswith(("Ballots Cast", "Voter Turnout",
                                    "Registered Voters")):
                    raise RuntimeError(
                        f"Unclassified statistics line: {current_precinct} {line!r}"
                    )
                if head == "Write-In Totals":
                    if current_office is None:
                        raise RuntimeError(
                            f"Write-In Totals outside any contest: "
                            f"{current_precinct} {line!r}"
                        )
                    out_rows.append({
                        "county": COUNTY, "precinct": current_precinct,
                        "office": current_office, "district": current_district,
                        "party": current_party, "candidate": "Write-In Totals",
                        "votes": total, "election_day": ed, "mail": mail,
                        "provisional": prov,
                    })
                    continue
                if current_office is None:
                    raise RuntimeError(
                        f"Vote row outside any contest: "
                        f"{current_precinct} {line!r}"
                    )
                out_rows.append({
                    "county": COUNTY, "precinct": current_precinct,
                    "office": current_office, "district": current_district,
                    "party": current_party, "candidate": head,
                    "votes": total, "election_day": ed, "mail": mail,
                    "provisional": prov,
                })
                continue

            raise RuntimeError(
                f"Unparsed line in precinct {current_precinct}: {line!r}"
            )

    return known_precincts


def parse(lines_iter):
    rows = []
    precincts = parse_stream(lines_iter, rows)
    return rows, precincts


def main(input_path, output_csv):
    src = Path(input_path)
    if src.suffix.lower() == ".pdf":
        text = pdf_to_text(src)
    else:
        text = src.read_text(errors="replace")

    rows, precincts = parse(iter_pages(text))

    with open(output_csv, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDNAMES)
        w.writeheader()
        w.writerows(rows)

    print(f"Wrote {len(rows)} rows for {len(precincts)} precincts to {output_csv}")
    if len(precincts) != 125:
        print(f"WARNING: expected 125 precincts, saw {len(precincts)}", file=sys.stderr)
    return rows


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage: pa_schuylkill_primary_2024_results_parser.py "
              "<input.pdf|input.txt> <output.csv>")
        sys.exit(1)
    main(sys.argv[1], sys.argv[2])
