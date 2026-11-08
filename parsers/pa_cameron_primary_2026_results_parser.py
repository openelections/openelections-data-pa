#!/usr/bin/env python3
"""Cameron County, PA 2026 Primary — precinct-level results parser.

Source situation (documented):
  * Cameron has no county-sourced precinct report for the 2026 primary.
    Per-precinct figures come from the PA Department of State official
    certified precinct returns for the primary:
      "~/code/openelections-sources-pa/2026/primary/Cameron County DOS Official
       Precinct Returns 2026 Primary.csv" — the Cameron slice (county code 12,
       election type "P") of the statewide 2026 primary certified
       precinct-returns export.  An identical slice is kept at
      work2026p/dos/cameron__dos_precinctreturns.txt (90 rows = 10 precincts
      x 9 named-candidate contest rows); the two files were cross-checked —
      same header, same values, only the leading header row differs.

  * "Cameron County 2 Percent Statistical Sample Report DOS 2026 Primary.pdf"
    (audit date 2-Jun-26) is a POST-election statistical sample: ONE precinct
    batch, "West Shippen Township", 198 ballots sampled, pre- and post-sample
    counts identical.  It does NOT print per-precinct Registered Voters /
    Ballots Cast (it lists only sampled contest vote counts), so it cannot
    supply RV/BC.  Its sampled counts equal the DOS Shippen West Precinct
    rows exactly (verified --verify): Governor DEM Shapiro 48 (+1 undervote),
    Governor REP Garrity 140 (+2 write-in, +7 undervote), Lt Gov DEM Davis
    47, Lt Gov REP Ventre 54 / Richey 86, U.S. House 15 DEM Bilger 46 /
    REP Thompson 140, State House 67 DEM Ware 46 / REP Causer 143.  The
    report's 2 write-ins and undervotes are sample tallies, not per-precinct
    returns, so per the no-invention rule no rows are emitted from them.
    Text extract: work2026p/cameron/twopct.txt.

Consequences (all inherent to the DOS source, not parsing losses):
  * No election-day / mail / provisional breakdown -> 7-column output header.
  * No per-precinct write-in rows (DOS bulk file carries none) -> no Write In
    rows emitted.
  * No per-precinct Registered Voters / Ballots Cast in either source -> none
    emitted.
  * No State Senate contest rows (Cameron's SD 25 seat was not up in 2026);
    the statewide county anchor 2026/20260519__pa__primary__county.csv
    likewise carries no CAMERON State Senate row.
  * No State Committee rows (the DOS bulk file does not carry State
    Committee).
  * Contests covered: Governor (DEM/REP), Lieutenant Governor (DEM/REP x2),
    U.S. House 15 (DEM/REP), State House 67 (DEM/REP) — the same 9
    contest rows per precinct the anchor verifies.

Precinct naming: DOS raw names are e.g. 'EMPORIUM X EAST' (X flag = precinct
split), 'SHIPPEN X WEST', plus plain municipality names.  Names follow the
repo's existing Cameron DOS-lineage precinct list (2016/2022 general and
2022 primary files): 'Emporium East Precinct', 'Shippen West Precinct',
etc.  (The county's own 2024 primary file and the 2% sample report use the
same ten precincts under county-style names — 'East Ward Emporium Borough',
'West Shippen Township', ....)

Usage:
    uv run python parsers/pa_cameron_primary_2026_results_parser.py \
        <dos_returns_csv> <out_csv>
    uv run python parsers/pa_cameron_primary_2026_results_parser.py \
        <dos_returns_csv> <out_csv> --verify

Defaults: <dos_returns_csv> = work2026p/dos/cameron__dos_precinctreturns.txt,
          <out_csv> = 2026/counties/20260519__pa__primary__cameron__precinct.csv
"""
from __future__ import annotations

import csv
import os
import sys

