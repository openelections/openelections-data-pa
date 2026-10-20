#!/usr/bin/env python3
"""Cameron County, PA 2022 General — precinct-level results parser.

Source situation (documented): the county's certified report
"~/code/openelections-sources-pa/2022/general/Cameron PA 2022 General Official
Results.pdf" is a scanned (RICOH copier) Electionware *county-level* Summary
Results Report only — 3 pages: STATISTICS, U.S. Senate, Governor, U.S. House
15, State House 67.  It contains NO precinct-level vote rows anywhere on its
3 pages (verified by rendering/reading every page image).  No precinct-level
document exists in the source repo for Cameron (checked the county's own
results archive and openelections-sources-pa issue #13).

Precinct-level figures therefore come from the PA Department of State
official certified precinct returns bulk file:
  https://www.pa.gov/content/dam/copapwp-pagov/en/dos/resources/
      voting-and-elections/bulk-data/ElectionReturns_2022_General_PrecinctReturns.txt
(ASCII comma-delimited, quoted fields; Cameron = county code 12; 130 rows =
10 precincts x 13 candidate rows).  Cameron's 10 precincts in the DOS file
match the county's own precinct list (Driftwood, Emporium E/M/W, Gibson,
Grove, Lumber, Portage, Shippen E/W) and match 2016 precinct names already
in this repo.

Consequences (all inherent to the DOS bulk source, not parsing losses):
  * No election-day / mail / provisional breakdown -> 7-column output header.
  * No per-precinct write-in rows (DOS bulk file carries none); the county
    summary PDF's county-level write-ins (U.S. Senate 3, Governor 0,
    U.S. House 15: 5, State House 67: 40) cannot be assigned to precincts,
    so per the no-invention rule no Write Ins rows are emitted.
  * No per-precinct Registered Voters / Ballots Cast (DOS bulk file has no
    turnout fields; the county summary gives only county totals, RV 3,002 /
    Ballots Cast 1,904).

The county summary PDF's TOTALS are used for verification: every contest's
precinct-level sum must equal the printed county total (see run_checks /
__main__ "--verify").

Usage:
    python parsers/pa_cameron_general_2022_results_parser.py <dos_prcres_txt> <out.csv>
    python parsers/pa_cameron_general_2022_results_parser.py --verify \
        <dos_prcres_txt> <out.csv>

If <dos_prcres_txt> is omitted the embedded copy of the Cameron rows (taken
verbatim from the 2022-11-08 extraction of the official DOS file) is used.
"""
from __future__ import annotations

import csv
import io
import os
import sys

COUNTY_NAME = "Cameron"
COUNTY_CODE = "12"

HEADER = ["county", "precinct", "office", "district", "party", "candidate", "votes"]
OFFICE_ORDER = ["U.S. Senate", "Governor", "U.S. House", "State House"]

OFFICES = {  # DOS office code -> (office, district field to use)
    "USS": ("U.S. Senate", "cand_district"),
    "GOV": ("Governor", "cand_district"),
    "USC": ("U.S. House", "uscd"),
    "STH": ("State House", "shd"),
}

# DOS candidate (Last, First, Middle) -> printed name (matches the county
# summary PDF and the names used across the 2022 general county files).
NAME_FIX = {
    ("FETTERMAN", "JOHN", "K"): "John Fetterman",
    ("OZ", "MEHMET", "C"): "Mehmet Oz",
    ("GERHARDT", "ERIK", ""): "Erik Gerhardt",
    ("WEISS", "RICHARD", "L"): "Richard L. Weiss",
    ("WASSERMER", "DANIEL", ""): "Daniel Wassmer",
    ("SHAPIRO", "JOSHUA", "D"): "Josh Shapiro / Austin Davis",
    ("MASTRIANO", "DOUGLAS", "V"): "Douglas Mastriano / Carrie Delrosso",
    ("HACKENBURG", "JONATHAN", "MATTHEW"): "Matt Hackenburg / Tim Mcmaster",
    ("DIGIULIO", "CHRISTINA", "PK"): "Christina Digiulio / Michael Bagdes-Canning",
    ("SOLOSKI", "JOSEPH", "P"): "Joe Soloski / Nicole Shultz",
    ("MOLESEVICH", "MICHAEL", "M"): "Mike Molesevich",
    ("THOMPSON", "GLENN", "WILLIAM"): "Glenn GT Thompson",  # DOS suffix JR
    ("CAUSER", "MARTIN", "T."): "Martin T. Causer",
}

# precinct code -> clean precinct name
PRECINCT_NAMES = {
    "10": "Driftwood Borough",
    "20": "Emporium East Precinct",
    "30": "Emporium Middle Precinct",
    "40": "Emporium West Precinct",
    "50": "Gibson Township",
    "60": "Grove Township",
    "70": "Lumber Township",
    "80": "Portage Township",
    "90": "Shippen East Precinct",
    "100": "Shippen West Precinct",
}

# County-level totals printed in the certified county summary PDF (verified
# by reading all three page images).  Used by --verify.
PDF_TOTALS = {
    "U.S. Senate": {
        "John Fetterman": 547,
        "Mehmet Oz": 1247,
        "Erik Gerhardt": 46,
        "Richard L. Weiss": 19,
        "Daniel Wassmer": 27,
    },
    "Governor": {
        "Josh Shapiro / Austin Davis": 639,
        "Douglas Mastriano / Carrie Delrosso": 1200,
        "Matt Hackenburg / Tim Mcmaster": 26,
        "Christina Digiulio / Michael Bagdes-Canning": 3,
        "Joe Soloski / Nicole Shultz": 22,
    },
    "U.S. House": {"Mike Molesevich": 432, "Glenn GT Thompson": 1448},
    "State House": {"Martin T. Causer": 1691},
}


