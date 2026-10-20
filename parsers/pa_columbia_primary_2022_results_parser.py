#!/usr/bin/env python3
"""Columbia County, PA 2022 Primary — precinct-level results parser.

Source situation (documented):
  * The county's own report "Columbia PA 2022 Primary Results.pdf"
    (cvr "Election Summary Report", 13 pages, "5/17/2022 9:48:23 PM",
    42 of 42 Precincts Reporting) is COUNTYWIDE-ONLY: one aggregate
    candidate table per contest, no per-precinct sections for any race.
    Its only precinct-shaped content is the per-precinct COMMITTEE PERSON
    contests (whose names confirm the county's precinct spellings used
    below: BEAVER TWP, BRIARCREEK TWP NORTHEAST, BLOOMSBURG 3-1 WARD,
    SCOTT TWP EAST, MOUNT PLEASANT TWP, FISHINGCREEK TWP, ...).
    Its numbers are UNOFFICIAL ELECTION-NIGHT values; they differ slightly
    from the certified returns (see below), e.g. Gov-Dem Shapiro 4,072 vs
    4,085, USS-Dem Fetterman 2,850 vs 2,860, USS-Rep McCormick 4,295 vs
    4,292.  (The 2022 general Columbia source, SOVC_Nov_8, is a different
    format — Dominion crosstab with per-precinct blocks — and does not
    repeat here: the primary PDF has no such blocks.)
  * Precinct-level figures therefore come from the PA Department of State
    official certified precinct returns bulk file for the primary:
        ElectionReturns_2022_Primary_PrecinctReturns.txt
    (comma-delimited, quoted; election type "P"; Columbia = DOS county
    code 19; 1,680 rows = 42 tabulation units x 40 candidate rows).
    The DOS units match the county's precinct set one-for-one
    (municipality type 4 = Township, 5 = Town, 6 = Borough; BENTON,
    CATAWISSA, BRIAR CREEK and SCOTT appear as township/borough or
    east/west sibling units; BLOOMSBURG Ward 3 is split X 1/X 2).

Verification (--verify), all for 42 of 42 units:
  * Per-(office, party, candidate) sums of the precinct rows equal the ENR
    county anchor (2022/20220517__pa__primary__county.csv) EXACTLY for all
    40 named candidates, including the post-recount GOP U.S. Senate
    figures (Barnette 1,658 / Oz 2,289 / Bochetto 53 / Bartos 325 /
    McCormick 4,292 / Gale 206 / Sands 340).  Columbia is not on the
    documented recount-county-file list and its 5/17 county PDF is
    election-night-only, so the certified DOS figures are the recount /
    final-canvas side and are what the anchor carries; precinct values
    below are therefore certified (post-recount) throughout.
  * The 5/17 election-night PDF countywide totals differ by -13..+13
    votes per candidate (pre-final-absentee/late-counting); they are NOT
    the certified totals and are not used for verification.

Consequences (inherent to the DOS bulk source, Clarion/Cameron precedent):
  * No election-day / mail / provisional breakdown -> 7-column output
    header (county,precinct,office,district,party,candidate,votes).
  * No per-precinct write-in rows: the DOS bulk file carries no write-in
    contest rows, so no "Write Ins" rows are emitted (the countywide
    write-in figures — e.g. USS-Rep 46 / Gov-Rep 48 / Gov-Dem 157 on the
    5/17 PDF — stay county-level only, per the no-invention rule).
  * No Registered Voters / Ballots Cast rows: DOS carries no per-precinct
    RV/BC (countwide only: RV 38,487, ballots 13,838).
  * The State Committee contests (Member of Democratic State Committee /
    Member of Republican State Committee; countywide Brittain 4,093 /
    Wilmington 6,215 / Rospendowski 5,945 per the anchor) are absent from
    the DOS precinct bulk file, so no per-precinct rows either.

Usage:
    python parsers/pa_columbia_primary_2022_results_parser.py <dos_prcres_txt> <out.csv>
    python parsers/pa_columbia_primary_2022_results_parser.py --verify <dos_prcres_txt> [<out.csv>]

If <dos_prcres_txt> is omitted the parser looks for the Columbia slice at
work2022p/columbia/columbia_prcres_slice.txt relative to the repo root.
"""
from __future__ import annotations

import csv
import os
import sys

COUNTY_NAME = "Columbia"
COUNTY_CODE = "19"  # DOS bulk-file county code (official numbering, not alpha)
ELECTION_TYPE = "P"

HEADER = ["county", "precinct", "office", "district", "party", "candidate", "votes"]
OFFICE_ORDER = ["U.S. Senate", "Governor", "Lieutenant Governor", "U.S. House",
                "State House"]

