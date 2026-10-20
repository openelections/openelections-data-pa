#!/usr/bin/env python3
"""Bedford County, PA 2022 Primary — precinct-level results parser.

Source situation (documented):
  * Two copies of the county's certified report live in
    openelections-sources-pa/2022/primary/
      "Bedford PA ElectionSummaryReportRPT.pdf Official.pdf" (July 2022 copy)
      "Bedford PA 2022 Primary ElectionSummaryReportRPT.pdf Official.pdf"
    They are byte-identical (MD5 53c22e1b994d1a8773f5c8d05a885fd8).
  * That report is a Dominion-style "Election Summary Report - Closed
    Primary" (generated 06/07/2022 2:21:31 PM, 30 pages) with COUNTYWIDE
    totals only: per-contest named-candidate + Write-in + Total Votes blocks
    for the DEM and REP U.S. Senate / Governor / Lieutenant Governor /
    U.S. House 13 / State Senate 32 / State House 78 / State Committee
    contests, party-level ballot statistics, and per-municipality DEM County
    Committee Person contests (single-precinct local party races).  Unlike the
    2022 general report (a "Results per Precinct" crosstab handled by
    pa_bedford_general_2022_results_parser.py) it carries NO per-precinct
    rows, so a distinct char-level approach would recover nothing.  (The
    write-in-detail listing at top level shows write-in name counts, but with
    no precinct labels.)

  Precinct-level figures therefore come from the PA Department of State
  official certified precinct returns bulk file for the primary:
      ElectionReturns_2022_Primary_PrecinctReturns.txt
  (ASCII comma-delimited, quoted fields; election type "P"; Bedford = county
  code 5; 1,440 rows = 40 precincts x 36 named-candidate contest rows).  The
  DOS precinct list (40 codes) matches the county's own precinct list and the
  2022 general precinct file already in this repo.  A verbatim Bedford slice
  of the bulk file is kept at work2022p/bedford/bedford_prcres_slice.txt.

Consequences (all inherent to the sources, not parsing losses):
  * No election-day / mail / provisional breakdown -> 7-column output header.
  * No per-precinct write-in rows (the DOS bulk file carries none): the DEM
    U.S. House 13 (227 write-ins), State Senate 32 (100) and State House 78
    (113) contests are write-in-only and therefore have NO rows at all, and
    the Write-in columns of every other contest cannot be assigned to
    precincts.  Per the no-invention rule no Write Ins rows are emitted.
  * The State Committee races (DEM: Dave Popp 1,823; REP: Tonya Clark 6,906 /
    Melvin C McDowell 3,680 / Barry L Dallara 4,081; countywide PDF totals)
    are absent from the DOS precinct bulk file for Bedford, so they have no
    per-precinct rows either (same treatment as Cameron County's primary).
  * No per-precinct Registered Voters / Ballots Cast.  Countywide PDF page 1:
    Ballots Cast 12,295 = DEM 2,142 (ED 1,114 + mail 1,013 + prov 15) +
    REP 10,153 (ED 9,207 + mail 852 + prov 94); RV DEM 6,287 / REP 23,263.
  * The DEM County Committee Person contests (per-municipality local party
    races; pages 17-30 of the county PDF) are omitted, same as Cameron.

Republican U.S. Senate recount: the county summary PDF is the post-recount
official report (06/07/2022) and its U.S. Senate figures equal the DOS
precinct sums AND the ENR county anchor exactly (Oz 2,926 / McCormick 2,744 /
Barnette 2,832 / Bartos 688 / Sands 725 / Bochetto 51 / Sean Gale 54), so the
recount is fully reconciled and no residual remains.

Usage:
    python parsers/pa_bedford_primary_2022_results_parser.py <dos_prcres_txt> <out.csv>
    python parsers/pa_bedford_primary_2022_results_parser.py --verify <dos_prcres_txt> <out.csv>

If <dos_prcres_txt> is omitted the parser looks for the Bedford slice first,
then the shared bulk file copy.
"""
from __future__ import annotations

import csv
import os
import sys

COUNTY_NAME = "Bedford"
COUNTY_CODE = "5"
ELECTION_TYPE = "P"  # DOS bulk-file election type for the primary

HEADER = ["county", "precinct", "office", "district", "party", "candidate",
          "votes"]
