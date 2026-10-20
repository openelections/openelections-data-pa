#!/usr/bin/env python3
"""
Parse Mifflin County PA 2022 (May 17) Primary Election precinct results.

Source: Mifflin PA 2022 Summary Official Result Precinct.pdf
(Electionware "Summary Results Report", Municipal Primary, generated
07/06/2022, 26 precincts x ~17 pages). Same family as the county's
2022-general parser, which sits on the shared text engine in
``electionware_txt``; parsed from the pdftotext -layout extract
(``parse_input`` accepts the PDF directly).

Primary-format differences from the general report (handled here):
  - The contest HEADER carries the ballot party ("DEM US SENATOR",
    "REP GOVERNOR", ...). Candidate rows print NO party prefix, so the
    wrapper's normalize_office encodes the party into the district
    slot ("13|DEM") and the post-processing strips it back out into
    the ``party`` column for every row of the contest, including the
    Write-ins / Overvotes / Undervotes aggregate rows (2022-primary
    convention: write-in rows carry the contest party, or the
    duplicate_entries test fails).
  - Contests: US SENATOR -> U.S. Senate; GOVERNOR -> Governor;
    LT. GOVERNOR -> Lieutenant Governor; REPRESENTATIVE IN CONGRESS
    13TH CONG. DIST. -> U.S. House 13; SENATOR IN THE GENERAL
    ASSEMBLY 30TH SEN. DIST. -> State Senate 30; REP. IN THE GENERAL
    ASSEMBLY 85TH / 171ST LEG. DIST. -> State House 85/171;
    MEMBER OF [DEMOCRATIC|REPUBLICAN] STATE COMMITTEE -> "Member of
    ..." State Committee (office as printed, no district);
    PCT. COMM. <area> -> "Pct. Comm. <area>" (local, vote-for-N
    committee race, printed mostly as write-in totals).
  - Dem-only contests (Congress 13, State House 85 primary, Pct.
    Comm.) may have zero named candidates: only a Write-ins total row.
  - The Statistics block prints per-party Ballots Cast lines
    (Democratic/Republican/Nonpartisan) which are junk here; same for
    the per-party Registered Voters lines.
  - RV totals print 0 for every precinct (as in the county's 2022
    general report).
  - Ballots Cast rows: per the source's STATISTICS block (Total /
    Democratic / Republican / Nonpartisan / Blank).
  - Write-In Totals rows are renamed "Write Ins" (2022 convention);
    named write-in detail rows ("Write-In: Scattered") are skipped.
  - Overvotes/Undervotes rows are kept (same convention as the
    county's 2022 general file).
  - Candidate/precinct names are ALL-CAPS; a NAME_MAP sets the
    conventional casing for the statewide names, everything else
    (state committee + Pct. Comm. locals) is generic title-cased with
    Jr./Sr. suffix handling.

Note on the GOP U.S. Senate recount: the source report is the county's
official 07/06/2022 amendment (post-recount generation date). Its
countywide GOP U.S. Senate totals run slightly ABOVE the DOS ENR
anchor for Oz (1763 vs 1759), Barnette (1655 vs 1651), McCormick
(2380 vs 2379) and Bartos (482 vs 480); the other three candidates
match exactly. See the verify script for the reconciliation.

Usage:
    python parsers/pa_mifflin_primary_2022_results_parser.py \
        <input.pdf|input.txt> <output.csv>
"""

import csv
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from electionware_txt import TxtConfig, parse_input, FIELDNAMES  # noqa: E402
from electionware_precinct_np import prettify_huntingdon_precinct  # noqa: E402

EXTRA_JUNK_PRIMARY = (
    r"^\s*May\s+17,\s*2022\s*$",
    # per-party Registered Voters / Ballots Cast lines (labels + numbers)
    r"^\s*Registered Voters - (Democratic|Republican|Nonpartisan)\b",
    r"^\s*Ballots Cast - (Democratic|Republican|Nonpartisan)\b",
)

PARTY_HDR_RE = re.compile(r"^(DEM|REP)\s+(.+)$")

EXTRA_JUNK_2022 = EXTRA_JUNK_PRIMARY


def normalize(line: str):
    """Office header -> (office, district[|party-sentinel])."""
    s = " ".join(line.split())
    upper = s.upper()
    m = PARTY_HDR_RE.match(upper)
    party_m = m.group(1) if m else ""
    if party_m:
        upper = m.group(2).strip()

    def out(office, district):
        if party_m:
            return (office, f"{district}|{party_m}" if district else f"|{party_m}")
        return (office, district)

    if upper == "US SENATOR":
        return out("U.S. Senate", "")
    if upper == "GOVERNOR":
        return out("Governor", "")
    if upper == "LT. GOVERNOR":
        return out("Lieutenant Governor", "")
    if upper.startswith("REPRESENTATIVE IN CONGRESS"):
        d = re.search(r"(\d+)", upper.split("CONGRESS", 1)[1])
        return out("U.S. House", d.group(1))
    if upper.startswith("SENATOR IN THE GENERAL ASSEMBLY"):
        d = re.search(r"(\d+)", upper.split("ASSEMBLY", 1)[1])
        return out("State Senate", d.group(1))
    if upper.startswith("REPRESENTATIVE IN THE GENERAL ASSEMBLY") or upper.startswith(
        "REP. IN THE GENERAL ASSEMBLY"
    ):
        d = re.search(r"(\d+)", upper.split("ASSEMBLY", 1)[1])
        return out("State House", d.group(1))
    if upper == "MEMBER OF DEMOCRATIC STATE COMMITTEE":
        return out("Member of Democratic State Committee", "")
    if upper == "MEMBER OF REPUBLICAN STATE COMMITTEE":
        return out("Member of Republican State Committee", "")
    if upper.startswith("PCT. COMM."):
        area = " ".join(s.split()[1:]).title()
        area = re.sub(
            r"\bMc([a-z])", lambda m: "Mc" + m.group(1).upper(), area
        ).replace("'S", "'s")
        return out(area, "")
    raise ValueError(f"Unrecognized office header: {line!r}")