def county_rows_from_dos(path: str) -> list[dict]:
    """Read the PA DOS prcres fixed structure, keep Cameron (county code 12)."""
    out = []
    with open(path, newline="", encoding="latin-1") as fh:
        for row in csv.reader(fh):
            if len(row) < 26 or row[0] != "2022" or row[1] != "G":
                continue
            if row[2] != COUNTY_CODE:
                continue
            out.append(
                {
                    "precinct_code": row[3],
                    "office_code": row[8],
                    "party": row[9],
                    "cand_last": row[11],
                    "cand_first": row[12],
                    "cand_middle": row[13],
                    "cand_suffix": row[14],
                    "votes": int(row[15]),
                    "cand_district": row[4],  # Candidate District field
                    "uscd": row[18],
                    "shd": row[20],
                    "shd_field": row[19],  # State Senatorial district (unused)
                }
            )
    return out


def embedded_county_rows() -> list[dict]:
    fh = io.StringIO(EMBEDDED_ROWS)
    out = []
    for row in csv.reader(fh):
        if not row:
            continue
        out.append(
            {
                "precinct_code": row[3],
                "office_code": row[8],
                "party": row[9],
                "cand_last": row[11],
                "cand_first": row[12],
                "cand_middle": row[13],
                "cand_suffix": row[14],
                "votes": int(row[15]),
                "cand_district": row[4],
                "uscd": row[18],
                "shd": row[20],
                "shd_field": row[19],
            }
        )
    return out


def cand_name(r: dict) -> str:
    key = (r["cand_last"], r["cand_first"], r["cand_middle"])
    name = NAME_FIX.get(key)
    if name is None:
        parts = [r["cand_first"].title(), r["cand_last"].title()]
        name = " ".join(p for p in parts if p)
        if r["cand_suffix"]:
            name += " " + r["cand_suffix"]
    return name


def district_for(r: dict) -> str:
    office, _ = OFFICES[r["office_code"]]
    if office == "Governor":
        return ""
    if office == "U.S. Senate":
        return ""
    if office == "U.S. House":
        return str(int(r["uscd"]))
    if office == "State House":
        return str(int(r["shd"]))
    return str(int(r["cand_district"]))


def to_output_rows(rows: list[dict]) -> list[list]:
    out = []
    for r in rows:
        office, _ = OFFICES[r["office_code"]]
        precinct = PRECINCT_NAMES[r["precinct_code"]]
        out.append(
            {
                "county": COUNTY_NAME,
                "precinct": precinct,
                "office": office,
                "district": district_for(r),
                "party": r["party"],
                "candidate": cand_name(r),
                "votes": r["votes"],
                "_code": int(r["precinct_code"]),
                "_oc": OFFICE_ORDER.index(office),
            }
        )
    out.sort(key=lambda x: (x["_code"], x["_oc"]))
    return [
        [
            o["county"],
            o["precinct"],
            o["office"],
            o["district"],
            o["party"],
            o["candidate"],
            str(o["votes"]),
        ]
        for o in out
    ]


def run_checks(rows: list[dict]) -> tuple[int, list[str]]:
    """Sum precinct votes per (office, candidate) and compare with the county
    summary PDF's printed TOTALS.  Returns (n_checked, problems)."""
    sums: dict[tuple, int] = {}
    for r in rows:
        office, _ = OFFICES[r["office_code"]]
        key = (office, cand_name(r))
        sums[key] = sums.get(key, 0) + r["votes"]
    problems = []
    n = 0
    for office, cands in PDF_TOTALS.items():
        for cand, total in cands.items():
            n += 1
            mine = sums.get((office, cand), 0)
            if mine != total:
                problems.append(f"{office}: {cand}: precinct sum {mine} != PDF total {total}")
    return n, problems


