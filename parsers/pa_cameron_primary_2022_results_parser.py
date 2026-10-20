#!/usr/bin/env python3
"""Cameron County, PA 2022 Primary — precinct-level results parser.

Source situation (documented):
  * The county's certified report "~/code/openelections-sources-pa/2022/primary/
    Cameron PA 2022 Primary Summary.pdf" (Electionware Summary Results Report,
    OFFICIAL RESULTS, generated 05/20/2022, 27 pages) is county-level only.  It
    carries STATISTICS (RV/BC, countywide), countywide totals for the DEM and
    REP U.S. Senate / Governor / Lieutenant Governor / U.S. House 15 / State
    House 67 / State Committee contests (with per-write-in-name detail), and
    per-precinct contest sections ONLY for the party Committee Person / Committee
    Chair races.  The main state/federal contests have no precinct-level vote
    rows anywhere on its 27 pages (verified by reading the extracted text of
    every page; text copy in work2022p/cameron/summary.txt).

  Precinct-level figures therefore come from the PA Department of State
  official certified precinct returns bulk file for the primary:
      ElectionReturns_2022_Primary_PrecinctReturns.txt
  (ASCII comma-delimited, quoted fields; election type "P"; Cameron = county
  code 12; 360 rows = 10 precincts x 36 named-candidate contest rows).  The
  10 precincts match the county's own precinct list (Driftwood, Emporium
  E/M/W, Gibson, Grove, Lumber, Portage, Shippen E/W) and the 2016/2022
  general precinct names already in this repo.  A verbatim Cameron slice of
  the bulk file is kept at work2022p/cameron/cameron_prcres_slice.txt.

Consequences (all inherent to the DOS bulk source, not parsing losses):
  * No election-day / mail / provisional breakdown -> 7-column output header.
  * No per-precinct write-in rows (DOS bulk file carries none): the DEM
    U.S. House 15 (53 write-ins) and State House 67 (34 write-ins) contests
    are write-in-only and therefore have NO rows at all, and the State
    Committee contests (DEM 27 / REP 62 write-ins) have no rows.  The county
    summary's write-in detail is county-level and cannot be assigned to
    precincts, so per the no-invention rule no Write Ins rows are emitted.
  * No State Senate contest appears on Cameron's 2022 primary ballot report
    (Senate district 25 was uncontested) and the DOS bulk file carries none.
  * No per-precinct Registered Voters / Ballots Cast.  County summary PDF:
    RV total 2,970 (DEM 833 / REP 1,788 / nonpartisan 349); Ballots Cast
    1,083 (DEM 303 / REP 780 / blank 5).

The Republican U.S. Senate primary recount (Oz vs McCormick, June 2022) did
not change Cameron's certified candidate totals: the county summary's U.S.
Senate figures (Oz 240, McCormick 225, ...) equal the ENR county anchor
(2022/20220517__pa__primary__county.csv) exactly, and the precinct sums
below equal both (see run_checks / "--verify").

Usage:
    python parsers/pa_cameron_primary_2022_results_parser.py <dos_prcres_txt> <out.csv>
    python parsers/pa_cameron_primary_2022_results_parser.py --verify <dos_prcres_txt> <out.csv>

If <dos_prcres_txt> is omitted the parser looks for the Cameron slice at
work2022p/cameron/cameron_prcres_slice.txt relative to the repo root.
"""
from __future__ import annotations

import csv
import os
import sys

COUNTY_NAME = "Cameron"
COUNTY_CODE = "12"
ELECTION_TYPE = "P"  # DOS bulk-file election type for the primary

HEADER = ["county", "precinct", "office", "district", "party", "candidate", "votes"]
OFFICE_ORDER = ["U.S. Senate", "Governor", "Lieutenant Governor", "U.S. House", "State House"]

OFFICES = {  # DOS office code -> (office, district field to use)
    "USS": ("U.S. Senate", "cand_district"),
    "GOV": ("Governor", "cand_district"),
    "LTG": ("Lieutenant Governor", "cand_district"),
    "USC": ("U.S. House", "uscd"),
    "STH": ("State House", "shd"),
}

