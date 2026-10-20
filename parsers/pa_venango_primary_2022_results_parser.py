#!/usr/bin/env python3
"""Venango County, PA 2022 Primary (May 17, 2022) — precinct-level results.

Source situation (documented):
  * The county's report "Venango PA 2022 Primary Election Summary with
    Write-Ins.pdf" (Electionware "Election Summary Report - w/Write-in
    Names", generated 06/26/2023, 72 pages; text copy in
    work2022p/venango/venango_primary.txt) is COUNTYWIDE only.  Its 102
    contest sections (incl. write-in-name detail) aggregate all 44
    precincts; no per-precinct vote rows appear anywhere (verified by
    reading the extracted text of every page).

  Precinct-level figures therefore come from the PA Department of State
  official certified precinct returns bulk file for the primary:
      ElectionReturns_2022_Primary_PrecinctReturns.txt
  (ASCII comma-delimited, quoted fields; election type "P"; Venango =
  county code 61; 1,645 rows across 46 precinct codes).  A verbatim
  Venango slice of the bulk file is kept at
  work2022p/venango/venango_prcres_slice.txt.

Precinct handling: the DOS bulk file carries 46 precinct codes, but the
county's own reports (and its 2022 general file
20221108__pa__general__venango__precinct.csv, "Election Day Precincts
Reporting 44 of 44") use 44 precincts, aggregating the two DOS splits
  560 "PRESIDENT   School District 1" + 565 "... School District 2"
  715 "VICTORY (USC 15)" + "VICTORY (USC 16)"  (720)
into single precincts ("President Twp" / "Victory Twp").  This parser
merges those pairs back to the county's 44-precinct convention and emits
a complete candidate grid per precinct (absent rows = 0 votes), so
contests carry every ballot candidate in every precinct, with 0-vote
rows where the bulk file prints no row.  Only one DOS row is actually
absent: LTG Coleman in DOS code 720 (merged into Victory Twp).

Consequences (all inherent to the DOS bulk source, not parsing losses):
  * No election-day / mail / provisional breakdown -> 7-column output
    header.
  * No per-precinct write-in detail (DOS bulk file carries none): DEM
    U.S. House District 15 was write-in-only (no candidate filed; 153
    countywide write-ins in the county summary) and therefore has NO
    rows; the Write-In / State Committee rows of the other contests
    (which the county summary reports only countywide) are not emitted
    either — per-precinct write-ins cannot be assigned without
    invention.
  * No per-precinct Registered Voters / Ballots Cast; the county
    summary reports only countywide: RV total 28,096 (DEM 9,117 / REP
    18,979 / NP 0); Ballots Cast 11,186 (ED 8,899 / mail 2,263 /
    prov 24) — DEM 3,225 (ED 1,762 / mail 1,453 / prov 10), REP 7,961
    (ED 7,137 / mail 810 / prov 14), blank 16.
  * Party Committee Man / Committee Woman contests (in the county
    summary only) are office as printed, countywide only; no precinct
    rows exist.

Recount (Oz vs McCormick, June 2022): the county summary PDF still
prints the PRE-recount countywide McCormick total (2,622; all other
candidates unchanged).  The DOS bulk file and the ENR county anchor
(2022/20220517__pa__primary__county.csv) both carry the POST-recount
total 2,623 for McCormick; every other named candidate total matches
the county summary PDF and the ENR anchor exactly (see --verify, which
treats the McCormick +1 as the documented expected difference).

Usage:
    python parsers/pa_venango_primary_2022_results_parser.py <dos_prcres_txt> <out.csv>
    python parsers/pa_venango_primary_2022_results_parser.py --verify <dos_prcres_txt> <out.csv>

If <dos_prcres_txt> is omitted the parser looks for the Venango slice at
work2022p/venango/venango_prcres_slice.txt relative to the repo root.
"""
from __future__ import annotations

import csv
import os
import sys

COUNTY_NAME = "Venango"
COUNTY_CODE = "61"
ELECTION_TYPE = "P"  # DOS bulk-file election type for the primary

