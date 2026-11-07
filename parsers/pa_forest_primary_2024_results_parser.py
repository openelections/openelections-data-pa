#!/usr/bin/env python3
"""Forest County, PA 2024 Primary (April 23) — precinct results parser.

Source: "Forest PA 2024 Primary Official Election Results.pdf" — the county
Board of Elections' compiled per-precinct spreadsheet (finalized April 26,
2024), printed to a copier as a 4-page image-only PDF. Text-mode extraction
yields no text, so the PDF is rendered with `pdftoppm` and read visually;
the parser consumes a transcribed text file in the same layout as the
county's 2022 parser input (contest header: party line ("DEMOCRATIC"/
"REPUBLICAN") + office title line, then candidate rows of
name + 9 precinct columns + Totals).

Layout notes:
- "SCATTERED" rows are write-ins and carry the contest's party (2024-primary
  convention). NAMED minor rows on the *opposite* party's ballot ("DONALD
  TRUMP" on the DEM President table, "JOE BIDEN" on the REP one) are write-in
  candidates the county canvass named individually; they are transcribed as
  printed, with the contest's party.
- KNOWN SOURCE ANOMALY (documented, verified against the county's paired
  Electionware summary report and the PA DOS certified precinct returns bulk
  file): The REP U.S. Senator table prints only DAVE MCCORMICK and SCATTERED;
  the ballot
  JOSEPH VODVARKA is absent and the SCATTERED row (79 countywide) equals
  Vodvarka's 72 summary-report votes + the summary's 7 write-in votes. Both
  candidate JOSEPH VODVARKA is absent from the table, and the SCATTERED row
  (79 countywide: Barnett 7, Green 4, Harmony 9, Hickory 15, Howe 1, Jenks 14,
  Kingsley 9, Tionesta 16, Borough 4) equals Vodvarka's 72 summary-report
  votes plus the summary's 7 write-in votes. Neither official source (county
  cert nor DOS bulk) reports named Vodvarka rows, so the combined row is
  transcribed as printed under the no-invention rule.
- No Registered Voters / Ballots Cast rows and no vote-type breakdowns
  appear in the source: 7-column output header.
- Cross-vintage note: the county's paired *unofficial* Electionware Summary
  Results Report (generated 04/24/2024) reads the same per-precinct values
  for every contest the two share, with most official county totals +1
  (one canvassed vote added per contest); per-precinct values are identical.

Usage: python parsers/pa_forest_primary_2024_results_parser.py <txt> <out.csv>
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
    "PRESIDENT OF THE UNITED STATES": ("President", ""),
    "UNITED STATES SENATOR": ("U.S. Senate", ""),
    "ATTORNEY GENERAL": ("Attorney General", ""),
    "AUDITOR GENERAL": ("Auditor General", ""),
    "STATE TREASURER": ("State Treasurer", ""),
    "REPRESENTATIVE IN CONGRESS 15TH DISTRICT": ("U.S. House", "15"),
    "SENATOR IN THE GENERAL ASSEMBLY 21ST DISTRICT": ("State Senate", "21"),
    "REPRESENTATIVE IN THE GENERAL ASSEMBLY 65TH DISTRICT":
        ("State House", "65"),
    "DELEGATE TO THE DEMOCRATIC NATIONAL CONVENTION 15TH DISTRICT":
        ("Delegate to the Democratic National Convention", "15"),
    "DELEGATE TO THE REPUBLICAN NATIONAL CONVENTION 15TH DISTRICT":
        ("Delegate to the Republican National Convention", "15"),
    "ALTERNATE DELEGATE TO THE REPUBLICAN NATIONAL CONVENTION 15TH DISTRICT":
        ("Alternate Delegate to the Republican National Convention", "15"),
}

NAME_FIX = {
    # source prints the pair "GT" inside the surname; the county's own later
    # files and repo precedents spell the congressman "Glenn 'GT' Thompson"
    # (2022-primary Forest parser convention)
    "Glenn Gt Thompson": "Glenn 'GT' Thompson",
    # surname styled "DeFoor"; "DePasquale" per repo precedent
    "Tim Defoor": "Tim DeFoor",
    "Eugene Depasquale": "Eugene DePasquale",
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
    name + 9 precinct numbers + Totals. Raises on any row whose precinct
    values do not sum to the printed Totals.
    """
    rows = []
    problems = []
    office = None
    district = None
    party = ""
    for raw in text.splitlines():
        stripped = " ".join(raw.split())
        if not stripped or stripped.startswith("#"):
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
        # merged precinct-header line (not expected in the transcript, but
        # tolerated for safety)
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
            problems.append(f"{office} ({district}) {party}: {raw_name} "
                            "row sum != Totals")
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