COUNTY_NAME = "Cameron"
COUNTY_CODE = "12"
YEAR = "2026"
ELECTION_TYPE = "P"

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_SOURCE = os.path.join(
    REPO_ROOT, "work2026p", "dos", "cameron__dos_precinctreturns.txt"
)
DEFAULT_OUT = os.path.join(
    REPO_ROOT, "2026", "counties", "20260519__pa__primary__cameron__precinct.csv"
)

HEADER = ["county", "precinct", "office", "district", "party", "candidate", "votes"]
OFFICE_ORDER = ["Governor", "Lieutenant Governor", "U.S. House", "State House"]

OFFICES = {  # DOS office code -> office
    "GOV": "Governor",
    "LTG": "Lieutenant Governor",
    "USC": "U.S. House",
    "STS": "State Senate",
    "STH": "State House",
}

# DOS candidate number -> printed name (matches the statewide county anchor
# 2026/20260519__pa__primary__county.csv and the 2% sample report).
NAME_FIX = {
    "2026C0403": "Josh Shapiro",        # SHAPIRO, JOSHUA D
    "2026C0735": "Stacy Garrity",       # GARRITY, STACY LORRAINE
    "2026C0092": "Austin Davis",        # DAVIS, AUSTIN
    "2026C1021": "John Ventre",         # VENTRE, JOHN
    "2026C0645": "Jason Richey",        # RICHEY, JASON L
    "2026C0283": "Ray Bilger",          # BILGER, RAY
    "2026C0517": "Glenn GT Thompson",   # THOMPSON, GLENN WILLIAM JR
    "2026C0761": "Zachary Ware",        # WARE, ZACHARY R
    "2026C0218": "Martin T Causer",     # CAUSER, MARTIN T
}

# precinct code -> clean precinct name (repo's Cameron DOS-lineage list,
# same ten codes/names as the 2022 primary / 2022 general parsers).
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

# Statewide county anchor totals (2026/20260519__pa__primary__county.csv,
# CAMERON rows).  Used by --verify: per contest/party/candidate precinct sums.
ANCHOR_TOTALS = {
    ("Governor", "DEM"): {"Josh Shapiro": 182},
    ("Governor", "REP"): {"Stacy Garrity": 451},
    ("Lieutenant Governor", "DEM"): {"Austin Davis": 179},
    ("Lieutenant Governor", "REP"): {"John Ventre": 163, "Jason Richey": 277},
    ("U.S. House", "DEM"): {"Ray Bilger": 176},
    ("U.S. House", "REP"): {"Glenn GT Thompson": 453},
    ("State House", "DEM"): {"Zachary Ware": 173},
    ("State House", "REP"): {"Martin T Causer": 467},
}

# 2% statistical sample report (post-sample counts): the single sampled batch
# "West Shippen Township" must equal the Shippen West Precinct rows.  Used by
# --verify as a per-precinct spot check.
SAMPLE_REPORT_PRECINCT = "Shippen West Precinct"
SAMPLE_REPORT = {  # (office, party, candidate) -> post-sample Total Votes
    ("Governor", "DEM", "Josh Shapiro"): 48,
    ("Governor", "REP", "Stacy Garrity"): 140,
    ("Lieutenant Governor", "DEM", "Austin Davis"): 47,
    ("Lieutenant Governor", "REP", "John Ventre"): 54,
    ("Lieutenant Governor", "REP", "Jason Richey"): 86,
    ("U.S. House", "DEM", "Ray Bilger"): 46,
    ("U.S. House", "REP", "Glenn GT Thompson"): 140,
    ("State House", "DEM", "Zachary Ware"): 46,
    ("State House", "REP", "Martin T Causer"): 143,
}


def county_rows_from_dos(path: str) -> list[dict]:
    """Read a PA DOS 2026 primary certified precinct-returns slice (CSV, quoted
    fields), keep Cameron (county code 12)."""
    out = []
    with open(path, newline="", encoding="latin-1") as fh:
        for row in csv.reader(fh):
            if len(row) < 23 or row[0] != YEAR or row[1] != ELECTION_TYPE:
                continue
            if row[2] != COUNTY_CODE:
                continue
            if row[8] not in OFFICES:
                raise SystemExit(
                    "unexpected office code %r in %s" % (row[8], path)
                )
            out.append(
                {
                    "precinct_code": row[3],
                    "office_code": row[8],
                    "party": row[9],
                    "cand_no": row[10],
                    "votes": int(row[15]),
                    "district": row[5],
                }
            )
    return out


