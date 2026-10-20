#!/usr/bin/env python3
"""Clarion County, PA 2022 Primary — precinct-level results parser.

Source situation (documented):
  * The county's certified report "~/code/openelections-sources-pa/2022/primary/
    Clarion PA 2022 Primary Election Summary Report.pdf" (Dominion "Election
    Summary Report / Closed Primary", scanned, 26 pages, "UNOFFICIAL
    RESULTS ... INCLUDES ELECTION DAY, ABSENTEE, MAIL-IN, PROVISIONAL
    BALLOTS & WRITE-INS", Precincts Reported 40 of 40) is COUNTYWIDE-ONLY:
    one aggregate candidate table per contest, no per-precinct sections
    anywhere (verified on the PaddleOCR-VL extraction of all 26 pages,
    work2022p/clarion/esr_ocr.txt).  Its "Page: N of 26" header and contest
    totals match the one-page recount companion exactly otherwise.
  * "Clarion PA 2022 Primary Official Recount Results.pdf" is the single
    U.S. Senator (REP) page ("Page: 8 of 28", generated 5/31/2022) of the
    POST-recount report, carrying only countywide totals: Barnette 1,688 /
    Oz 1,571 / Bochetto 34 / Bartos 218 / McCormick 2,152 / Gale 102 /
    Sands 361 / Total 6,126 (Times Cast 6,215 / 14,621; Unresolved
    Write-In 39).  The pre-recount ESR printed Oz 1,570 / McCormick 2,151 /
    Total 6,163 (candidates 6,124 + Write-in 39): the recount moved
    Clarion +1 Oz and +1 McCormick.  The post-recount totals equal the
    ENR county anchor (2022/20220517__pa__primary__county.csv) exactly.

  Precinct-level figures therefore come from the PA Department of State
  official certified precinct returns bulk file for the primary:
      ElectionReturns_2022_Primary_PrecinctReturns.txt
  (ASCII comma-delimited, quoted fields; election type "P"; Clarion =
  county code 16; 1,435 rows = 41 tabulation units x 35 candidate rows).
  The DOS precinct list matches the county's own precinct set: Clarion
  Township X 1/2 (municipality type 4 = Township) vs Clarion Borough
  X 1/2/3 (type 6 = Borough) and Knox Township (code 180) vs Knox Borough
  (190).  It also carries a second Monroe Township unit, "MONROE School
  District 2" (code 245) — an unused split with ZERO votes in all 35 of
  its contest rows; the county's own reports count Monroe Township as a
  single precinct (ESR: "Precincts Reported: 40 of 40"; the ESR's County
  Committee listing shows a single "19.01 - Monroe Township", and the
  repo's 2020/2022-general Clarion files have one "Monroe Township"), so
  parser emits DOS code 240 under the name "Monroe Township", asserts
  code 245 is all zeros, and skips it — 40 emitted precincts.  A verbatim
  Clarion slice of the bulk file is kept at
  work2022p/clarion/clarion_prcres_slice.txt.

Precinct-row sums (this parser, --verify) equal both the ENR anchor and
the post-recount countywide totals for all 34 named candidates, including
the post-recount GOP U.S. Senate figures (Oz 1,571 / McCormick 2,152).

Consequences (all inherent to the DOS bulk source, not parsing losses):
  * No election-day / mail / provisional breakdown -> 7-column output
    header (countywide ESR/recount pages carry the Times Cast counts
    (DEM 2,220 / 6,182; REP 6,215 / 14,621 post-recount) but no per-
    precinct RV/BC, so no Registered Voters / Ballots Cast rows either;
    countywide: RV 20,789, Ballots Cast 8,434).
  * No per-precinct write-in rows (the DOS bulk file carries none): the
    write-in-only DEM U.S. House 15 (264 write-ins scattered over ~40
    named write-in candidates) and State House 63 (180 write-ins)
    contests have NO rows at all, and every other contest's aggregate
    Write-in column (Senate DEM 46, Gov DEM 76 / REP 35, Lt Gov DEM 42 /
    REP 23, Senate REP 39 post-recount) cannot be assigned to precincts.
    Per the no-invention rule no Write Ins rows are emitted.  (The DEM
    LG rep's named write-in rows are county-level detail only.)
  * The State Committee contests (DEM district 16: Stephanie White 1,979
    + 29 write-ins; REP "district 16": Nancy Banner 5,206; ESR countywide
    pages 6 / 25-26) are absent from the DOS precinct bulk file, so they
    have no per-precinct rows either (Bedford/Cameron precedent).
  * No State Senate contest appears on Clarion's 2022 primary ballot
    (State Senatorial District 21 was uncontested) and the DOS bulk file
    carries none.

Usage:
    python parsers/pa_clarion_primary_2022_results_parser.py <dos_prcres_txt> <out.csv>
    python parsers/pa_clarion_primary_2022_results_parser.py --verify <dos_prcres_txt> [<out.csv>]

If <dos_prcres_txt> is omitted the parser looks for the Clarion slice at
work2022p/clarion/clarion_prcres_slice.txt relative to the repo root.
"""
from __future__ import annotations