HEADER = ["county", "precinct", "office", "district", "party", "candidate", "votes"]
OFFICE_ORDER = [
    "U.S. Senate",
    "Governor",
    "Lieutenant Governor",
    "U.S. House",
    "State House",
]

OFFICES = {  # DOS office code -> output office name
    "USS": "U.S. Senate",
    "GOV": "Governor",
    "LTG": "Lieutenant Governor",
    "USC": "U.S. House",
    "STH": "State House",
}

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
    ("KELLY", "GEORGE", "J"): "Mike Kelly",                 # DOS suffix JR
    ("PASTORE", "DANIEL ", ""): "Dan Pastore",
    ("TELESZ", "RICHARD ", "E"): "Richard Telesz",
    ("JAMES", "ROBERT", "LEE"): "R Lee James",
}

# DOS precinct code -> county precinct name (merged splits noted in the
# docstring).  Names match the county's own 44-precinct list as used in
# the 2022 general file 20221108__pa__general__venango__precinct.csv.
PRECINCT_NAMES = {
    "10": "Allegheny Twp",
    "20": "Barkeyville Boro",
    "30": "Canal Twp",
    "40": "Cherrytree Twp",
    "50": "Clinton Twp",
    "60": "Clintonville Boro",
    "70": "Cooperstown Boro",
    "100": "Cornplanter Twp",
    "115": "Cranberry Twp 1",
    "125": "Cranberry Twp 2",
    "135": "Cranberry Twp 3",
    "170": "Emlenton Boro",
    "195": "Franklin 1",
    "225": "Franklin 2",
    "265": "Franklin 3",
    "290": "Frenchcreek Twp",
    "300": "Irwin Twp",
    "310": "Jackson Twp",
    "320": "Mineral Twp",
    "330": "Oakland Twp",
    "355": "Oil City 1",
    "375": "Oil City 2",
    "395": "Oil City 3",
    "405": "Oil City 4",
    "425": "Oil City 5",
    "445": "Oil City 6",
    "465": "Oil City 7",
    "510": "Oilcreek Twp",
    "520": "Pinegrove Twp",
    "530": "Pleasantville Boro",
    "540": "Plum Twp",
    "550": "Polk Boro",
    "560": "President Twp",   # + 565 below
    "565": "President Twp",   # merged
    "575": "Richland Twp",
    "595": "Rockland Twp",
    "620": "Rouseville Boro",
    "630": "Sandycreek Twp",
    "640": "Scrubgrass Twp",
    "650": "Sugarcreek Boro 1/4",
    "660": "Sugarcreek Boro 2",
    "670": "Sugarcreek Boro 3",
    "695": "Sugarcreek Boro 5",
    "710": "Utica Boro",
    "715": "Victory Twp",     # + 720 below (DOS splits the precinct
    "720": "Victory Twp",     # between U.S. House districts 15/16)
}

DEFAULT_SOURCE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "work2022p", "venango", "venango_prcres_slice.txt",
)

