#!/usr/bin/env python3
"""Union County, PA 2026 Primary (2026-05-19) — precinct-level results parser.

Source situation (documented):
  * Union's county-sourced PDF, "Union County Summary results-1.pdf" (summary
    report dated 6/9/2026, "Official Count"), is COUNTYWIDE ONLY — one contest
    per paired page-group with no per-precinct rows (so it supplies no
    precinct data; no __county.csv rows are created from it because the
    statewide county anchor already carries the countywide totals; see
    verification below).  Text extract: work2026p/union/summary.txt.
  * Per-precinct figures therefore come from the PA Department of State
    certified precinct-returns bulk file fallback (AGENT_BRIEF rule):
      "Union County DOS Official Precinct Returns 2026 Primary.csv" — the
    Union slice (county code 60, election type "P") of the statewide 2026
    primary certified precinct-returns export.  An identical slice is kept at
    work2026p/dos/union__dos_precinctreturns.txt (232 rows = 27 precincts x
    {GOV DEM/REP, LTG DEM/REP, USC DEM/REP, STH contest rows}); the two
    sources were cross-checked — same (precinct_code, office_code, party,
    district, votes) multiset, byte-equivalent data, only the header row
    differs.  The parser reads the txt slice.

DOS quirks (inherent to the source, not parsing losses):
  * No per-candidate election-day/mail/provisional breakdown -> plain
    7-column output header.
  * No write-in rows at all (the county summary does print countywide
    write-in totals — e.g. Governor DEM 9, Governor REP 118, Gen. Assembly 83
    DEM 6, Gen. Assembly 85 DEM 56 — but nothing precinct-level) -> no Write
    In rows emitted.
  * No Registered Voters / Ballots Cast rows -> none emitted.
  * No State Committee rows (the DOS bulk file does not carry State
    Committee) -> the two Member of * State Committee contests (Countywide:
    DEM Rick Thomas 1,984, REP Daniel F. Clark 2,404 per the county summary /
    anchor) are documented omissions.
  * No State Senate rows (Union's SD 23 seat was not up in 2026); the anchor
    likewise carries no UNION State Senate row.
  * Uncontested DEM contests appear as write-in-only in the county summary
    (General Assembly 83 DEM: 6 write-ins; General Assembly 85 DEM: 56
    write-ins) and have no named candidate anywhere, so the anchor carries no
    row and no rows are emitted for them.

Contests covered (the same ones the anchor verifies for UNION):
  Governor (DEM/REP), Lieutenant Governor (DEM/REP x2 candidates),
  U.S. House 15 (DEM/REP), State House 76 (DEM/REP), State House 83 (REP),
  State House 85 (REP).

District handling: the DOS slice carries the district in field index 5 for
USC/STS/STH rows (cross-checked per row against the us_cd / state_house
fields at indexes 18/20) — U.S. House 15 for all 27 precincts; State House
76 (16 precincts), 83 (5), 85 (6).  (The Venango-2024 vintage bug where
"Us CONGRESSIONAL District" text was regexed out of county reports does not
apply here: the district comes straight from the DOS field, never regexed.)

Precinct naming: DOS prints "NAME", qualifier ("X"/"W"/""), split.  Names are
combined per the 2026 brief ("CARROLL VALLEY","D","1" -> "Carroll Valley
District 1"): X + number -> "District N", W + number -> "Ward N", the
Mifflinburg East/West splits keep the repo's Cameron-2026 DOS-lineage style
"Mifflinburg East Precinct"/"Mifflinburg West Precinct", and plain
municipality names are title-cased as-is (Union files carry bare
municipality names, per the county's 2022/2024 lineage rows).

Usage:
    uv run python parsers/pa_union_primary_2026_results_parser.py \
        <dos_returns_txt> <out_csv> [--verify]
    (--verify cross-checks every contest's precinct sums against the
    statewide county anchor 2026/20260519__pa__primary__county.csv)

Defaults: <dos_returns_txt> = work2026p/dos/union__dos_precinctreturns.txt,
          <out_csv>         = 2026/counties/20260519__pa__primary__union__precinct.csv
"""
from __future__ import annotations

import csv
import os
import re
import sys