import csv
import os
import sys

COUNTY_NAME = "Clarion"
COUNTY_CODE = "16"
ELECTION_TYPE = "P"  # DOS bulk-file election type for the primary

HEADER = ["county", "precinct", "office", "district", "party", "candidate", "votes"]
OFFICE_ORDER = ["U.S. Senate", "Governor", "Lieutenant Governor", "U.S. House",
                "State House"]

OFFICES = {  # DOS office code -> (office, district source field)
    "USS": ("U.S. Senate", "cand_district"),
    "GOV": ("Governor", "cand_district"),
    "LTG": ("Lieutenant Governor", "cand_district"),
    "USC": ("U.S. House", "uscd"),
    "STH": ("State House", "shd"),
}

# DOS candidate (Last, First, Middle) -> printed name (matches the ESR /
# official recount companion's candidate rows for the 2022 primary).
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
    ("BROWN", "JOHN", "A"): "John A Brown",
    ("FRYE", "CHRISTOPHER", "C"): "Chris Frye",
    ("COLEMAN", "JEFFREY", "H."): "Jeff Coleman",
    ("DIAMOND", "RUSSELL", "H"): "Russ Diamond",
    ("DELROSSO", "CARRIE", ""): "Carrie Lewis DelRosso",
    ("DANIELS", "THEODORE", ""): "Teddy Daniels",
    ("THOMPSON", "GLENN", "WILLIAM"): "Glenn GT Thompson",
    ("OBERLANDER", "DONNA", "R"): "Donna Oberlander",
}

# DOS precinct code -> precinct name.  Convention follows the county's
# 2022 general file (20221108__pa__general__clarion__precinct.csv):
# plain Municipality W/D/P wording, borough/township spelled out.
PRECINCT_NAMES = {
    "10": "Ashland Township",
    "20": "Beaver Township",
    "30": "Brady Township",
    "40": "Callensburg Borough",
    # township (type 4) vs borough (type 6) Clarion
    "50": "Clarion Township First",
    "60": "Clarion Township Second",
    "70": "Clarion Borough First",
    "80": "Clarion Borough Second",
    "90": "Clarion Borough Third",
    "100": "East Brady Borough",
    "110": "Elk Township",
    "120": "Farmington Township North",
    "130": "Farmington Township Central",
    "150": "Foxburg Borough",
    "160": "Hawthorn Borough",
    "170": "Highland Township",
    "180": "Knox Township",
    "190": "Knox Borough",
    "200": "Licking Township",
    "210": "Limestone Township",
    "220": "Madison Township",
    "230": "Millcreek Township",
    "240": "Monroe Township",
    "250": "New Bethlehem Borough",
    "260": "Paint Township",
    "270": "Perry Township",
    "280": "Piney Township",
    "290": "Porter Township East",
    "300": "Porter Township West",
    "315": "Redbank Township East",
    "320": "Redbank Township West",
    "330": "Richland Township",
    "340": "Rimersburg Borough",
    "350": "Salem Township",
    "360": "Shippenville Borough",
    "370": "Sligo Borough",
    "380": "St Petersburg Borough",
    "390": "Strattanville Borough",
    "400": "Toby Township",
    "420": "Washington Township",
}

# Second Monroe Township tabulation unit in the DOS bulk file (code 245,
# "MONROE   School District 2"): an unused split — zero votes in all 35
# contest rows of all 40 precincts' worth of entries.  The county's own
# reports count Monroe Township as ONE precinct (ESR: "Precincts Reported:
# 40 of 40"; County Committee listing shows a single "19.01 - Monroe
# Township"), and the repo's 2020/2022-general Clarion files have a single
# "Monroe Township".  So the parser emits code 240 under the name
# "Monroe Township" and skips the all-zero 245 unit, asserting first that
# it really carries no votes anywhere.
ZERO_CODES = {
    "245": "Monroe Township School District 2",
}

DEFAULT_SOURCE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "work2022p", "clarion", "clarion_prcres_slice.txt",
)