OFFICES = {  # DOS office code -> output office name
    "USS": "U.S. Senate",
    "GOV": "Governor",
    "LTG": "Lieutenant Governor",
    "USC": "U.S. House",
    "STH": "State House",
}

# DOS candidate (Last, First, Middle) -> candidate name as printed on the
# county's certified report / used in the repository for this election.
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
    ("DELROSSO", "CARRIE", ""): "Carrie Lewis DelRosso",
    ("DANIELS", "THEODORE", ""): "Teddy Daniels",
    ("WALDMAN", "AMANDA", "R"): "Amanda R Waldman",
    ("MEUSER", "DANIEL", "P"): "Dan Meuser",
    ("GIANNATTASIO", "EDWARD", "R"): "Ed Giannattasio",
    ("MARTIN", "JOSEPH", "M"): "Joseph M Martin",
    ("KLINE", "AARON", "A"): "Aaron Kline",
    ("LEADBETER", "ROBERT", ""): "Robert Leadbeter",
    ("PENMAN", "JANINE", "M"): "Janine M Penman",
}

# DOS precinct code -> precinct name (municipality type 4 = Township,
# 5 = Town, 6 = Borough; names follow the county's own spellings, which the
# 2022 general file 20221108__pa__general__columbia__precinct.csv uses too).
PRECINCT_NAMES = {
    "10": "BEAVER TWP",
    "20": "BENTON TWP",
    "30": "BENTON BOROUGH",
    "55": "BERWICK 1ST WARD",
    "70": "BERWICK 2ND WARD",
    "85": "BERWICK 3RD WARD",
    "105": "BERWICK 4TH WARD",
    "120": "BLOOMSBURG 1ST WARD",
    "130": "BLOOMSBURG 2ND WARD",
    "140": "BLOOMSBURG 3-1 WARD",
    "150": "BLOOMSBURG 3-2 WARD",
    "170": "BLOOMSBURG 4TH WARD",
    "210": "BRIARCREEK TWP NORTHEAST",
    "230": "BRIARCREEK TWP WEST",
    "240": "BRIARCREEK BORO",
    "250": "CATAWISSA TWP",
    "265": "CATAWISSA BOROUGH",
    "285": "CENTRALIA BOROUGH",
    "300": "CLEVELAND TWP",
    "320": "CONYNGHAM TWP",
    "345": "FISHINGCREEK TWP",
    "360": "FRANKLIN TWP",
    "375": "GREENWOOD TWP",
    "395": "HEMLOCK TWP",
    "410": "JACKSON TWP",
    "425": "LOCUST TWP",
    "440": "MADISON TWP",
    "450": "MAIN TWP",
    "460": "MIFFLIN TWP",
    "470": "MILLVILLE BOROUGH",
    "480": "MONTOUR TWP",
    "490": "MOUNT PLEASANT TWP",
    "500": "NORTH CENTRE TWP",
    "510": "ORANGE TWP",
    "520": "ORANGEVILLE BOROUGH",
    "535": "PINE TWP",
    "550": "ROARINGCREEK TWP",
    "565": "SCOTT TWP EAST",
    "585": "SCOTT TWP WEST",
    "600": "SOUTH CENTRE TWP",
    "610": "STILLWATER BOROUGH",
    "620": "SUGARLOAF TWP",
}

N_UNITS = 42

DEFAULT_SOURCE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "work2022p", "columbia", "columbia_prcres_slice.txt",
)

# Certified countywide totals = sums of the precinct rows below (also equal
# to the ENR county anchor exactly for all 40 named candidates, so --verify
# covers check 1 and check 2 in one pass).  Write-ins and State Committee
# have no precinct source and are documented in the module docstring.
ENR_TOTALS = {
    ("U.S. Senate", "DEM"): {
        "Alex Khalil": 201,
        "Conor Lamb": 1040,
        "Malcolm Kenyatta": 164,
        "John Fetterman": 2860,
    },
    ("U.S. Senate", "REP"): {
        "Kathy Barnette": 1658,
        "Mehmet Oz": 2289,
        "George Bochetto": 53,
        "Jeff Bartos": 325,
        "Dave McCormick": 4292,
        "Sean Gale": 206,
        "Carla Sands": 340,
    },
    ("Governor", "DEM"): {"Josh Shapiro": 4085},
    ("Governor", "REP"): {
        "Lou Barletta": 5148,
        "Douglas V Mastriano": 2591,
        "Nche Zama": 52,
        "Dave White": 306,
        "Melissa Hart": 138,
        "Bill McSwain": 657,
        "Charlie Gerow": 37,
        "Joe Gale": 163,
        "Jake Corman": 69,
    },
    ("Lieutenant Governor", "DEM"): {
        "Austin Davis": 2009,
        "Ray Sosa": 655,
        "Brian Sims": 1426,
    },
    ("Lieutenant Governor", "REP"): {
        "Clarice Schillinger": 1195,
        "James Earl Jones": 880,
        "Rick Saccone": 721,
        "John Brown": 390,
        "Chris Frye": 262,
        "Jeff Coleman": 689,
        "Russ Diamond": 340,
        "Carrie Lewis DelRosso": 2602,
        "Teddy Daniels": 1271,
    },
    ("U.S. House", "DEM"): {"Amanda R Waldman": 3989},
    ("U.S. House", "REP"): {"Dan Meuser": 8573},
    ("State House", "DEM"): {"Ed Giannattasio": 3964},
    ("State House", "REP"): {
        "Joseph M Martin": 465,
        "Aaron Kline": 2171,
        "Robert Leadbeter": 4228,
        "Janine M Penman": 2235,
    },
}