EMBEDDED_ROWS = """\
"2022","G",12,10,1,0,1,4,"USS","DEM",2022C0203,"FETTERMAN","JOHN","K","",2,0,0,15,25,67,6,"DRIFTWOOD","","","","",0,5,023,0010,"","",0,5,25,67
"2022","G",12,20,1,0,1,4,"USS","DEM",2022C0203,"FETTERMAN","JOHN","K","",38,0,0,15,25,67,6,"EMPORIUM X EAST","X","EAST","","",0,10,023,0020,"","",0,5,25,67
"2022","G",12,30,1,0,1,4,"USS","DEM",2022C0203,"FETTERMAN","JOHN","K","",54,0,0,15,25,67,6,"EMPORIUM X MIDDLE","X","MIDDLE","","",0,10,023,0030,"","",0,5,25,67
"2022","G",12,40,1,0,1,4,"USS","DEM",2022C0203,"FETTERMAN","JOHN","K","",127,0,0,15,25,67,6,"EMPORIUM X WEST","X","WEST","","",0,10,023,0040,"","",0,5,25,67
"2022","G",12,50,1,0,1,4,"USS","DEM",2022C0203,"FETTERMAN","JOHN","K","",17,0,0,15,25,67,4,"GIBSON","","","","",0,15,023,0050,"","",0,5,25,67
"2022","G",12,60,1,0,1,4,"USS","DEM",2022C0203,"FETTERMAN","JOHN","K","",16,0,0,15,25,67,4,"GROVE","","","","",0,20,023,0060,"","",0,5,25,67
"2022","G",12,70,1,0,1,4,"USS","DEM",2022C0203,"FETTERMAN","JOHN","K","",44,0,0,15,25,67,4,"LUMBER","","","","",0,25,023,0070,"","",0,5,25,67
"2022","G",12,80,1,0,1,4,"USS","DEM",2022C0203,"FETTERMAN","JOHN","K","",16,0,0,15,25,67,4,"PORTAGE","","","","",0,30,023,0080,"","",0,5,25,67
"2022","G",12,90,1,0,1,4,"USS","DEM",2022C0203,"FETTERMAN","JOHN","K","",111,0,0,15,25,67,4,"SHIPPEN X EAST","X","EAST","","",0,35,023,0090,"","",0,5,25,67
"2022","G",12,100,1,0,1,4,"USS","DEM",2022C0203,"FETTERMAN","JOHN","K","",122,0,0,15,25,67,4,"SHIPPEN X WEST","X","WEST","","",0,35,023,0100,"","",0,5,25,67
"2022","G",12,10,1,0,2,2,"USS","REP",2022C0147,"OZ","MEHMET","C","",19,0,0,15,25,67,6,"DRIFTWOOD","","","","",0,5,023,0010,"","",0,5,25,67
"2022","G",12,20,1,0,2,2,"USS","REP",2022C0147,"OZ","MEHMET","C","",92,0,0,15,25,67,6,"EMPORIUM X EAST","X","EAST","","",0,10,023,0020,"","",0,5,25,67
"2022","G",12,30,1,0,2,2,"USS","REP",2022C0147,"OZ","MEHMET","C","",101,0,0,15,25,67,6,"EMPORIUM X MIDDLE","X","MIDDLE","","",0,10,023,0030,"","",0,5,25,67
"2022","G",12,40,1,0,2,2,"USS","REP",2022C0147,"OZ","MEHMET","C","",209,0,0,15,25,67,6,"EMPORIUM X WEST","X","WEST","","",0,10,023,0040,"","",0,5,25,67
"2022","G",12,50,1,0,2,2,"USS","REP",2022C0147,"OZ","MEHMET","C","",64,0,0,15,25,67,4,"GIBSON","","","","",0,15,023,0050,"","",0,5,25,67
"2022","G",12,60,1,0,2,2,"USS","REP",2022C0147,"OZ","MEHMET","C","",44,0,0,15,25,67,4,"GROVE","","","","",0,20,023,0060,"","",0,5,25,67
"2022","G",12,70,1,0,2,2,"USS","REP",2022C0147,"OZ","MEHMET","C","",64,0,0,15,25,67,4,"LUMBER","","","","",0,25,023,0070,"","",0,5,25,67
"2022","G",12,80,1,0,2,2,"USS","REP",2022C0147,"OZ","MEHMET","C","",39,0,0,15,25,67,4,"PORTAGE","","","","",0,30,023,0080,"","",0,5,25,67
"2022","G",12,90,1,0,2,2,"USS","REP",2022C0147,"OZ","MEHMET","C","",227,0,0,15,25,67,4,"SHIPPEN X EAST","X","EAST","","",0,35,023,0090,"","",0,5,25,67
"2022","G",12,100,1,0,2,2,"USS","REP",2022C0147,"OZ","MEHMET","C","",388,0,0,15,25,67,4,"SHIPPEN X WEST","X","WEST","","",0,35,023,0100,"","",0,5,25,67
"2022","G",12,10,1,0,4,99,"USS","LIB",2022C1609,"GERHARDT","ERIK","","",0,0,0,15,25,67,6,"DRIFTWOOD","","","","",0,5,023,0010,"","",0,5,25,67
"2022","G",12,20,1,0,4,99,"USS","LIB",2022C1609,"GERHARDT","ERIK","","",6,0,0,15,25,67,6,"EMPORIUM X EAST","X","EAST","","",0,10,023,0020,"","",0,5,25,67
"2022","G",12,30,1,0,4,99,"USS","LIB",2022C1609,"GERHARDT","ERIK","","",5,0,0,15,25,67,6,"EMPORIUM X MIDDLE","X","MIDDLE","","",0,10,023,0030,"","",0,5,25,67
"2022","G",12,40,1,0,4,99,"USS","LIB",2022C1609,"GERHARDT","ERIK","","",3,0,0,15,25,67,6,"EMPORIUM X WEST","X","WEST","","",0,10,023,0040,"","",0,5,25,67
"2022","G",12,50,1,0,4,99,"USS","LIB",2022C1609,"GERHARDT","ERIK","","",1,0,0,15,25,67,4,"GIBSON","","","","",0,15,023,0050,"","",0,5,25,67
"2022","G",12,60,1,0,4,99,"USS","LIB",2022C1609,"GERHARDT","ERIK","","",2,0,0,15,25,67,4,"GROVE","","","","",0,20,023,0060,"","",0,5,25,67
"2022","G",12,70,1,0,4,99,"USS","LIB",2022C1609,"GERHARDT","ERIK","","",3,0,0,15,25,67,4,"LUMBER","","","","",0,25,023,0070,"","",0,5,25,67
"2022","G",12,80,1,0,4,99,"USS","LIB",2022C1609,"GERHARDT","ERIK","","",2,0,0,15,25,67,4,"PORTAGE","","","","",0,30,023,0080,"","",0,5,25,67
"2022","G",12,90,1,0,4,99,"USS","LIB",2022C1609,"GERHARDT","ERIK","","",9,0,0,15,25,67,4,"SHIPPEN X EAST","X","EAST","","",0,35,023,0090,"","",0,5,25,67
"2022","G",12,100,1,0,4,99,"USS","LIB",2022C1609,"GERHARDT","ERIK","","",15,0,0,15,25,67,4,"SHIPPEN X WEST","X","WEST","","",0,35,023,0100,"","",0,5,25,67
"2022","G",12,10,1,0,5,99,"USS","GRN",2022C1613,"WEISS","RICHARD","L","",0,0,0,15,25,67,6,"DRIFTWOOD","","","","",0,5,023,0010,"","",0,5,25,67
"2022","G",12,20,1,0,5,99,"USS","GRN",2022C1613,"WEISS","RICHARD","L","",1,0,0,15,25,67,6,"EMPORIUM X EAST","X","EAST","","",0,10,023,0020,"","",0,5,25,67
"2022","G",12,30,1,0,5,99,"USS","GRN",2022C1613,"WEISS","RICHARD","L","",0,0,0,15,25,67,6,"EMPORIUM X MIDDLE","X","MIDDLE","","",0,10,023,0030,"","",0,5,25,67
"2022","G",12,40,1,0,5,99,"USS","GRN",2022C1613,"WEISS","RICHARD","L","",7,0,0,15,25,67,6,"EMPORIUM X WEST","X","WEST","","",0,10,023,0040,"","",0,5,25,67
"2022","G",12,50,1,0,5,99,"USS","GRN",2022C1613,"WEISS","RICHARD","L","",0,0,0,15,25,67,4,"GIBSON","","","","",0,15,023,0050,"","",0,5,25,67
"2022","G",12,60,1,0,5,99,"USS","GRN",2022C1613,"WEISS","RICHARD","L","",1,0,0,15,25,67,4,"GROVE","","","","",0,20,023,0060,"","",0,5,25,67
"2022","G",12,70,1,0,5,99,"USS","GRN",2022C1613,"WEISS","RICHARD","L","",4,0,0,15,25,67,4,"LUMBER","","","","",0,25,023,0070,"","",0,5,25,67
"2022","G",12,80,1,0,5,99,"USS","GRN",2022C1613,"WEISS","RICHARD","L","",1,0,0,15,25,67,4,"PORTAGE","","","","",0,30,023,0080,"","",0,5,25,67
"2022","G",12,90,1,0,5,99,"USS","GRN",2022C1613,"WEISS","RICHARD","L","",2,0,0,15,25,67,4,"SHIPPEN X EAST","X","EAST","","",0,35,023,0090,"","",0,5,25,67
"2022","G",12,100,1,0,5,99,"USS","GRN",2022C1613,"WEISS","RICHARD","L","",3,0,0,15,25,67,4,"SHIPPEN X WEST","X","WEST","","",0,35,023,0100,"","",0,5,25,67
"2022","G",12,10,1,0,8,99,"USS","KEY",2022C1605,"WASSMER","DANIEL","","",0,0,0,15,25,67,6,"DRIFTWOOD","","","","",0,5,023,0010,"","",0,5,25,67
"2022","G",12,20,1,0,8,99,"USS","KEY",2022C1605,"WASSMER","DANIEL","","",2,0,0,15,25,67,6,"EMPORIUM X EAST","X","EAST","","",0,10,023,0020,"","",0,5,25,67
"2022","G",12,30,1,0,8,99,"USS","KEY",2022C1605,"WASSMER","DANIEL","","",2,0,0,15,25,67,6,"EMPORIUM X MIDDLE","X","MIDDLE","","",0,10,023,0030,"","",0,5,25,67
"2022","G",12,40,1,0,8,99,"USS","KEY",2022C1605,"WASSMER","DANIEL","","",13,0,0,15,25,67,6,"EMPORIUM X WEST","X","WEST","","",0,10,023,0040,"","",0,5,25,67
"2022","G",12,50,1,0,8,99,"USS","KEY",2022C1605,"WASSMER","DANIEL","","",1,0,0,15,25,67,4,"GIBSON","","","","",0,15,023,0050,"","",0,5,25,67
"2022","G",12,60,1,0,8,99,"USS","KEY",2022C1605,"WASSMER","DANIEL","","",0,0,0,15,25,67,4,"GROVE","","","","",0,20,023,0060,"","",0,5,25,67
"2022","G",12,70,1,0,8,99,"USS","KEY",2022C1605,"WASSMER","DANIEL","","",2,0,0,15,25,67,4,"LUMBER","","","","",0,25,023,0070,"","",0,5,25,67
"2022","G",12,80,1,0,8,99,"USS","KEY",2022C1605,"WASSMER","DANIEL","","",2,0,0,15,25,67,4,"PORTAGE","","","","",0,30,023,0080,"","",0,5,25,67
"2022","G",12,90,1,0,8,99,"USS","KEY",2022C1605,"WASSMER","DANIEL","","",2,0,0,15,25,67,4,"SHIPPEN X EAST","X","EAST","","",0,35,023,0090,"","",0,5,25,67
"2022","G",12,100,1,0,8,99,"USS","KEY",2022C1605,"WASSMER","DANIEL","","",3,0,0,15,25,67,4,"SHIPPEN X WEST","X","WEST","","",0,35,023,0100,"","",0,5,25,67
"2022","G",12,10,2,0,1,1,"GOV","DEM",2022C0206,"SHAPIRO","JOSHUA","D","",2,0,0,15,25,67,6,"DRIFTWOOD","","","","",0,5,023,0010,"","",0,5,25,67
"2022","G",12,20,2,0,1,1,"GOV","DEM",2022C0206,"SHAPIRO","JOSHUA","D","",47,0,0,15,25,67,6,"EMPORIUM X EAST","X","EAST","","",0,10,023,0020,"","",0,5,25,67
"2022","G",12,30,2,0,1,1,"GOV","DEM",2022C0206,"SHAPIRO","JOSHUA","D","",60,0,0,15,25,67,6,"EMPORIUM X MIDDLE","X","MIDDLE","","",0,10,023,0030,"","",0,5,25,67
"2022","G",12,40,2,0,1,1,"GOV","DEM",2022C0206,"SHAPIRO","JOSHUA","D","",155,0,0,15,25,67,6,"EMPORIUM X WEST","X","WEST","","",0,10,023,0040,"","",0,5,25,67
"2022","G",12,50,2,0,1,1,"GOV","DEM",2022C0206,"SHAPIRO","JOSHUA","D","",20,0,0,15,25,67,4,"GIBSON","","","","",0,15,023,0050,"","",0,5,25,67
"2022","G",12,60,2,0,1,1,"GOV","DEM",2022C0206,"SHAPIRO","JOSHUA","D","",15,0,0,15,25,67,4,"GROVE","","","","",0,20,023,0060,"","",0,5,25,67
"2022","G",12,70,2,0,1,1,"GOV","DEM",2022C0206,"SHAPIRO","JOSHUA","D","",50,0,0,15,25,67,4,"LUMBER","","","","",0,25,023,0070,"","",0,5,25,67
"2022","G",12,80,2,0,1,1,"GOV","DEM",2022C0206,"SHAPIRO","JOSHUA","D","",12,0,0,15,25,67,4,"PORTAGE","","","","",0,30,023,0080,"","",0,5,25,67
"2022","G",12,90,2,0,1,1,"GOV","DEM",2022C0206,"SHAPIRO","JOSHUA","D","",134,0,0,15,25,67,4,"SHIPPEN X EAST","X","EAST","","",0,35,023,0090,"","",0,5,25,67
"2022","G",12,100,2,0,1,1,"GOV","DEM",2022C0206,"SHAPIRO","JOSHUA","D","",144,0,0,15,25,67,4,"SHIPPEN X WEST","X","WEST","","",0,35,023,0100,"","",0,5,25,67
"2022","G",12,10,2,0,2,2,"GOV","REP",2022C0139,"MASTRIANO","DOUGLAS","V","",19,0,0,15,25,67,6,"DRIFTWOOD","","","","",0,5,023,0010,"","",0,5,25,67
"2022","G",12,20,2,0,2,2,"GOV","REP",2022C0139,"MASTRIANO","DOUGLAS","V","",87,0,0,15,25,67,6,"EMPORIUM X EAST","X","EAST","","",0,10,023,0020,"","",0,5,25,67
"2022","G",12,30,2,0,2,2,"GOV","REP",2022C0139,"MASTRIANO","DOUGLAS","V","",97,0,0,15,25,67,6,"EMPORIUM X MIDDLE","X","MIDDLE","","",0,10,023,0030,"","",0,5,25,67
"2022","G",12,40,2,0,2,2,"GOV","REP",2022C0139,"MASTRIANO","DOUGLAS","V","",194,0,0,15,25,67,6,"EMPORIUM X WEST","X","WEST","","",0,10,023,0040,"","",0,5,25,67
"2022","G",12,50,2,0,2,2,"GOV","REP",2022C0139,"MASTRIANO","DOUGLAS","V","",62,0,0,15,25,67,4,"GIBSON","","","","",0,15,023,0050,"","",0,5,25,67
"2022","G",12,60,2,0,2,2,"GOV","REP",2022C0139,"MASTRIANO","DOUGLAS","V","",47,0,0,15,25,67,4,"GROVE","","","","",0,20,023,0060,"","",0,5,25,67
"2022","G",12,70,2,0,2,2,"GOV","REP",2022C0139,"MASTRIANO","DOUGLAS","V","",63,0,0,15,25,67,4,"LUMBER","","","","",0,25,023,0070,"","",0,5,25,67
"2022","G",12,80,2,0,2,2,"GOV","REP",2022C0139,"MASTRIANO","DOUGLAS","V","",46,0,0,15,25,67,4,"PORTAGE","","","","",0,30,023,0080,"","",0,5,25,67
"2022","G",12,90,2,0,2,2,"GOV","REP",2022C0139,"MASTRIANO","DOUGLAS","V","",213,0,0,15,25,67,4,"SHIPPEN X EAST","X","EAST","","",0,35,023,0090,"","",0,5,25,67
"2022","G",12,100,2,0,2,2,"GOV","REP",2022C0139,"MASTRIANO","DOUGLAS","V","",372,0,0,15,25,67,4,"SHIPPEN X WEST","X","WEST","","",0,35,023,0100,"","",0,5,25,67
"2022","G",12,10,2,0,4,99,"GOV","LIB",2022C1618,"HACKENBURG","JONATHAN","MATTHEW","",0,0,0,15,25,67,6,"DRIFTWOOD","","","","",0,5,023,0010,"","",0,5,25,67
"2022","G",12,20,2,0,4,99,"GOV","LIB",2022C1618,"HACKENBURG","JONATHAN","MATTHEW","",4,0,0,15,25,67,6,"EMPORIUM X EAST","X","EAST","","",0,10,023,0020,"","",0,5,25,67
"2022","G",12,30,2,0,4,99,"GOV","LIB",2022C1618,"HACKENBURG","JONATHAN","MATTHEW","",4,0,0,15,25,67,6,"EMPORIUM X MIDDLE","X","MIDDLE","","",0,10,023,0030,"","",0,5,25,67
"2022","G",12,40,2,0,4,99,"GOV","LIB",2022C1618,"HACKENBURG","JONATHAN","MATTHEW","",4,0,0,15,25,67,6,"EMPORIUM X WEST","X","WEST","","",0,10,023,0040,"","",0,5,25,67
"2022","G",12,50,2,0,4,99,"GOV","LIB",2022C1618,"HACKENBURG","JONATHAN","MATTHEW","",0,0,0,15,25,67,4,"GIBSON","","","","",0,15,023,0050,"","",0,5,25,67
"2022","G",12,60,2,0,4,99,"GOV","LIB",2022C1618,"HACKENBURG","JONATHAN","MATTHEW","",0,0,0,15,25,67,4,"GROVE","","","","",0,20,023,0060,"","",0,5,25,67
"2022","G",12,70,2,0,4,99,"GOV","LIB",2022C1618,"HACKENBURG","JONATHAN","MATTHEW","",1,0,0,15,25,67,4,"LUMBER","","","","",0,25,023,0070,"","",0,5,25,67
"2022","G",12,80,2,0,4,99,"GOV","LIB",2022C1618,"HACKENBURG","JONATHAN","MATTHEW","",1,0,0,15,25,67,4,"PORTAGE","","","","",0,30,023,0080,"","",0,5,25,67
"2022","G",12,90,2,0,4,99,"GOV","LIB",2022C1618,"HACKENBURG","JONATHAN","MATTHEW","",3,0,0,15,25,67,4,"SHIPPEN X EAST","X","EAST","","",0,35,023,0090,"","",0,5,25,67
"2022","G",12,100,2,0,4,99,"GOV","LIB",2022C1618,"HACKENBURG","JONATHAN","MATTHEW","",9,0,0,15,25,67,4,"SHIPPEN X WEST","X","WEST","","",0,35,023,0100,"","",0,5,25,67
"2022","G",12,10,2,0,5,99,"GOV","GRN",2022C1611,"DIGIULIO","CHRISTINA","PK","",0,0,0,15,25,67,6,"DRIFTWOOD","","","","",0,5,023,0010,"","",0,5,25,67
"2022","G",12,20,2,0,5,99,"GOV","GRN",2022C1611,"DIGIULIO","CHRISTINA","PK","",0,0,0,15,25,67,6,"EMPORIUM X EAST","X","EAST","","",0,10,023,0020,"","",0,5,25,67
"2022","G",12,30,2,0,5,99,"GOV","GRN",2022C1611,"DIGIULIO","CHRISTINA","PK","",0,0,0,15,25,67,6,"EMPORIUM X MIDDLE","X","MIDDLE","","",0,10,023,0030,"","",0,5,25,67
"2022","G",12,40,2,0,5,99,"GOV","GRN",2022C1611,"DIGIULIO","CHRISTINA","PK","",1,0,0,15,25,67,6,"EMPORIUM X WEST","X","WEST","","",0,10,023,0040,"","",0,5,25,67
"2022","G",12,50,2,0,5,99,"GOV","GRN",2022C1611,"DIGIULIO","CHRISTINA","PK","",0,0,0,15,25,67,4,"GIBSON","","","","",0,15,023,0050,"","",0,5,25,67
"2022","G",12,60,2,0,5,99,"GOV","GRN",2022C1611,"DIGIULIO","CHRISTINA","PK","",0,0,0,15,25,67,4,"GROVE","","","","",0,20,023,0060,"","",0,5,25,67
"2022","G",12,70,2,0,5,99,"GOV","GRN",2022C1611,"DIGIULIO","CHRISTINA","PK","",0,0,0,15,25,67,4,"LUMBER","","","","",0,25,023,0070,"","",0,5,25,67
"2022","G",12,80,2,0,5,99,"GOV","GRN",2022C1611,"DIGIULIO","CHRISTINA","PK","",0,0,0,15,25,67,4,"PORTAGE","","","","",0,30,023,0080,"","",0,5,25,67
"2022","G",12,90,2,0,5,99,"GOV","GRN",2022C1611,"DIGIULIO","CHRISTINA","PK","",0,0,0,15,25,67,4,"SHIPPEN X EAST","X","EAST","","",0,35,023,0090,"","",0,5,25,67
"2022","G",12,100,2,0,5,99,"GOV","GRN",2022C1611,"DIGIULIO","CHRISTINA","PK","",2,0,0,15,25,67,4,"SHIPPEN X WEST","X","WEST","","",0,35,023,0100,"","",0,5,25,67
"2022","G",12,10,2,0,8,99,"GOV","KEY",2022C1606,"SOLOSKI","JOSEPH","P","",0,0,0,15,25,67,6,"DRIFTWOOD","","","","",0,5,023,0010,"","",0,5,25,67
"2022","G",12,20,2,0,8,99,"GOV","KEY",2022C1606,"SOLOSKI","JOSEPH","P","",2,0,0,15,25,67,6,"EMPORIUM X EAST","X","EAST","","",0,10,023,0020,"","",0,5,25,67
"2022","G",12,30,2,0,8,99,"GOV","KEY",2022C1606,"SOLOSKI","JOSEPH","P","",1,0,0,15,25,67,6,"EMPORIUM X MIDDLE","X","MIDDLE","","",0,10,023,0030,"","",0,5,25,67
"2022","G",12,40,2,0,8,99,"GOV","KEY",2022C1606,"SOLOSKI","JOSEPH","P","",8,0,0,15,25,67,6,"EMPORIUM X WEST","X","WEST","","",0,10,023,0040,"","",0,5,25,67
"2022","G",12,50,2,0,8,99,"GOV","KEY",2022C1606,"SOLOSKI","JOSEPH","P","",0,0,0,15,25,67,4,"GIBSON","","","","",0,15,023,0050,"","",0,5,25,67
"2022","G",12,60,2,0,8,99,"GOV","KEY",2022C1606,"SOLOSKI","JOSEPH","P","",0,0,0,15,25,67,4,"GROVE","","","","",0,20,023,0060,"","",0,5,25,67
"2022","G",12,70,2,0,8,99,"GOV","KEY",2022C1606,"SOLOSKI","JOSEPH","P","",3,0,0,15,25,67,4,"LUMBER","","","","",0,25,023,0070,"","",0,5,25,67
"2022","G",12,80,2,0,8,99,"GOV","KEY",2022C1606,"SOLOSKI","JOSEPH","P","",2,0,0,15,25,67,4,"PORTAGE","","","","",0,30,023,0080,"","",0,5,25,67
"2022","G",12,90,2,0,8,99,"GOV","KEY",2022C1606,"SOLOSKI","JOSEPH","P","",2,0,0,15,25,67,4,"SHIPPEN X EAST","X","EAST","","",0,35,023,0090,"","",0,5,25,67
"2022","G",12,100,2,0,8,99,"GOV","KEY",2022C1606,"SOLOSKI","JOSEPH","P","",4,0,0,15,25,67,4,"SHIPPEN X WEST","X","WEST","","",0,35,023,0100,"","",0,5,25,67
"2022","G",12,10,5,15,1,99,"USC","DEM",2022C1538,"MOLESEVICH","MICHAEL","M","",2,0,0,15,25,67,6,"DRIFTWOOD","","","","",0,5,023,0010,"","",0,5,25,67
"2022","G",12,20,5,15,1,99,"USC","DEM",2022C1538,"MOLESEVICH","MICHAEL","M","",35,0,0,15,25,67,6,"EMPORIUM X EAST","X","EAST","","",0,10,023,0020,"","",0,5,25,67
"2022","G",12,30,5,15,1,99,"USC","DEM",2022C1538,"MOLESEVICH","MICHAEL","M","",45,0,0,15,25,67,6,"EMPORIUM X MIDDLE","X","MIDDLE","","",0,10,023,0030,"","",0,5,25,67
"2022","G",12,40,5,15,1,99,"USC","DEM",2022C1538,"MOLESEVICH","MICHAEL","M","",110,0,0,15,25,67,6,"EMPORIUM X WEST","X","WEST","","",0,10,023,0040,"","",0,5,25,67
"2022","G",12,50,5,15,1,99,"USC","DEM",2022C1538,"MOLESEVICH","MICHAEL","M","",12,0,0,15,25,67,4,"GIBSON","","","","",0,15,023,0050,"","",0,5,25,67
"2022","G",12,60,5,15,1,99,"USC","DEM",2022C1538,"MOLESEVICH","MICHAEL","M","",9,0,0,15,25,67,4,"GROVE","","","","",0,20,023,0060,"","",0,5,25,67
"2022","G",12,70,5,15,1,99,"USC","DEM",2022C1538,"MOLESEVICH","MICHAEL","M","",33,0,0,15,25,67,4,"LUMBER","","","","",0,25,023,0070,"","",0,5,25,67
"2022","G",12,80,5,15,1,99,"USC","DEM",2022C1538,"MOLESEVICH","MICHAEL","M","",10,0,0,15,25,67,4,"PORTAGE","","","","",0,30,023,0080,"","",0,5,25,67
"2022","G",12,90,5,15,1,99,"USC","DEM",2022C1538,"MOLESEVICH","MICHAEL","M","",88,0,0,15,25,67,4,"SHIPPEN X EAST","X","EAST","","",0,35,023,0090,"","",0,5,25,67
"2022","G",12,100,5,15,1,99,"USC","DEM",2022C1538,"MOLESEVICH","MICHAEL","M","",88,0,0,15,25,67,4,"SHIPPEN X WEST","X","WEST","","",0,35,023,0100,"","",0,5,25,67
"2022","G",12,10,5,15,2,1,"USC","REP",2022C0153,"THOMPSON","GLENN","WILLIAM","JR",19,0,0,15,25,67,6,"DRIFTWOOD","","","","",0,5,023,0010,"","",0,5,25,67
"2022","G",12,20,5,15,2,1,"USC","REP",2022C0153,"THOMPSON","GLENN","WILLIAM","JR",105,0,0,15,25,67,6,"EMPORIUM X EAST","X","EAST","","",0,10,023,0020,"","",0,5,25,67
"2022","G",12,30,5,15,2,1,"USC","REP",2022C0153,"THOMPSON","GLENN","WILLIAM","JR",119,0,0,15,25,67,6,"EMPORIUM X MIDDLE","X","MIDDLE","","",0,10,023,0030,"","",0,5,25,67
"2022","G",12,40,5,15,2,1,"USC","REP",2022C0153,"THOMPSON","GLENN","WILLIAM","JR",250,0,0,15,25,67,6,"EMPORIUM X WEST","X","WEST","","",0,10,023,0040,"","",0,5,25,67
"2022","G",12,50,5,15,2,1,"USC","REP",2022C0153,"THOMPSON","GLENN","WILLIAM","JR",70,0,0,15,25,67,4,"GIBSON","","","","",0,15,023,0050,"","",0,5,25,67
"2022","G",12,60,5,15,2,1,"USC","REP",2022C0153,"THOMPSON","GLENN","WILLIAM","JR",54,0,0,15,25,67,4,"GROVE","","","","",0,20,023,0060,"","",0,5,25,67
"2022","G",12,70,5,15,2,1,"USC","REP",2022C0153,"THOMPSON","GLENN","WILLIAM","JR",82,0,0,15,25,67,4,"LUMBER","","","","",0,25,023,0070,"","",0,5,25,67
"2022","G",12,80,5,15,2,1,"USC","REP",2022C0153,"THOMPSON","GLENN","WILLIAM","JR",48,0,0,15,25,67,4,"PORTAGE","","","","",0,30,023,0080,"","",0,5,25,67
"2022","G",12,90,5,15,2,1,"USC","REP",2022C0153,"THOMPSON","GLENN","WILLIAM","JR",263,0,0,15,25,67,4,"SHIPPEN X EAST","X","EAST","","",0,35,023,0090,"","",0,5,25,67
"2022","G",12,100,5,15,2,1,"USC","REP",2022C0153,"THOMPSON","GLENN","WILLIAM","JR",438,0,0,15,25,67,4,"SHIPPEN X WEST","X","WEST","","",0,35,023,0100,"","",0,5,25,67
"2022","G",12,10,7,67,2,2,"STH","REP",2022C0725,"CAUSER","MARTIN","T.","",19,0,0,15,25,67,6,"DRIFTWOOD","","","","",0,5,023,0010,"","",0,5,25,67
"2022","G",12,20,7,67,2,2,"STH","REP",2022C0725,"CAUSER","MARTIN","T.","",126,0,0,15,25,67,6,"EMPORIUM X EAST","X","EAST","","",0,10,023,0020,"","",0,5,25,67
"2022","G",12,30,7,67,2,2,"STH","REP",2022C0725,"CAUSER","MARTIN","T.","",143,0,0,15,25,67,6,"EMPORIUM X MIDDLE","X","MIDDLE","","",0,10,023,0030,"","",0,5,25,67
"2022","G",12,40,7,67,2,2,"STH","REP",2022C0725,"CAUSER","MARTIN","T.","",317,0,0,15,25,67,6,"EMPORIUM X WEST","X","WEST","","",0,10,023,0040,"","",0,5,25,67
"2022","G",12,50,7,67,2,2,"STH","REP",2022C0725,"CAUSER","MARTIN","T.","",75,0,0,15,25,67,4,"GIBSON","","","","",0,15,023,0050,"","",0,5,25,67
"2022","G",12,60,7,67,2,2,"STH","REP",2022C0725,"CAUSER","MARTIN","T.","",58,0,0,15,25,67,4,"GROVE","","","","",0,20,023,0060,"","",0,5,25,67
"2022","G",12,70,7,67,2,2,"STH","REP",2022C0725,"CAUSER","MARTIN","T.","",98,0,0,15,25,67,4,"LUMBER","","","","",0,25,023,0070,"","",0,5,25,67
"2022","G",12,80,7,67,2,2,"STH","REP",2022C0725,"CAUSER","MARTIN","T.","",52,0,0,15,25,67,4,"PORTAGE","","","","",0,30,023,0080,"","",0,5,25,67
"2022","G",12,90,7,67,2,2,"STH","REP",2022C0725,"CAUSER","MARTIN","T.","",318,0,0,15,25,67,4,"SHIPPEN X EAST","X","EAST","","",0,35,023,0090,"","",0,5,25,67
"2022","G",12,100,7,67,2,2,"STH","REP",2022C0725,"CAUSER","MARTIN","T.","",485,0,0,15,25,67,4,"SHIPPEN X WEST","X","WEST","","",0,35,023,0100,"","",0,5,25,67
"""


def main(argv: list[str]) -> int:
    args = [a for a in argv if a != "--verify"]
    rows = []
    if args and os.path.exists(args[0]):
        rows = county_rows_from_dos(args[0])
        src = args[0]
    else:
        rows = embedded_county_rows()
        src = "embedded Cameron rows from official DOS 2022 precinct file"

    if not rows:
        print("No Cameron rows found in source", file=sys.stderr)
        return 1
    missing = sorted({r["precinct_code"] for r in rows} - set(PRECINCT_NAMES))
    if missing:
        print("Unknown precinct codes: %s" % missing, file=sys.stderr)
        return 1

    out_rows = to_output_rows(rows)

    if "--verify" in argv:
        n, problems = run_checks(rows)
        print("verify: %d candidate totals checked against county summary PDF" % n)
        if problems:
            for p in problems:
                print("  MISMATCH " + p)
            return 1
        print("verify: all contest sums match the county summary PDF TOTALS")

    if len(args) > 1:
        with open(args[1], "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(HEADER)
            w.writerows(out_rows)
        print("wrote %s (%d data rows) from %s" % (args[1], len(out_rows), src))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))