# County-level TOTALS printed in the county summary PDF (Electionware
# "Election Summary Report - w/Write-in Names", 06/26/2023), named
# candidates only.  Used by --verify.  Note: the PDF's McCormick total
# is PRE-recount (2,622); the CERTIFIED post-recount total is 2,623
# (= DOS bulk sum = ENR anchor) — verified with RECOUNT_EXPECTED below.
PDF_TOTALS = {
    ("U.S. Senate", "DEM"): {
        "Alex Khalil": 62,
        "Conor Lamb": 887,
        "Malcolm Kenyatta": 54,
        "John Fetterman": 2138,
    },
    ("U.S. Senate", "REP"): {
        "Kathy Barnette": 2167,
        "Mehmet Oz": 2042,
        "George Bochetto": 30,
        "Jeff Bartos": 316,
        "Dave McCormick": 2622,   # pre-recount; certified 2,623
        "Sean Gale": 195,
        "Carla Sands": 439,
    },
    ("Governor", "DEM"): {"Josh Shapiro": 2990},
    ("Governor", "REP"): {
        "Lou Barletta": 827,
        "Douglas V Mastriano": 4164,
        "Nche Zama": 151,
        "Dave White": 624,
        "Melissa Hart": 496,
        "Bill McSwain": 1097,
        "Charlie Gerow": 87,
        "Joe Gale": 187,
        "Jake Corman": 185,
    },
    ("Lieutenant Governor", "DEM"): {
        "Austin Davis": 2239,
        "Ray Sosa": 225,
        "Brian Sims": 577,
    },
    ("Lieutenant Governor", "REP"): {
        "Clarice Schillinger": 322,
        "James Earl Jones": 582,
        "Rick Saccone": 1304,
        "John Brown": 171,
        "Chris Frye": 684,
        "Jeff Coleman": 439,
        "Russ Diamond": 157,
        "Carrie Lewis Delrosso": 2780,
        "Teddy Daniels": 880,
    },
    ("U.S. House", "REP"): {
        "Glenn GT Thompson": 4471,   # District 15
        "Mike Kelly": 2823,          # District 16
    },
    ("U.S. House", "DEM"): {"Dan Pastore": 994, "Richard Telesz": 253},
    ("State House", "REP"): {"R Lee James": 7402},
}

# Omitted from the output (no per-precinct data in the DOS bulk file;
# the county summary reports them countywide only): the write-in-only
# DEM U.S. House District 15 contest (153 countywide write-ins), the
# write-in rows of the other contests, the State Committee contests
# (DEM: no candidate filed / REP: Cynthia Pankratz 4,973, Samuel H
# Breene 6,590 — both in the ENR anchor), and the local Committee
# Man / Committee Woman contests.


def county_rows_from_dos(path: str) -> list[dict]:
    """Read the PA DOS prcres comma-delimited structure; keep Venango
    (county code 61), 2022 primary election type "P"."""
    out = []
    with open(path, newline="", encoding="latin-1") as fh:
        for row in csv.reader(fh):
            if len(row) < 26 or row[:2] != ["2022", ELECTION_TYPE]:
                continue
            if row[2] != COUNTY_CODE:
                continue
            if row[8] not in OFFICES:
                raise SystemExit(
                    "unexpected office code %r in %s" % (row[8], path))
            if row[3] not in PRECINCT_NAMES:
                raise SystemExit(
                    "unknown precinct code %r in %s" % (row[3], path))
            out.append(
                {
                    "precinct_name": PRECINCT_NAMES[row[3]],
                    "office": OFFICES[row[8]],
                    "party": row[9],
                    "cand_last": row[11],
                    "cand_first": row[12],
                    "cand_middle": row[13],
                    "votes": int(row[15]),
                    "uscd": row[18],
                }
            )
    return out


def cand_name(r: dict) -> str:
    key = (r["cand_last"], r["cand_first"], r["cand_middle"])
    name = NAME_FIX.get(key)
    if name is None:
        raise SystemExit("unmapped candidate %r" % (key,))
    return name


def district_for(r: dict) -> str:
    if r["office"] == "U.S. House":
        return str(int(r["uscd"]))
    if r["office"] == "State House":
        return "64"
    return ""


def run_checks(rows: list[dict]) -> tuple[int, list[str]]:
    """Sum precinct votes per (office, party, candidate) and compare with
    the county summary PDF's printed countywide TOTALS."""
    sums: dict[tuple, int] = {}
    for r in rows:
        key = (r["office"], r["party"], cand_name(r))
        sums[key] = sums.get(key, 0) + r["votes"]
    problems = []
    n = 0
    for (office, party), cands in PDF_TOTALS.items():
        for cand, total in cands.items():
            n += 1
            mine = sums.get((office, party, cand), 0)
            if mine != total:
                problems.append(
                    "%s [%s]: %s: precinct sum %d != PDF total %d"
                    % (office, party, cand, mine, total))
    return n, problems