OFFICE_ORDER = ["U.S. Senate", "Governor", "Lieutenant Governor", "U.S. House",
                "State Senate", "State House"]

OFFICES = {  # DOS office code -> (office, district field to use)
    "USS": ("U.S. Senate", "cand_district"),
    "GOV": ("Governor", "cand_district"),
    "LTG": ("Lieutenant Governor", "cand_district"),
    "USC": ("U.S. House", "uscd"),
    "STS": ("State Senate", "sldd"),
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
    ("DELROSSO", "CARRIE", ""): "Carrie Lewis DelRosso",
    ("DANIELS", "THEODORE", ""): "Teddy Daniels",
    ("JOYCE", "JOHN", ""): "John Joyce",
    ("STEFANO", "PATRICK", "J."): "Patrick J. Stefano",
    ("TOPPER", "JESSE", "W"): "Jesse Topper",
}

# precinct code -> name (exactly as in
# 2022/counties/20221108__pa__general__bedford__precinct.csv, which uses the
# county's own precinct-number prefixes).  DOS raw names in the bulk file are
# e.g. 'BEDFORD X 1' (township precinct 1 -> 'X' flag), 'BEDFORD W EAST'
# (borough ward), 'BLOOMFIELD', 'HOPEWELL' x2 / 'WOODBURY' x2 (borough vs
# township separated by DOS field 21: 6 = Borough, 4 = Township).
PRECINCT_NAMES = {
    "10": "0201 Bedford Township No 1",
    "20": "0202 Bedford Township No 2",
    "30": "0101 Bedford Borough East Ward",
    "40": "0102 Bedford Borough West Ward",
    "50": "0301 Bloomfield Township",
    "60": "0401 Broad Top Township",
    "80": "0501 Coaldale Borough",
    "90": "0601 Colerain Township",
    "100": "0701 Cumberland Valley Township",
    "110": "2501 East Providence Township",
    "120": "3201 East St. Clair Township",
    "130": "0801 Everett Borough",
    "140": "0901 Harrison Township",
    "150": "1101 Hopewell Township",       # DOS f21=4 (township)
    "160": "1001 Hopewell Borough",        # DOS f21=6 (borough)
    "170": "1201 Hyndman Borough",
    "180": "1301 Juniata Township",
    "190": "1401 Kimmel Township",
    "200": "1501 King Township",
    "210": "1601 Liberty Township",
    "220": "1701 Lincoln Township",
    "230": "1801 Londonderry Township",
    "240": "1901 Mann Township",
    "250": "2001 Manns Choice Borough",
    "260": "2101 Monroe Township",
    "270": "2201 Napier Township",
    "280": "2301 New Paris Borough",
    "285": "3301 Pavia Township",
    "290": "2401 Pleasantville Borough",
    "300": "2601 Rainsburg Borough",
    "310": "2701 Saxton Borough",
    "320": "2801 Schellsburg Borough",
    "330": "2901 Snake Spring Township",
    "340": "3601 South Woodbury Township",
    "350": "3003 Southampton Township",
    "370": "3101 St. Clairsville Borough",
    "390": "2502 West Providence Township",
    "400": "3202 West St. Clair Township",
    "410": "3501 Woodbury Township",       # DOS f21=4 (township)
    "420": "3401 Woodbury Borough",        # DOS f21=6 (borough)
}

DEFAULT_SOURCES = [
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                 "work2022p", "bedford", "bedford_prcres_slice.txt"),
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                 "work2022p", "monroe",
                 "ElectionReturns_2022_Primary_PrecinctReturns.txt"),
]

