#!/usr/bin/env python3
"""Warren County, PA 2022 Primary — precinct-level results parser.

Source situation (documented):
  * The county's certified report "~/code/openelections-sources-pa/2022/primary/
    Warren PA 2022 Primary Official Results Summary.pdf" (Dominion "Election
    Summary Report", OFFICIAL RESULTS, generated 5/20/2022, 32 pages) is
    COUNTYWIDE only.  It carries countywide candidate totals for the DEM and
    REP U.S. Senate / Governor / Lieutenant Governor / U.S. House 15 / State
    House 65 / State Committee contests, plus per-precinct sections ONLY for
    the 33 single-precinct Democratic/Republican Committee Member races
    (those races' turnout rows are also how the SUGAR GROVE precinct codes
    were disambiguated, see below).  No per-precinct vote rows exist for the
    main contests anywhere in the 32 pages (verified by reading the extracted
    text of every page; text copy in work2022p/warren/source_layout.txt).

  Precinct-level figures therefore come from the PA Department of State
  official certified precinct returns bulk file for the primary:
      ElectionReturns_2022_Primary_PrecinctReturns.txt
  (ASCII comma-delimited, quoted fields; election type "P"; Warren = county
  code 62 in this file -- NOT 61, which is Venango; 1,155 rows = 33 precincts
  x 35 named-candidate contest rows).  The 33 precincts match the county's
  own precinct list (33 of 33 reported on the summary PDF) and the 2022
  general precinct names already in this repo.  A copy of the downloaded
  bulk file is kept at work2022p/warren/dl/
  ElectionReturns_2022_Primary_PrecinctReturns_current.txt (byte-identical to
  the copy at work2022p/monroe/...; md5 a5e74514888d1ccef186863cf43f3595).

Consequences (all inherent to the DOS bulk source, not parsing losses):
  * No election-day / mail / provisional breakdown -> 7-column output header.
  * No per-precinct write-in rows (DOS bulk file carries none): the
    countywide write-in totals printed on the Dominion summary (e.g. DEM
    U.S. House 15 write-in-only 285) cannot be assigned to precincts, so per
    the no-invention rule no Write Ins rows are emitted.
  * No per-precinct Registered Voters / Ballots Cast.  County summary PDF:
    Registered Voters 26,429; Ballots Cast 8,056 (DEM 2,434 / REP 5,622).
  * No State Committee (committee-member) and no county committee races: the
    DOS bulk file carries only the 8 ballot contests (USS/GOV/LTG/USC/STH,
    both parties where applicable).

Republican U.S. Senate recount gap (the one real caveat):
  The DOS bulk file carries 231 Warren "USS"/"REP" rows (33 precincts x 7
  candidates) but EVERY vote field in them is zero; the same happens for
  Luzerne (county code 40) and no other county -- i.e. DOS blanked the
  Republican U.S. Senate contest for exactly the two counties that reported
  it after the June 2022 statewide recount was still open.  The current file
  on pa.gov (re-downloaded 2026-10, byte-identical) still has them zero, and
  DOS publishes no per-precinct recount dataset.  Warren's county summary
  PDF (5/20/2022, pre-recount) gives the contest only countywide:
  Oz 1,435 / McCormick 1,887 / Barnette 1,473 / Bartos 331 / Sands 301 /
  Gale 56 / Bochetto 40, write-ins 14; the post-recount ENR county anchor
  moves exactly Oz 1,435->1,436 and McCormick 1,887->1,888 (all other
  candidates unchanged).  With no per-precinct (pre- or post-recount) source
  for this contest, the GOP U.S. Senate race is OMITTED from this precinct
  file per the no-invention rule, and the omission is documented here.

  Every contest the DOS file does carry sums EXACTLY to the county summary
  PDF's printed totals and to the ENR county anchor (checked by --verify and
  by work2022p/warren/verify_warren.py for the ENR side).

Precinct code -> name mapping: DOS names are trimmed/normalized to the
strings already used in 2022/counties/20221108__pa__general__warren__precinct.csv.
The two SUGAR GROVE codes (255/270) carry the same DOS name; they are split
using the Dominion summary's single-precinct committee-race turnout rows:
Democratic ballots (committee-race Times Cast) are 68 in SUGAR GROVE TWP vs
32 in SUGAR GROVE BORO, and the DOS file's own Democratic votes match TWP at
code 255 (GOV DEM 63, USS DEM 63) vs BORO at code 270 (GOV DEM 27, USS DEM
30); 2022-general registered voters confirm the TWP is the larger precinct
(1,002 vs 398).  Hence 255 = SUGAR GROVE TWP and 270 = SUGAR GROVE BORO.

Usage:
    uv run python parsers/pa_warren_primary_2022_results_parser.py <dos_prcres_txt> <out.csv>
    uv run python parsers/pa_warren_primary_2022_results_parser.py --verify <dos_prcres_txt> <out.csv>

If <dos_prcres_txt> is omitted the parser looks for the copy in
work2022p/warren/dl/ relative to the repo root.
"""
from __future__ import annotations