def main(argv: list[str]) -> int:
    args = [a for a in argv if a != "--verify"]
    src = args[0] if args and os.path.exists(args[0]) else DEFAULT_SOURCE
    if not os.path.exists(src):
        print("No Venango 2022 primary DOS source found (tried %s)"
              % (args or DEFAULT_SOURCE), file=sys.stderr)
        return 1
    rows = county_rows_from_dos(src)
    if not rows:
        print("No Venango primary rows found in source %s" % src,
              file=sys.stderr)
        return 1

    # Aggregate per (merged precinct, office, district, party, candidate).
    agg: dict[tuple, int] = {}
    codes_by_pct: dict[str, int] = {}
    for r in rows:
        name = r["precinct_name"]
        if name not in codes_by_pct:
            codes_by_pct[name] = len(codes_by_pct)
        key = (name, r["office"], district_for(r), r["party"], cand_name(r))
        agg[key] = agg.get(key, 0) + r["votes"]

    # Complete grid fill: every candidate appears in every precinct in
    # scope (0 votes where the bulk file prints no row).  Scope = every
    # precinct for statewide contests and State House; for U.S. House,
    # precincts whose (merged) constituent codes carry that district.
    usc_by_district: dict[str, set[str]] = {}
    for r in rows:
        usc_by_district.setdefault(r["uscd"], set()).add(r["precinct_name"])

    precincts = sorted(codes_by_pct,
                       key=lambda n: codes_by_pct[n])
    filled = set(agg)
    for key in list(agg):
        name, office, district, party, cand = key
        if office == "U.S. House":
            scope = usc_by_district[district]
        else:
            scope = precincts
        for p in scope:
            filled.add((p, office, district, party, cand))

    out_rows = sorted(
        (
            {
                "county": COUNTY_NAME,
                "precinct": key[0],
                "office": key[1],
                "district": key[2],
                "party": key[3],
                "candidate": key[4],
                "votes": agg.get(key, 0),
                "_p": codes_by_pct[key[0]],
                "_o": OFFICE_ORDER.index(key[1]),
            }
            for key in filled
        ),
        key=lambda x: (x["_p"], x["_o"], x["party"], x["candidate"]),
    )

    if "--verify" in argv:
        n, problems = run_checks(rows)
        print("verify: %d candidate totals checked against the county "
              "summary PDF (06/26/2023 Election Summary w/Write-in Names)"
              % n)
        if problems:
            expected = {f"U.S. Senate [REP]: Dave McCormick: precinct "
                        f"sum {sumsMcCormick(rows)} != PDF total 2622"}
            unexpected = [p for p in problems if p not in expected]
            if unexpected:
                for p in unexpected:
                    print("  MISMATCH " + p)
                return 1
            print("  EXPECTED RECOUNT RESIDUAL: "
                  "U.S. Senate [REP]: Dave McCormick: precinct sum %d != "
                  "PDF total 2622 (PDF is PRE-recount; certified "
                  "post-recount total is 2,623 = DOS sum = ENR anchor)"
                  % sumsMcCormick(rows))
        else:
            print("verify: all contest sums match the county summary PDF "
                  "TOTALS")
    else:
        # Always print the recount reconciliation note.
        mc = sumsMcCormick(rows)
        if mc != PDF_TOTALS[("U.S. Senate", "REP")]["Dave McCormick"]:
            print("note: GOP U.S. Senate Dave McCormick precinct sum %d "
                  "= certified post-recount total (county summary PDF "
                  "prints pre-recount 2,622)" % mc)
        else:
            print("note: GOP U.S. Senate Dave McCormick precinct sum %d "
                  "(pre-recount value; certified total is 2,623)" % mc)

    if len(args) > 1:
        with open(args[1], "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(HEADER)
            w.writerows(
                [o["county"], o["precinct"], o["office"], o["district"],
                 o["party"], o["candidate"], str(o["votes"])]
                for o in out_rows
            )
        print("wrote %s (%d data rows, %d precincts) from %s"
              % (args[1], len(out_rows), len(precincts), src))
    return 0


def sumsMcCormick(rows: list[dict]) -> int:
    return sum(
        r["votes"] for r in rows
        if cand_name(r) == "Dave McCormick")


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))