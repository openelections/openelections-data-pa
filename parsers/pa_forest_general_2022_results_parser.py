#!/usr/bin/env python3
"""Forest County, PA 2022 General — precinct results parser.

Source: "Forest PA 2022 General Final Results Spreadsheet for November 2022
Election.pdf" — a county-compiled per-precinct spreadsheet (text-mode PDF;
extract with `pdftotext -layout`).

Layout: one contest header (ALL CAPS, alone on its line) followed by a
merged precinct-header line and candidate rows: name + 9 precinct columns
+ Totals. "SCATTERED" rows are write-ins. The source has NO vote breakdown
(no election-day/mail columns), no Registered Voters / Ballots Cast rows,
and no party labels; parties for the statewide slate are filled from the
ENR-derived county file (2022/20221108__pa__general__county.csv).

Usage: python parsers/pa_forest_general_2022_results_parser.py <pdf|txt> <out.csv>
"""
from __future__ import annotations

import csv
import re
import subprocess
import sys
import tempfile

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
    "GOVERNOR/LIEUTENANT GOVERNOR": ("Governor", ""),
    "REPRESENTATIVE IN CONGRESS 15TH DISTRICT": ("U.S. House", "15"),
    "REPRESENTATIVE IN THE GENERAL ASSEMBLY 65TH DISTRICT": ("State House", "65"),
}

# ENR-derived parties (2022/20221108__pa__general__county.csv).
PARTY = {
    "JOHN FETTERMAN": "DEM",
    "MEHMET OZ": "REP",
    "ERIK GERHARDT": "LIB",
    "RICHARD L. WEISS": "GRN",
    "DANIEL WASSMER": "KEY",
    "JOSH SHAPIRO/AUSTIN DAVIS": "DEM",
    "DOUGLAS V. MASTRIANO/CARRIE LEWIS DELROSSO": "REP",
    "MATT HACKENBURG/TIM MCMASTER": "LIB",
    "CHRISTINA DIGIULIO/MICHAEL BAGDES-CANNING": "GRN",
    "JOE SOLOSKI/NICOLE SHULTZ": "KEY",
    "MIKE MOLESEVICH": "DEM",
    "GLENN GT THOMPSON": "REP",
    "KATHY L. RAPP": "REP",
}

NAME_FIX = {
    "Glenn Gt Thompson": "Glenn 'GT' Thompson",
    "Christina Digiulio/Michael Bagdes-Canning":
        "Christina DiGiulio/Michael Bagdes-Canning",
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


def extract_text(path: str) -> str:
    if path.lower().endswith(".pdf"):
        return subprocess.run(
            ["pdftotext", "-layout", path, "-"],
            capture_output=True, text=True, check=True,
        ).stdout
    return open(path, encoding="utf-8").read()


def parse(text: str):
    lines = text.splitlines()
    rows = []
    office = district = None
    for line in lines:
        stripped = " ".join(line.split())
        if not stripped:
            continue
        key = stripped.upper()
        if key in OFFICE_MAP:
            office, district = OFFICE_MAP[key]
            continue
        if office is None:
            continue
        # merged precinct-header line
        if "Totals" in stripped and "Barnett" in stripped:
            continue
        toks = line.split()
        if len(toks) < 11:
            continue
        vals = toks[-10:-1]
        total = toks[-1]
        # candidate rows: name + 9 precinct numbers + Totals number
        if not (total.isdigit() and all(v.lstrip("-").isdigit() for v in vals)):
            continue
        name = " ".join(toks[:-10])
        if not name:
            continue
        if name.upper() == "SCATTERED":
            candidate, party = "Write Ins", ""
        else:
            candidate, party = title_case(name), PARTY.get(name.upper(), "")
        for (_, prec), v in zip(PRECS, vals):
            rows.append([COUNTY, prec, office, district, party, candidate, v])
    return rows


def main() -> None:
    inp, out = sys.argv[1], sys.argv[2]
    rows = parse(extract_text(inp))
    with open(out, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["county", "precinct", "office", "district", "party",
                    "candidate", "votes"])
        w.writerows(rows)
    print(f"{COUNTY}: wrote {len(rows)} rows -> {out}")


if __name__ == "__main__":
    main()