import csv
import os
import sys

COUNTY_NAME = "Warren"
COUNTY_CODE = "62"
ELECTION_TYPE = "P"  # DOS bulk-file election type for the primary

HEADER = ["county", "precinct", "office", "district", "party", "candidate", "votes"]
OFFICE_ORDER = ["U.S. Senate", "Governor", "Lieutenant Governor", "U.S. House", "State House"]

OFFICES = {  # DOS office code -> office
    "USS": "U.S. Senate",
    "GOV": "Governor",
    "LTG": "Lieutenant Governor",
    "USC": "U.S. House",
    "STH": "State House",
}

# DOS candidate (Last, First, Middle) -> printed name.  The names match the
# county summary PDF's candidate blocks (and the Cameron 2022-primary file,
# which shares the statewide races).
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
    ("RAPP", "KATHY", "L"): "Kathy L Rapp",
}

# DOS precinct code -> clean precinct name (same strings as the 2022 general
# Warren precinct file; see module docstring for the SUGAR GROVE split).
PRECINCT_NAMES = {
    "10": "BEAR LAKE BORO",
    "20": "BROKENSTRAW TWP",
    "30": "CHERRY GROVE TWP",
    "40": "CLARENDON BORO",
    "50": "COLUMBUS TWP",
    "60": "CONEWANGO #1 TWP",
    "70": "CONEWANGO #2 TWP",
    "80": "DEERFIELD TWP",
    "90": "ELDRED TWP",
    "100": "ELK TWP",
    "110": "FARMINGTON TWP",
    "125": "FREEHOLD TWP",
    "140": "GLADE TWP",
    "150": "LIMESTONE TWP",
    "165": "MEAD TWP",
    "180": "PINE GROVE TWP",
    "195": "PITTSFIELD TWP",
    "210": "PLEASANT TWP",
    "220": "SHEFFIELD TWP",
    "230": "SOUTHWEST TWP",
    "240": "SPRING CREEK TWP",
    "255": "SUGAR GROVE TWP",
    "270": "SUGAR GROVE BORO",
    "280": "TIDIOUTE BORO",
    "290": "TRIUMPH TWP",
    "300": "WARREN CENTRAL",
    "310": "WARREN EAST",
    "320": "WARREN NORTH",
    "330": "WARREN SOUTH",
    "340": "WARREN SOUTHEAST",
    "350": "WARREN WEST",
    "360": "WATSON TWP",
    "370": "YOUNGSVILLE BORO",
}

DEFAULT_SOURCE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "work2022p", "warren", "dl",
    "ElectionReturns_2022_Primary_PrecinctReturns_current.txt",
)

# County-level totals printed in the certified county summary PDF (pp. 2/17-19
# of 32).  Used by --verify.  The GOP U.S. Senate contest is listed here only
# as documentation of the recount gap; it is NOT expected in the output
# (the DOS bulk file's Warren USS/REP rows are all zero) -- see
# RECOUNT_OMITTED below.
PDF_TOTALS = {
    ("U.S. Senate", "DEM"): {
        "Alex Khalil": 67,
        "Conor Lamb": 334,
        "Malcolm Kenyatta": 54,
        "John Fetterman": 1896,
    },
    ("Governor", "DEM"): {
        "Josh Shapiro": 2233,
    },
    ("Governor", "REP"): {
        "Lou Barletta": 394,
        "Douglas V Mastriano": 2729,
        "Nche Zama": 61,
        "Dave White": 339,
        "Melissa Hart": 377,
        "Bill McSwain": 1355,
        "Charlie Gerow": 56,
        "Joe Gale": 37,
        "Jake Corman": 153,
    },
    ("Lieutenant Governor", "DEM"): {
        "Austin Davis": 1606,
        "Ray Sosa": 189,
        "Brian Sims": 428,
    },
    ("Lieutenant Governor", "REP"): {
        "Clarice Schillinger": 249,
        "James Earl Jones": 381,
        "Rick Saccone": 699,
        "John Brown": 145,
        "Chris Frye": 238,
        "Jeff Coleman": 414,
        "Russ Diamond": 238,
        "Carrie Lewis Delrosso": 2155,
        "Teddy Daniels": 716,
    },
    ("U.S. House", "REP"): {"Glenn GT Thompson": 4755},
    ("State House", "REP"): {"Kathy L Rapp": 5087},
}