COUNTY_NAME = "Union"
COUNTY_CODE = "60"  # PA DOS county code
YEAR = "2026"
ELECTION_TYPE = "P"

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_SOURCE = os.path.join(
    REPO_ROOT, "work2026p", "dos", "union__dos_precinctreturns.txt")
DEFAULT_OUT = os.path.join(
    REPO_ROOT, "2026", "counties",
    "20260519__pa__primary__union__precinct.csv")
ANCHOR_PATH = os.path.join(
    REPO_ROOT, "2026", "20260519__pa__primary__county.csv")

HEADER = ["county", "precinct", "office", "district", "party", "candidate",
          "votes"]
OFFICE_ORDER = ["Governor", "Lieutenant Governor", "U.S. House", "State House"]

OFFICES = {  # DOS office code -> standard office
    "GOV": "Governor",
    "LTG": "Lieutenant Governor",
    "USC": "U.S. House",
    "STS": "State Senate",
    "STH": "State House",
}

# DOS candidate number -> printed name, matching the statewide county anchor
# 2026/20260519__pa__primary__county.csv (case-insensitive compare).
NAME_FIX = {
    "2026C0403": "Josh Shapiro",        # SHAPIRO, JOSHUA D
    "2026C0735": "Stacy Garrity",       # GARRITY, STACY LORRAINE
    "2026C0092": "Austin Davis",        # DAVIS, AUSTIN
    "2026C1021": "John Ventre",         # VENTRE, JOHN
    "2026C0645": "Jason Richey",        # RICHEY, JASON L
    "2026C0283": "Ray Bilger",          # BILGER, RAY
    "2026C0517": "Glenn GT Thompson",   # THOMPSON, GLENN WILLIAM JR
    "2026C0730": "Sara Eggler",         # EGGLER, SARA ELIZABETH
    "2026C0104": "Stephanie Borowicz",  # BOROWICZ, STEPHANIE PAIGE
    "2026C0466": "Jamie L Flick",       # FLICK, JAMIE LEE
    "2026C0160": "David H Rowe",        # ROWE, DAVID HUMMER
}

# DOS precinct code -> printed precinct name (qualifier + split combined per
# the 2026 brief; see module docstring).
PRECINCT_NAMES = {
    "10": "Buffalo District 1",
    "20": "Buffalo District 2",
    "30": "East Buffalo District 1",
    "40": "East Buffalo District 2",
    "50": "East Buffalo District 3",
    "60": "East Buffalo District 4",
    "70": "Gregg",
    "80": "Hartleton",
    "90": "Hartley",
    "100": "Kelly District 1",
    "110": "Kelly District 2",
    "120": "Lewis",
    "130": "Lewisburg Ward 1",
    "140": "Lewisburg Ward 2",
    "150": "Lewisburg Ward 3",
    "160": "Lewisburg Ward 4",
    "170": "Limestone",
    "180": "Mifflinburg East Precinct",
    "190": "Mifflinburg West Precinct",
    "200": "New Berlin",
    "210": "Union",
    "215": "Union Independent",
    "220": "West Buffalo",
    "230": "White Deer District 1",
    "240": "White Deer District 2",
    "250": "White Deer District 3",
    "260": "White Deer District 4",
}


def dos_rows(path: str) -> list[dict]:
    """Union (county code 60, type 'P') rows of the DOS certified
    precinct-returns slice, with per-row district integrity checks."""
    out = []
    with open(path, newline="", encoding="latin-1") as fh:
        for r in csv.reader(fh):
            if len(r) < 21 or r[0] != YEAR or r[1] != ELECTION_TYPE:
                continue
            if r[2] != COUNTY_CODE:
                continue
            if r[8] not in OFFICES:
                raise SystemExit(
                    "unexpected DOS office code %r in %s" % (r[8], path))
            if not re.fullmatch(r"2026C\d{4}", r[10]):
                raise SystemExit("unexpected candidate id %r" % r[10])
            district = r[5]
            if r[8] == "USC" and district != r[18]:
                raise SystemExit("USC district %r != us_cd %r at precinct %s"
                                 % (r[5], r[18], r[3]))
            if r[8] == "STH" and district != r[20]:
                raise SystemExit("STH district %r != state_house %r at "
                                 "precinct %s" % (r[5], r[20], r[3]))
            if r[8] in ("GOV", "LTG"):
                if district != "0":
                    raise SystemExit("district %r on a %s row" %
                                     (district, r[8]))
                district = ""
            out.append({
                "district": district,
                "precinct_code": r[3],
                "office_code": r[8],
                "party": r[9],
                "cand_no": r[10],
                "votes": int(r[15]),
                "party_rank": int(r[6]),
                "ballot_no": int(r[7]),
            })
    return out


