#!/usr/bin/env python3
"""Venango County, PA 2026 Primary (2026-05-19) — precinct-level results parser.

Source situation (documented):
  * Venango's county-sourced PDF, "Venango County 2026 General Primary
    Election Summary with Writeins OFFICIAL.pdf" (Electionware summary
    generated 6/1/2026, marked OFFICIAL), is COUNTYWIDE ONLY — one contest
    per page with Election-Day/Mail/Provisional columns and named write-in
    lines, but no per-precinct rows (so it supplies no precinct data; no
    __county.csv rows are created from it because the statewide county
    anchor already carries the countywide totals; see verification below).
    Text extract: work2026p/venango/summary.txt.
  * Per-precinct figures therefore come from the PA Department of State
    certified precinct-returns bulk file fallback (AGENT_BRIEF rule):
      "Venango County DOS Official Precinct Returns 2026 Primary.csv" — the
    Venango slice (county code 61, election type "P") of the statewide 2026
    primary certified precinct-returns export.  An identical slice is kept at
    work2026p/dos/venango__dos_precinctreturns.txt (414 rows = 46 precincts x
    {GOV DEM/REP, LTG DEM/REP, USC DEM-or-REP per district, STH DEM/REP}
    contest rows); the two sources were cross-checked — same (precinct_code,
    office_code, party, district, votes) multiset, byte-equivalent data, only
    the header row differs.  The parser reads the txt slice.

DOS quirks (inherent to the source, not parsing losses):
  * No per-candidate election-day/mail/provisional breakdown -> plain
    7-column output header (the county summary's ED/mail/provisional columns
    are countywide totals only).
  * No write-in rows at all (the county summary prints countywide Write-In
    Totals plus individually-named countywide write-in lines for several
    contests, e.g. DEM Governor 25 write-ins incl. "BLANK", "FICTITIOUS", but
    nothing precinct-level) -> no Write In rows emitted.
  * No Registered Voters / Ballots Cast rows -> none emitted.
  * No State Committee rows (the DOS bulk file does not carry State
    Committee) -> the two Member of * State Committee contests (countywide:
    DEM Edward Scurry 2,299, REP Sam Breene 3,758 per the anchor / county
    summary) are documented omissions.
  * No State Senate rows (Venango's SD 21 seat was not up in 2026); the
    anchor likewise carries no VENANGO State Senate row.

Contests covered (the same ones the anchor verifies for VENANGO):
  Governor (DEM/REP), Lieutenant Governor (DEM/REP x2 candidates),
  U.S. House 15 (22 precincts) and U.S. House 16 (24 precincts, DEM/REP
  each), State House 64 (DEM/REP, all 46 precincts).

District handling: the DOS slice carries the district in field index 5 for
USC/STS/STH rows (cross-checked per row against the us_cd / state_house
fields at indexes 18/20) — U.S. House 15 or 16 split by precinct, State
House 64 for all precincts.  This is the same straight-from-field approach
that sidesteps the 2024 vintage bug, where a "Nth CONGRESSIONAL District"
regex against the county report produced wrong/missing districts; nothing is
regexed here, the DOS field is authoritative and per-row cross-checked.

Precinct naming: DOS prints "NAME", qualifier ("D"/"X"/""), split.  Names are
combined per the 2026 brief ("CARROLL VALLEY","D","1" -> "Carroll Valley
District 1")): D + number -> "District N"; plain municipality names get the
Township/Borough suffix they need, matching the county's prior-year lineage
rows; "PRESIDENT   School District 1" splits are named as printed; the
Victory Township precincts are DOS-splitted by congressional district and
keep the source's parenthetical as printed.

Usage:
    uv run python parsers/pa_venango_primary_2026_results_parser.py \
        <dos_returns_txt> <out_csv> [--verify]
    (--verify cross-checks every contest's precinct sums against the
    statewide county anchor 2026/20260519__pa__primary__county.csv)

Defaults: <dos_returns_txt> = work2026p/dos/venango__dos_precinctreturns.txt,
          <out_csv>         = 2026/counties/20260519__pa__primary__venango__precinct.csv
"""
from __future__ import annotations

import csv
import os
import re
import sys

COUNTY_NAME = "Venango"
COUNTY_CODE = "61"  # PA DOS county code
YEAR = "2026"
ELECTION_TYPE = "P"

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_SOURCE = os.path.join(
    REPO_ROOT, "work2026p", "dos", "venango__dos_precinctreturns.txt")
