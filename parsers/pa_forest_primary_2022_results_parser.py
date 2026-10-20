#!/usr/bin/env python3
"""Forest County, PA 2022 Primary (May 17) — precinct results parser.

Source: "Forest PA 2022 Primary Final Results May 17 22.pdf" — the county
Board of Elections' compiled per-precinct spreadsheet (May 20, 2022), printed
to a copier as a 4-page image-only PDF. Text-mode extraction yields no text,
so the PDF is rendered with `pdftoppm` and read visually; the parser consumes
a transcribed text file in the same layout as the county's 2022 general
parser input (contest header + merged precinct-header line + candidate rows
of name + 9 precinct columns + Totals).

Layout notes:
- Each contest is two lines: party ("DEMOCRATIC"/"REPUBLICAN") then the
  office title. The party line supplies the row `party` and the Write Ins
  party (2022-primary convention: write-ins carry the contest's party).
- "SCATTERED" rows are write-ins.
- No Registered Voters / Ballots Cast rows and no vote-type breakdowns
  appear in the source: 7-column output header.
- The DEM "Representative in Congress" table lists "Glenn GT Thompson" and
  "Mike Molesevich" (both 12), and the DEM "General Assembly" table lists
  "Kathy L. Rapp" (14) — cross-party write-in rows printed as named rows by
  the county; they are transcribed as printed, with the contest's party.
- The GOP U.S. Senate race predates the June recount, but Forest's printed
  county totals match the post-recount ENR anchor exactly, so no amendment
  was needed.

Usage: python parsers/pa_forest_primary_2022_results_parser.py <txt> <out.csv>
"""
from __future__ import annotations

import csv
import re
import sys

COUNTY = "Forest"

# PDF column headers -> precinct names (Forest County roster, 9 precincts).
PRECS = [
    ("Barnett", "Barnett Township"),
    ("Green", "Green Township"),
    ("Harmony", "Harmony Township"),
    ("Hickory", "Hickory Township"),
    ("Howe", "Howe Township"),
    ("Jenks", "Jenks Township"),
    ("Kingsley", "Kingsley Township"),
    ("Tionesta", "Tionesta Township"),
    ("Borough", "Tionesta Borough"),
]

OFFICE_MAP = {
    "UNITED STATES SENATOR": ("U.S. Senate", ""),
    "GOVERNOR": ("Governor", ""),
    "LIEUTENANT GOVERNOR": ("Lieutenant Governor", ""),
    "REPRESENTATIVE IN CONGRESS 15TH DISTRICT": ("U.S. House", "15"),
    "REPRESENTATIVE IN THE GENERAL ASSEMBLY 65TH DISTRICT":
        ("State House", "65"),
    "MEMBER OF THE DEMOCRATIC STATE COMMITTEE":
        ("Member of Democratic State Committee", ""),
    "MEMBER OF THE REPUBLICAN STATE COMMITTEE":
        ("Member of Republican State Committee", ""),
    "DEMOCRATIC COMMITTEE PERSON": ("Democratic Committee Person", ""),
    "REPUBLICAN COMMITTEE PERSON": ("Republican Committee Person", ""),
}

NAME_FIX = {
    "Glenn Gt Thompson": "Glenn 'GT' Thompson",
    # source prints the triple-C "MCCCHESNEY"; the county's own later files
    # spell the candidate "Scott McChesney" (2023 Township Auditor, 2025
    # 20251104__pa__general__forest__county.csv "SCOTT W. MCCHESNEY")
    "Scott McCchesney": "Scott McChesney",
}


def title_case(name: str) -> str:
    out = []
    for i, w in enumerate(name.split()):
        if re.match(r"^[IVX]+$", w.upper()):
            out.append(w.upper())
        elif i and w.lower() in {"of", "the", "and", "for"}:
            out.append(w.lower())
        else:
            out.append(w.capitalize())
    s = " ".join(out)
    s = re.sub(r"\bMc([a-z])", lambda m: "Mc" + m.group(1).upper(), s)
    s = re.sub(r"(?<=-)([a-z])", lambda m: m.group(1).upper(), s)
    s = re.sub(r"(?<=/)([a-z])", lambda m: m.group(1).upper(), s)
    return NAME_FIX.get(s, s)


def parse(text: str):
    """Parse the transcribed layout into (candidate, precinct, votes) tuples.

    Input columns are whitespace-split; each candidate row is
    name + 9 precinct numbers + Totals. Returns rows with a verified
    per-row sum (precinct values == printed Totals).
    """
    rows = []
    problems = []
    office = None
    district = None
    party = ""
    for line in text.splitlines():
        stripped = " ".join(line.split())
        if not stripped:
            continue
        key = stripped.upper()
        if key in {"DEMOCRATIC", "REPUBLICAN"}:
            party = "DEM" if key == "DEMOCRATIC" else "REP"
            office = district = None
            continue
        if key in OFFICE_MAP:
            office, district = OFFICE_MAP[key]
            continue
        if office is None:
            continue
        # merged precinct-header line
        if "Totals" in stripped and "Barnett" in stripped:
            continue
        toks = stripped.split()
        if len(toks) < 11:
            continue
        vals = toks[-10:-1]
        total = toks[-1]
        # candidate rows: name + 9 precinct numbers + Totals number
        if not (total.isdigit() and all(v.isdigit() for v in vals)):
            continue
        raw_name = " ".join(toks[:-10])
        if not raw_name:
            continue
        if sum(int(v) for v in vals) != int(total):
            problems.append(f"{office} ({district}) {party}: {raw_name} row sum != Totals")
        name = ("Write Ins" if raw_name.upper() == "SCATTERED"
                else title_case(raw_name))
        for (_, prec), v in zip(PRECS, vals):
            rows.append([COUNTY, prec, office, district, party, name, v])
    if problems:
        raise SystemExit("sum mismatches:\n  " + "\n  ".join(problems))
    return rows


def main() -> None:
    inp, out = sys.argv[1], sys.argv[2]
    rows = parse(open(inp, encoding="utf-8").read())
    with open(out, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["county", "precinct", "office", "district", "party",
                    "candidate", "votes"])
        w.writerows(rows)
    print(f"{COUNTY}: wrote {len(rows)} rows -> {out}")


if __name__ == "__main__":
    main()