# Contest deliberately omitted from the output (documented above).
RECOUNT_OMITTED = ("U.S. Senate", "REP")


def county_rows_from_dos(path: str) -> list[dict]:
    """Read the PA DOS prcres comma-delimited structure, keep Warren (county
    code 62), primary election type."""
    out = []
    with open(path, newline="", encoding="latin-1") as fh:
        for row in csv.reader(fh):
            if len(row) < 24 or row[:2] != ["2022", ELECTION_TYPE]:
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
    office = OFFICES[r["office_code"]]
    if office in ("Governor", "Lieutenant Governor", "U.S. Senate"):
        return ""
    if office == "U.S. House":
        return str(int(r["uscd"]))
    return str(int(r["shd"]))  # State House


def to_output_rows(rows: list[dict]) -> list[list]:
    out = []
    for r in rows:
        office = OFFICES[r["office_code"]]
        if (office, r["party"]) == RECOUNT_OMITTED:
            continue  # documented: DOS bulk file zeroes this contest for Warren
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
    out.sort(key=lambda x: (x["_code"], x["_oc"], x["candidate"]))
    return [
        [o["county"], o["precinct"], o["office"], o["district"], o["party"], o["candidate"], str(o["votes"])]
        for o in out
    ]


def run_checks(rows: list[dict]) -> tuple[int, list[str]]:
    """Sum precinct votes per (office, party, candidate) and compare with the
    county summary PDF's printed countywide TOTALS.  Returns (n_checked,
    problems).  Expects the GOP U.S. Senate contest to be absent (documented
    recount gap), and separately reports the DOS zero-row sums for it."""
    sums: dict[tuple, int] = {}
    uss_rep_dos = 0
    for r in rows:
        office = OFFICES[r["office_code"]]
        if (office, r["party"]) == RECOUNT_OMITTED:
            uss_rep_dos += r["votes"]
            continue
        key = (office, r["party"], cand_name(r))
        sums[key] = sums.get(key, 0) + r["votes"]
    problems = []
    n = 0
    for (office, party), cands in PDF_TOTALS.items():
        if (office, party) == RECOUNT_OMITTED:
            continue
        for cand, total in cands.items():
            n += 1
            mine = sums.get((office, party, cand), 0)
            if mine != total:
                problems.append(f"{office} [{party}]: {cand}: precinct sum {mine} != PDF total {total}")
    if uss_rep_dos:
        problems.append(f"U.S. Senate [REP]: DOS bulk rows are not all zero (sum {uss_rep_dos}); "
                        "re-examine the documented omission")
    return n, problems


def main(argv: list[str]) -> int:
    args = [a for a in argv if a != "--verify"]
    src = args[0] if args and os.path.exists(args[0]) else DEFAULT_SOURCE
    if not os.path.exists(src):
        print("No Warren 2022 primary DOS source found (tried %s)" % (args or DEFAULT_SOURCE),
              file=sys.stderr)
        return 1
    rows = county_rows_from_dos(src)
    if not rows:
        print("No Warren primary rows found in source %s" % src, file=sys.stderr)
        return 1
    missing_precincts = sorted({r["precinct_code"] for r in rows} - set(PRECINCT_NAMES))
    if missing_precincts:
        print("Unknown precinct codes: %s" % missing_precincts, file=sys.stderr)
        return 1

    out_rows = to_output_rows(rows)

    if "--verify" in argv:
        n, problems = run_checks(rows)
        print("verify: %d candidate totals checked against county summary PDF" % n)
        print("verify: GOP U.S. Senate omitted (DOS zero-row gap; PDF pre-recount countywide "
              "Oz 1,435 / McCormick 1,887; ENR post-recount Oz 1,436 / McCormick 1,888)")
        if problems:
            for p in problems:
                print("  MISMATCH " + p)
            return 1
        print("verify: all contest sums match the county summary PDF totals")

    if len(args) > 1:
        with open(args[1], "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(HEADER)
            w.writerows(out_rows)
        print("wrote %s (%d data rows, 33 precincts) from %s" % (args[1], len(out_rows), src))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))