def cand_name(r: dict) -> str:
    return NAME_FIX.get(r["cand_no"], "%s %s" % (r["cand_no"], "UNKNOWN"))


def district_for(r: dict) -> str:
    office = OFFICES[r["office_code"]]
    if office in ("Governor", "Lieutenant Governor"):
        return ""
    return str(int(r["district"]))  # U.S. House / State Senate / State House


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
    out.sort(
        key=lambda x: (
            x["_code"],
            x["_oc"],
            OFFICE_ORDER.index(x["office"]),
            x["candidate"],
        )
    )
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


def run_checks(rows: list[dict]) -> tuple[int, int, list[str]]:
    """1) sum per (office, party, candidate) vs the county anchor;
    2) sum the Shippen West Precinct rows vs the 2% sample report.
    Returns (n_anchor_checks, n_sample_checks, problems)."""
    sums: dict[tuple, int] = {}
    sample_sums: dict[tuple, int] = {}
    for r in rows:
        key = (OFFICES[r["office_code"]], r["party"], cand_name(r))
        sums[key] = sums.get(key, 0) + r["votes"]
        if PRECINCT_NAMES[r["precinct_code"]] == SAMPLE_REPORT_PRECINCT:
            sample_sums[key] = sample_sums.get(key, 0) + r["votes"]
    problems = []
    n_anchor = 0
    for (office, party), cands in ANCHOR_TOTALS.items():
        for cand, total in cands.items():
            n_anchor += 1
            mine = sums.get((office, party, cand), 0)
            if mine != total:
                problems.append(
                    "%s [%s]: %s: precinct sum %d != county anchor %d"
                    % (office, party, cand, mine, total)
                )
    n_sample = 0
    for key, total in SAMPLE_REPORT.items():
        n_sample += 1
        mine = sample_sums.get(key, 0)
        if mine != total:
            problems.append(
                "2%% sample %s [%s]: %s: Shippen West sum %d != report %d"
                % (key[0], key[1], key[2], mine, total)
            )
    return n_anchor, n_sample, problems


def main(argv: list[str]) -> int:
    args = [a for a in argv if a != "--verify"]
    src = args[0] if args and os.path.exists(args[0]) else DEFAULT_SOURCE
    if not os.path.exists(src):
        print(
            "No Cameron 2026 primary DOS source found (tried %s)" % (args or DEFAULT_SOURCE),
            file=sys.stderr,
        )
        return 1
    rows = county_rows_from_dos(src)
    if not rows:
        print("No Cameron 2026 primary rows found in source %s" % src, file=sys.stderr)
        return 1
    missing_precincts = sorted({r["precinct_code"] for r in rows} - set(PRECINCT_NAMES))
    if missing_precincts:
        print("Unknown precinct codes: %s" % missing_precincts, file=sys.stderr)
        return 1

    out_rows = to_output_rows(rows)

    if "--verify" in argv:
        n_anchor, n_sample, problems = run_checks(rows)
        print(
            "verify: %d contest totals checked against the 2026 county anchor"
            % n_anchor
        )
        print(
            "verify: %d values checked against the 2%% sample report "
            "(%s)" % (n_sample, SAMPLE_REPORT_PRECINCT)
        )
        if problems:
            for p in problems:
                print("  MISMATCH " + p)
            return 1
        print("verify: all sums match both references")

    out_path = args[1] if len(args) > 1 else DEFAULT_OUT
    if out_path != "-":
        os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
        with open(out_path, "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(HEADER)
            w.writerows(out_rows)
        print("wrote %s (%d data rows) from %s" % (out_path, len(out_rows), src))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