# County-level named-candidate totals printed in the certified county summary
# PDF (OFFICIAL RESULTS, generated 06/07/2022; post-recount).  Used --verify;
# every value also equals the ENR county anchor.  The 2022-primary contests
# the county PDF reports whose totals were write-in-only (they appear with no
# DOS precinct rows and so no output rows): DEM U.S. House 13 (227 write-ins),
# DEM State Senate 32 (100), DEM State House 78 (113); and the State
# Committee contests (see module docstring).
PDF_TOTALS = {
    ("U.S. Senate", "DEM"): {
        "Alex Khalil": 71,
        "Conor Lamb": 327,
        "Malcolm Kenyatta": 48,
        "John Fetterman": 1543,
    },
    ("U.S. Senate", "REP"): {
        "Kathy Barnette": 2832,
        "Mehmet Oz": 2926,
        "George Bochetto": 51,
        "Jeff Bartos": 688,
        "Dave McCormick": 2744,
        "Sean Gale": 54,
        "Carla Sands": 725,
    },
    ("Governor", "DEM"): {"Josh Shapiro": 1839},
    ("Governor", "REP"): {
        "Lou Barletta": 919,
        "Douglas V Mastriano": 5779,
        "Nche Zama": 31,
        "Dave White": 438,
        "Melissa Hart": 243,
        "Bill McSwain": 1945,
        "Charlie Gerow": 89,
        "Joe Gale": 45,
        "Jake Corman": 537,
    },
    ("Lieutenant Governor", "DEM"): {
        "Austin Davis": 1334,
        "Ray Sosa": 209,
        "Brian Sims": 348,
    },
    ("Lieutenant Governor", "REP"): {
        "Clarice Schillinger": 584,
        "James Earl Jones": 711,
        "Rick Saccone": 1202,
        "John Brown": 273,
        "Chris Frye": 353,
        "Jeff Coleman": 1170,
        "Russ Diamond": 218,
        "Carrie Lewis DelRosso": 3384,
        "Teddy Daniels": 1482,
    },
    ("U.S. House", "REP"): {"John Joyce": 9152},
    ("State Senate", "REP"): {"Patrick J. Stefano": 8343},
    ("State House", "REP"): {"Jesse Topper": 9508},
}


def county_rows_from_dos(path: str) -> list[dict]:
    """Read the PA DOS prcres comma-delimited structure, keep Bedford (county
    code 5), primary election type."""
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
                    "cand_suffix": row[14],
                    "votes": int(row[15]),
                    "cand_district": row[4],  # Candidate District field
                    "uscd": row[18],
                    "sldd": row[19],
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
    if office == "State Senate":
        return str(int(r["sldd"]))
    return str(int(r["shd"]))  # State House


def to_output_rows(rows: list[dict]) -> list[dict]:
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
                "_party": r["party"],
            }
        )
    out.sort(key=lambda x: (x["_code"], x["_oc"], x["_party"],
                            x["candidate"]))
    return out


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
                problems.append(
                    f"{office} [{party}]: {cand}: precinct sum {mine}"
                    f" != PDF total {total}")
    return n, problems


def main(argv: list[str]) -> int:
    args = [a for a in argv if a != "--verify"]
    src = None
    if args and os.path.exists(args[0]):
        src = args[0]
    else:
        for cand in DEFAULT_SOURCES:
            if os.path.exists(cand):
                src = cand
                break
    if not src:
        print("No Bedford 2022 primary DOS source found (tried %s)"
              % (args or DEFAULT_SOURCES), file=sys.stderr)
        return 1
    rows = county_rows_from_dos(src)
    if not rows:
        print("No Bedford primary rows found in source %s" % src,
              file=sys.stderr)
        return 1
    missing_precincts = sorted({r["precinct_code"] for r in rows}
                               - set(PRECINCT_NAMES))
    if missing_precincts:
        print("Unknown precinct codes: %s" % missing_precincts,
              file=sys.stderr)
        return 1
    # every precinct should carry the same 36 named-candidate rows
    per = {}
    for r in rows:
        per.setdefault(r["precinct_code"], set()).add(
            (r["office_code"], r["party"], cand_name(r)))
    if len({frozenset(v) for v in per.values()}) != 1:
        print("Precincts differ in their contest/candidate sets",
              file=sys.stderr)
        return 1

    out_rows = to_output_rows(rows)

    if "--verify" in argv:
        n, problems = run_checks(rows)
        print("verify: %d candidate totals checked against county summary"
              " PDF" % n)
        if problems:
            for p in problems:
                print("  MISMATCH " + p)
            return 1
        print("verify: all contest sums match the county summary PDF TOTALS")

    if len(args) > 1:
        with open(args[1], "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(HEADER)
            for o in out_rows:
                w.writerow([o["county"], o["precinct"], o["office"],
                            o["district"], o["party"], o["candidate"],
                            str(o["votes"])])
        print("wrote %s (%d data rows) from %s" % (args[1], len(out_rows),
                                                   src))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))