def county_rows_from_dos(path: str) -> list[dict]:
    """Read the PA DOS prcres comma-delimited structure, keep Columbia
    (county code 19), primary election type."""
    out = []
    with open(path, newline="", encoding="latin-1") as fh:
        for row in csv.reader(fh):
            if len(row) < 26 or row[:2] != ["2022", ELECTION_TYPE]:
                continue
            if row[2] != COUNTY_CODE:
                continue
            if row[8] not in OFFICES:
                raise SystemExit("unexpected office code %r in %s"
                                 % (row[8], path))
            out.append(
                {
                    "precinct_code": row[3],
                    "office_code": row[8],
                    "party": row[9],
                    "cand_last": row[11],
                    "cand_first": row[12],
                    "cand_middle": row[13],
                    "votes": int(row[15]),
                }
            )
    return out


def cand_name(r: dict) -> str:
    key = (r["cand_last"], r["cand_first"], r["cand_middle"])
    name = NAME_FIX.get(key)
    if name is None:
        parts = [r["cand_first"].title(), r["cand_last"].title()]
        name = " ".join(p for p in parts if p)
    return name


def district_for(r: dict) -> str:
    office = OFFICES[r["office_code"]]
    if office == "U.S. House":
        return "9"
    if office == "State House":
        return "109"
    return ""


def to_output_rows(rows: list[dict]) -> list[list]:
    out = []
    for r in rows:
        office = OFFICES[r["office_code"]]
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
        [o["county"], o["precinct"], o["office"], o["district"], o["party"],
         o["candidate"], str(o["votes"])]
        for o in out
    ]


def run_checks(rows: list[dict]) -> tuple[int, list[str]]:
    """Sum precinct votes per (office, party, candidate) and compare with
    the certified countywide totals (ENR anchor values)."""
    sums: dict[tuple, int] = {}
    for r in rows:
        key = (OFFICES[r["office_code"]], r["party"], cand_name(r))
        sums[key] = sums.get(key, 0) + r["votes"]
    problems = []
    n = 0
    for (office, party), cands in ENR_TOTALS.items():
        for cand, total in cands.items():
            n += 1
            mine = sums.get((office, party, cand), 0)
            if mine != total:
                problems.append("%s [%s]: %s: precinct sum %d != certified "
                                "total %d" % (office, party, cand, mine, total))
    return n, problems


def main(argv: list[str]) -> int:
    args = [a for a in argv if a != "--verify"]
    src = args[0] if args and os.path.exists(args[0]) else DEFAULT_SOURCE
    if not os.path.exists(src):
        print("No Columbia 2022 primary DOS source found (tried %s)"
              % (args or DEFAULT_SOURCE), file=sys.stderr)
        return 1
    rows = county_rows_from_dos(src)
    if not rows:
        print("No Columbia primary rows found in source %s" % src,
              file=sys.stderr)
        return 1
    units = {r["precinct_code"]: None for r in rows}
    missing_precincts = sorted(set(units) - set(PRECINCT_NAMES))
    if missing_precincts:
        print("Unknown precinct codes: %s" % missing_precincts, file=sys.stderr)
        return 1
    if len(units) != N_UNITS:
        print("Expected %d tabulation units, found %d" % (N_UNITS, len(units)),
              file=sys.stderr)
        return 1

    out_rows = to_output_rows(rows)

    if "--verify" in argv:
        n, problems = run_checks(rows)
        print("verify: %d candidate totals checked against the certified "
              "countywide totals (ENR anchor)" % n)
        if problems:
            for p in problems:
                print("  MISMATCH " + p)
            return 1
        print("verify: all contest sums match the certified countywide totals")

    if len(args) > 1:
        with open(args[1], "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(HEADER)
            w.writerows(out_rows)
        print("wrote %s (%d data rows, %d precincts)" % (args[1], len(out_rows), len(units)))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))