# Countywide totals printed in the certified county reports (ESR pages, and
# the post-recount U.S. Senator (REP) page where the recount changed the
# value).  Used by --verify.  Every value also equals the ENR county
# anchor, except the state-office write-in rows (ESR-only detail) and the
# pre-recount values noted below.
PDF_TOTALS = {
    ("U.S. Senate", "DEM"): {
        "Alex Khalil": 67,
        "Conor Lamb": 673,
        "Malcolm Kenyatta": 41,
        "John Fetterman": 1358,
    },
    ("U.S. Senate", "REP"): {
        "Kathy Barnette": 1688,
        "Mehmet Oz": 1571,        # recount page (ESR pre-recount printed 1570)
        "George Bochetto": 34,
        "Jeff Bartos": 218,
        "Dave McCormick": 2152,   # recount page (ESR pre-recount printed 2151)
        "Sean Gale": 102,
        "Carla Sands": 361,
    },
    ("Governor", "DEM"): {"Josh Shapiro": 2004},
    ("Governor", "REP"): {
        "Lou Barletta": 473,
        "Douglas V Mastriano": 3505,
        "Nche Zama": 46,
        "Dave White": 401,
        "Melissa Hart": 316,
        "Bill McSwain": 1102,
        "Charlie Gerow": 43,
        "Joe Gale": 97,
        "Jake Corman": 133,
    },
    ("Lieutenant Governor", "DEM"): {
        "Austin Davis": 1578,
        "Ray Sosa": 156,
        "Brian Sims": 316,
    },
    ("Lieutenant Governor", "REP"): {
        "Clarice Schillinger": 207,
        "James Earl Jones": 416,
        "Rick Saccone": 905,
        "John A Brown": 134,
        "Chris Frye": 430,
        "Jeff Coleman": 322,
        "Russ Diamond": 95,
        "Carrie Lewis DelRosso": 2764,
        "Teddy Daniels": 595,
    },
    ("U.S. House", "REP"): {"Glenn GT Thompson": 5284},
    ("State House", "REP"): {"Donna Oberlander": 5738},
}


def county_rows_from_dos(path: str) -> list[dict]:
    """Read the PA DOS prcres comma-delimited structure, keep Clarion
    (county code 16), primary election type."""
    zero_votes: dict[str, list[int]] = {}
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
            if row[3] in ZERO_CODES:
                zero_votes.setdefault(row[3], []).append(int(row[15]))
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
                }
            )
    return out, zero_votes


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
    out.sort(key=lambda x: (x["_code"], x["_oc"], x["candidate"]))
    return [
        [o["county"], o["precinct"], o["office"], o["district"], o["party"],
         o["candidate"], str(o["votes"])]
        for o in out
    ]


def run_checks(rows: list[dict]) -> tuple[int, list[str]]:
    """Sum precinct votes per (office, party, candidate) and compare with
    the countywide totals printed in the ESR / post-recount pages."""
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
                problems.append("%s [%s]: %s: precinct sum %d != printed "
                                "total %d" % (office, party, cand, mine, total))
    return n, problems


def main(argv: list[str]) -> int:
    args = [a for a in argv if a != "--verify"]
    src = args[0] if args and os.path.exists(args[0]) else DEFAULT_SOURCE
    if not os.path.exists(src):
        print("No Clarion 2022 primary DOS source found (tried %s)"
              % (args or DEFAULT_SOURCE), file=sys.stderr)
        return 1
    rows, zero_votes = county_rows_from_dos(src)
    if not rows:
        print("No Clarion primary rows found in source %s" % src,
              file=sys.stderr)
        return 1
    for code in sorted(ZERO_CODES):
        votes = zero_votes.get(code, [])
        if any(v != 0 for v in votes):
            print("ZERO-CODE %s (%s) carries %d nonzero vote row(s); "
                  "refusing to skip"
                  % (code, ZERO_CODES[code],
                     sum(1 for v in votes if v != 0)), file=sys.stderr)
            return 1
        print("note: skipped unused zero-vote tabulation unit %s (%s): "
              "0 in all %d contest rows" % (code, ZERO_CODES[code], len(votes)))
    missing_precincts = sorted({r["precinct_code"] for r in rows}
                               - set(PRECINCT_NAMES))
    if missing_precincts:
        print("Unknown precinct codes: %s" % missing_precincts,
              file=sys.stderr)
        return 1

    out_rows = to_output_rows(rows)

    if "--verify" in argv:
        n, problems = run_checks(rows)
        print("verify: %d candidate totals checked against the countywide "
              "ESR / post-recount printed totals" % n)
        if problems:
            for p in problems:
                print("  MISMATCH " + p)
            return 1
        print("verify: all contest sums match the countywide printed totals")

    if len(args) > 1:
        with open(args[1], "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(HEADER)
            w.writerows(out_rows)
        print("wrote %s (%d data rows) from %s"
              % (args[1], len(out_rows), src))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))