# Conventional (repo-wide) casing for the statewide candidates; the
# printed form is ALL-CAPS.
NAME_MAP = {
    "JOHN FETTERMAN": "John Fetterman",
    "CONOR LAMB": "Conor Lamb",
    "MALCOLM KENYATTA": "Malcolm Kenyatta",
    "ALEX KHALIL": "Alex Khalil",
    "DAVE MCCORMICK": "Dave McCormick",
    "MEHMET OZ": "Mehmet Oz",
    "KATHY BARNETTE": "Kathy Barnette",
    "CARLA SANDS": "Carla Sands",
    "JEFF BARTOS": "Jeff Bartos",
    "GEORGE BOCHETTO": "George Bochetto",
    "SEAN GALE": "Sean Gale",
    "JOSH SHAPIRO": "Josh Shapiro",
    "DOUGLAS V MASTRIANO": "Douglas V. Mastriano",
    "LOU BARLETTA": "Lou Barletta",
    "DAVE WHITE": "Dave White",
    "BILL MCSWAIN": "Bill McSwain",
    "JAKE CORMAN": "Jake Corman",
    "CHARLIE GEROW": "Charlie Gerow",
    "MELISSA HART": "Melissa Hart",
    "JOE GALE": "Joe Gale",
    "NCHE ZAMA": "Nche Zama",
    "AUSTIN DAVIS": "Austin Davis",
    "BRIAN SIMS": "Brian Sims",
    "RAY SOSA": "Ray Sosa",
    "CARRIE LEWIS DELROSSO": "Carrie Lewis DelRosso",
    "TEDDY DANIELS": "Teddy Daniels",
    "JEFF COLEMAN": "Jeff Coleman",
    "RICK SACCONE": "Rick Saccone",
    "CLARICE SCHILLINGER": "Clarice Schillinger",
    "CHRIS FRYE": "Chris Frye",
    "JAMES EARL JONES": "James Earl Jones",
    "JOHN BROWN": "John Brown",
    "RUSS DIAMOND": "Russ Diamond",
    "JOHN JOYCE": "John Joyce",
    "CAROL TAYLOR": "Carol Taylor",
    "JUDY WARD": "Judy Ward",
    "ROBERT ZEIGLER": "Robert Zeigler",
    "KERRY A. BENNINGHOFF": "Kerry A. Benninghoff",
    "DAVID H. ROWE": "David Hummer Rowe",
    "CLARE E. FLYNN": "Clare E. Flynn",
    "JIM SMITH": "Jim Smith",
    "ANN DUNMIRE": "Ann Dunmire",
}


def prettify_candidate(name: str) -> str:
    """Title-case an ALL-CAPS local name, keeping suffixes and initials."""
    upper = name.upper()
    if upper in NAME_MAP:
        return NAME_MAP[upper]
    words = name.split()
    out = []
    for w in words:
        wl = w.lower().rstrip(".")
        if wl in ("jr.", "jr", "sr.", "sr", "ii", "iii", "iv"):
            out.append(w.title().replace("Jr..", "Jr."))
        elif w.upper() == "DELROSSO" or w.upper().startswith("MC") and len(w) > 3:
            out.append(w.title().replace("Delrosso", "DelRosso"))
        else:
            out.append(w.title())
    return " ".join(out)


CONFIG = TxtConfig(
    county="Mifflin",
    normalize_office=normalize,
    prettify_precinct=prettify_huntingdon_precinct,
    party_optional=True,  # primary candidate rows print no party prefix
    extra_junk=EXTRA_JUNK_PRIMARY,
)


def main(argv):
    if len(argv) != 3:
        sys.exit(f"Usage: {Path(argv[0]).name} <input.pdf|input.txt> <output.csv>")
    rows, precinct_count, warnings, unnamed = parse_input(argv[1], CONFIG)
    for row in rows:
        # primary: the office header carries the ballot party
        district = row["district"]
        if "|" in district:
            district, party = district.split("|", 1)
            row["district"] = district
            row["party"] = party
        if row["candidate"] == "Write-ins":
            row["candidate"] = "Write Ins"
        else:
            row["candidate"] = prettify_candidate(row["candidate"])
    out_path = Path(argv[2])
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDNAMES)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row[k] for k in FIELDNAMES})
    print(
        f"Wrote {len(rows)} rows across {precinct_count} precincts to {out_path}"
    )
    if unnamed:
        print(f"Skipped {unnamed} unnamed Statistics segment(s)")
    if warnings:
        print(f"WARNING: {len(warnings)} unmatched lines:")
        for w in warnings[:40]:
            print("  " + w)
        if len(warnings) > 40:
            print("  ...")


if __name__ == "__main__":
    main(sys.argv)