# DOS candidate (Last, First, Middle) -> printed name (matches the county
# summary PDF's candidate blocks for the 2022 primary).
NAME_FIX = {
    ("KHALIL", "ALEXANDRIA", "GLORIA"): "Alex Khalil",
    ("LAMB", "CONOR", "J"): "Conor Lamb",
    ("KENYATTA", "MALCOLM", ""): "Malcolm Kenyatta",
    ("FETTERMAN", "JOHN", "K"): "John Fetterman",
    ("BARNETTE", "KATHY", "J"): "Kathy Barnette",
    ("OZ", "MEHMET", "C"): "Mehmet Oz",
    ("BOCHETTO", "GEORGE", "A"): "George Bochetto",
    ("BARTOS", "JEFFREY", "A"): "Jeff Bartos",
    ("MCCORMICK", "DAVID", "H"): "Dave McCormick",
    ("GALE", "SEAN", "PETER"): "Sean Gale",
    ("SANDS", "CARLA", "HERD"): "Carla Sands",
    ("SHAPIRO", "JOSHUA", "D"): "Josh Shapiro",
    ("BARLETTA", "LOUIS", ""): "Lou Barletta",
    ("MASTRIANO", "DOUGLAS", "V"): "Douglas V Mastriano",
    ("ZAMA", "NCHE", ""): "Nche Zama",
    ("WHITE", "DAVID", "J"): "Dave White",
    ("HART", "MELISSA", "A"): "Melissa Hart",
    ("MCSWAIN", "WILLIAM", "M"): "Bill McSwain",
    ("GEROW", "CHARLES", "R"): "Charlie Gerow",
    ("GALE", "JOSEPH", "CHARLES"): "Joe Gale",
    ("CORMAN", "JACOB", "DOYLE"): "Jake Corman",
    ("DAVIS", "AUSTIN", ""): "Austin Davis",
    ("SOSA", "RAYMOND", "L"): "Ray Sosa",
    ("SIMS", "BRIAN", ""): "Brian Sims",
    ("SCHILLINGER", "CLARICE", "D"): "Clarice Schillinger",
    ("JONES", "JAMES", "E"): "James Earl Jones",
    ("SACCONE", "RICHARD", ""): "Rick Saccone",
    ("BROWN", "JOHN", "A"): "John Brown",
    ("FRYE", "CHRISTOPHER", "C"): "Chris Frye",
    ("COLEMAN", "JEFFREY", "H."): "Jeff Coleman",
    ("DIAMOND", "RUSSELL", "H"): "Russ Diamond",
    ("DELROSSO", "CARRIE", ""): "Carrie Lewis Delrosso",
    ("DANIELS", "THEODORE", ""): "Teddy Daniels",
    ("THOMPSON", "GLENN", "WILLIAM"): "Glenn GT Thompson",  # DOS suffix JR
    ("CAUSER", "MARTIN", "T."): "Martin T Causer",
    ("ROSSMAN", "ROBERT", "W."): "Robert W Rossman",
}

# precinct code -> clean precinct name (same list as the 2022 general parser)
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

DEFAULT_SOURCE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "work2022p", "cameron", "cameron_prcres_slice.txt",
)