def to_output_rows(rows: list[dict]) -> list[list]:
    out = []
    for r in rows:
        office = OFFICES[r["office_code"]]
        cand = NAME_FIX[r["cand_no"]]
        out.append({
            "precinct": PRECINCT_NAMES[r["precinct_code"]],
            "office": office,
            "district": r["district"],
            "party": r["party"],
            "candidate": cand,
            "votes": r["votes"],
            "_p": int(r["precinct_code"]),
            "_o": (OFFICE_ORDER.index(office), r["party_rank"],
                   r["ballot_no"]),
        })
    out.sort(key=lambda x: (x["_p"], x["_o"]))
    return [[COUNTY_NAME, o["precinct"], o["office"], o["district"],
             o["party"], o["candidate"], str(o["votes"])] for o in out]


def norm(s: str) -> str:
    """Case/punctuation-insensitive name key for anchor comparison."""
    return re.sub(r"[^a-z0-9]", "", s.lower())


def verify_anchor(rows: list[dict]) -> tuple[int, list[str]]:
    """Every (office, district, party, candidate) precinct sum vs the
    statewide county anchor's UNION rows (named candidates, no write-ins)."""
    mine: dict[tuple, int] = {}
    for r in rows:
        key = (OFFICES[r["office_code"]], r["district"], r["party"],
               norm(NAME_FIX[r["cand_no"]]))
        mine[key] = mine.get(key, 0) + r["votes"]
    anchor = {}
    with open(ANCHOR_PATH, newline="", encoding="utf-8") as fh:
        for row in csv.reader(fh):
            if row and row[0].upper() == COUNTY_NAME.upper():
                office, district, party, cand, votes = row[1], row[2], row[3], row[4], int(row[5])
                if party == "Democratic":
                    party = "DEM"
                elif party == "Republican":
                    party = "REP"
                anchor[(office, district, party, norm(cand))] = votes
    problems, n = [], 0
    for key in sorted(mine):
        n += 1
        if key not in anchor:
            problems.append("our row %r has no anchor counterpart" % (key,))
        elif anchor[key] != mine[key]:
            problems.append("%s %s [%s] %s: ours %d != anchor %d" %
                            (key[0], key[1], key[2], key[3], mine[key],
                             anchor[key]))
    for key in sorted(anchor):
        if key not in mine:
            # documented omissions: State Committee (not in the DOS bulk file)
            if "State Committee" in key[0]:
                continue
            problems.append("anchor candidate not covered by ours: %r" %
                            (key,))
    return n, problems


def main(argv: list[str]) -> int:
    args = [a for a in argv if a != "--verify"]
    src = args[0] if args and os.path.exists(args[0]) else DEFAULT_SOURCE
    if not os.path.exists(src):
        print("No Union 2026 primary DOS source found (tried %r)" % src,
              file=sys.stderr)
        return 1
    rows = dos_rows(src)
    if not rows:
        print("No Union 2026 primary rows found in %s" % src, file=sys.stderr)
        return 1
    missing = sorted({r["precinct_code"] for r in rows} - set(PRECINCT_NAMES))
    if missing:
        print("Unknown DOS precinct codes: %s" % missing, file=sys.stderr)
        return 1

    out_rows = to_output_rows(rows)
    problems = []
    if "--verify" in argv:
        n, problems = verify_anchor(rows)
        print("verify: %d contest totals cross-checked against the 2026 "
              "anchor %s" % (n, os.path.relpath(ANCHOR_PATH, REPO_ROOT)))
        if problems:
            for p in problems:
                print("  MISMATCH " + p)
            return 1
    out_path = args[1] if len(args) > 1 else DEFAULT_OUT
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    with open(out_path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(HEADER)
        w.writerows(out_rows)
    print("wrote %s (%d data rows) from %s" % (out_path, len(out_rows), src))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