DEFAULT_OUT = os.path.join(
    REPO_ROOT, "2026", "counties",
    "20260519__pa__primary__venango__precinct.csv")
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
    "2026C1072": "Justin Wagner",       # WAGNER, JUSTIN REED
    "2026C0461": "Mike Kelly",          # KELLY, GEORGE JOSEPH JR.
    "2026C0517": "Glenn GT Thompson",   # THOMPSON, GLENN WILLIAM JR
    "2026C1357": "Amanda Shaffer",      # SHAFFER, AMANDA
    "2026C0793": "R Lee James",         # JAMES, ROBERT LEE
}

# DOS precinct code -> printed precinct name (qualifier + split combined per
# the 2026 brief, suffixes matching the county's prior-year rows).  Codes 715/
# 720 are Victory Township split by congressional district, 560/565 President
# Township's two School District precincts (name as printed).
PRECINCT_NAMES = {
    "10": "Allegheny Township",
    "20": "Barkeyville Borough",
    "30": "Canal Township",
    "40": "Cherrytree Township",
    "50": "Clinton Township",
    "60": "Clintonville Borough",
    "70": "Cooperstown Borough",
    "100": "Cornplanter Township",
    "115": "Cranberry District 1",
    "125": "Cranberry District 2",
    "135": "Cranberry District 3",
    "170": "Emlenton Borough",
    "195": "Franklin District 1",
    "225": "Franklin District 2",
    "265": "Franklin District 3",
    "290": "Frenchcreek Township",
    "300": "Irwin Township",
    "310": "Jackson Township",
    "320": "Mineral Township",
    "330": "Oakland Township",
    "355": "Oil City District 1",
    "375": "Oil City District 2",
    "395": "Oil City District 3",
    "405": "Oil City District 4",
    "425": "Oil City District 5",
    "445": "Oil City District 6",
    "465": "Oil City District 7",
    "510": "Oilcreek Township",
    "520": "Pinegrove Township",
    "530": "Pleasantville Borough",
    "540": "Plum Township",
    "550": "Polk Borough",
    "560": "President School District 1",
    "565": "President School District 2",
    "575": "Richland Township",
    "595": "Rockland Township",
    "620": "Rouseville Borough",
    "630": "Sandycreek Township",
    "640": "Scrubgrass Township",
    "650": "Sugarcreek District 1",
    "660": "Sugarcreek District 2",
    "670": "Sugarcreek District 3",
    "695": "Sugarcreek District 5",
    "710": "Utica Borough",
    "715": "Victory (USC 15)",
    "720": "Victory (USC 16)",
}


def dos_rows(path: str) -> list[dict]:
    """Venango (county code 45, type 'P') rows of the DOS certified
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
                "precinct_code": r[3],
                "district": district,
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
    statewide county anchor's VENANGO rows (named candidates, no write-ins)."""
    mine: dict[tuple, int] = {}
    for r in rows:
        key = (OFFICES[r["office_code"]], r["district"], r["party"],
               norm(NAME_FIX[r["cand_no"]]))
        mine[key] = mine.get(key, 0) + r["votes"]
    anchor = {}
    with open(ANCHOR_PATH, newline="", encoding="utf-8") as fh:
        for row in csv.reader(fh):
            if row and row[0].upper() == COUNTY_NAME.upper():
                office, district, party, cand, votes = (row[1], row[2],
                                                        row[3], row[4],
                                                        int(row[5]))
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
        print("No Venango 2026 primary DOS source found (tried %r)" % src,
              file=sys.stderr)
        return 1
    rows = dos_rows(src)
    if not rows:
        print("No Venango 2026 primary rows found in %s" % src,
              file=sys.stderr)
        return 1
    missing = sorted({r["precinct_code"] for r in rows} - set(PRECINCT_NAMES))
    if missing:
        print("Unknown DOS precinct codes: %s" % missing, file=sys.stderr)
        return 1
    # per-precinct USC district sanity: Venango splits Victory by USC district
    usc_by_precinct: dict[str, set] = {}
    for r in rows:
        if r["office_code"] == "USC":
            usc_by_precinct.setdefault(r["precinct_code"], set()).add(
                r["district"])
    multi = {p: sorted(v) for p, v in usc_by_precinct.items() if len(v) > 1}
    if multi:
        raise SystemExit("precincts carrying multiple US House contests: %r"
                         % multi)

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