# County-level totals printed in the certified county summary PDF (page NNN
# of 27).  Used by --verify; every value also equals the ENR county anchor.
PDF_TOTALS = {
    ("U.S. Senate", "DEM"): {
        "Alex Khalil": 17,
        "Conor Lamb": 39,
        "Malcolm Kenyatta": 8,
        "John Fetterman": 218,
    },
    ("U.S. Senate", "REP"): {
        "Kathy Barnette": 178,
        "Mehmet Oz": 240,
        "George Bochetto": 7,
        "Jeff Bartos": 43,
        "Dave McCormick": 225,
        "Sean Gale": 10,
        "Carla Sands": 59,
    },
    ("Governor", "DEM"): {"Josh Shapiro": 270},
    ("Governor", "REP"): {
        "Lou Barletta": 82,
        "Douglas V Mastriano": 332,
        "Nche Zama": 17,
        "Dave White": 36,
        "Melissa Hart": 25,
        "Bill McSwain": 168,
        "Charlie Gerow": 7,
        "Joe Gale": 14,
        "Jake Corman": 87,
    },
    ("Lieutenant Governor", "DEM"): {
        "Austin Davis": 166,
        "Ray Sosa": 46,
        "Brian Sims": 54,
    },
    ("Lieutenant Governor", "REP"): {
        "Clarice Schillinger": 36,
        "James Earl Jones": 45,
        "Rick Saccone": 99,
        "John Brown": 27,
        "Chris Frye": 17,
        "Jeff Coleman": 75,
        "Russ Diamond": 19,
        "Carrie Lewis Delrosso": 305,
        "Teddy Daniels": 87,
    },
    ("U.S. House", "REP"): {"Glenn GT Thompson": 744},
    ("State House", "REP"): {"Robert W Rossman": 113, "Martin T Causer": 658},
}


def county_rows_from_dos(path: str) -> list[dict]:
    """Read the PA DOS prcres comma-delimited structure, keep Cameron (county code 12),
    primary election type."""
    out = []
    with open(path, newline="", encoding="latin-1") as fh:
        for row in csv.reader(fh):
            if len(row) < 26 or row[:2] != ["2022", ELECTION_TYPE]:
                continue
            if row[2] != COUNTY_CODE:
                continue
            if row[8] not in OFFICES:
                raise SystemExit("unexpected office code %r in %s" % (row[8], path))
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
    if office in ("Governor", "Lieutenant Governor", "U.S. Senate"):
        return ""
    if office == "U.S. House":
        return str(int(r["uscd"]))
    return str(int(r["shd"]))  # State House


def to_output_rows(rows: list[dict]) -> list[list]:
    out = []
    for r in rows:
        office, _ = OFFICES[r["office_code"]]
        out.append(
            {
                "county": COUNTY_NAME,
                "precinct": PRECINCT_NAMES[r["precinct_code"]],
                "office": office,
                "district": district_for(r),
                "party": r["party"],
                "candidate": cand_name(r),
                "votes": r["votes"],
                "_code": int(r["precinct_code"]),
                "_oc": OFFICE_ORDER.index(office),
            }
        )
    out.sort(key=lambda x: (x["_code"], x["_oc"], OFFICE_ORDER.index(x["office"]), x["candidate"]))
    return [
        [o["county"], o["precinct"], o["office"], o["district"], o["party"], o["candidate"], str(o["votes"])]
        for o in out
    ]


def run_checks(rows: list[dict]) -> tuple[int, list[str]]:
    """Sum precinct votes per (office, party, candidate) and compare with the
    county summary PDF's printed TOTALS.  Returns (n_checked, problems)."""
    sums: dict[tuple, int] = {}
    for r in rows:
        office, _ = OFFICES[r["office_code"]]
        key = (office, r["party"], cand_name(r))
        sums[key] = sums.get(key, 0) + r["votes"]
    problems = []
    n = 0
    for (office, party), cands in PDF_TOTALS.items():
        for cand, total in cands.items():
            n += 1
            mine = sums.get((office, party, cand), 0)
            if mine != total:
                problems.append(f"{office} [{party}]: {cand}: precinct sum {mine} != PDF total {total}")
    return n, problems


def main(argv: list[str]) -> int:
    args = [a for a in argv if a != "--verify"]
    src = args[0] if args and os.path.exists(args[0]) else DEFAULT_SOURCE
    if not os.path.exists(src):
        print("No Cameron 2022 primary DOS source found (tried %s)" % (args or DEFAULT_SOURCE),
              file=sys.stderr)
        return 1
    rows = county_rows_from_dos(src)
    if not rows:
        print("No Cameron primary rows found in source %s" % src, file=sys.stderr)
        return 1
    missing_precincts = sorted({r["precinct_code"] for r in rows} - set(PRECINCT_NAMES))
    if missing_precincts:
        print("Unknown precinct codes: %s" % missing_precincts, file=sys.stderr)
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