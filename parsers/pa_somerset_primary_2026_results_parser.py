#!/usr/bin/env python3
"""Somerset County, PA 2026 General Primary (2026-05-19) precinct results parser.

Source situation (documented):

  * Somerset County has no per-precinct county source for the 2026 primary in
    ~/code/openelections-sources-pa/2026/primary/ — the only Somerset-related
    file is "Somerset County 2026 Primary Official Precinct Returns (PA PA
    DOS).csv", which is itself a slice of the statewide PA Department of State
    certified precinct-returns bulk export (it carries Delaware (county code
    23) and Somerset (code 56) rows only; its Somerset portion is row-for-row
    identical to the repo slice at work2026p/dos/somerset__dos_precinctreturns.txt).
    So precinct-level figures come entirely from the PA DOS certified
    precinct-returns bulk file for the 2026 primary (fallback per the brief;
    county code 56, election type "P", 690 rows = 69 precinct x 10
    named-candidate rows).  DOS is the post-canvas certified tabulation, so
    sums here match the countywide anchor 2026/20260519__pa__primary__county.csv
    exactly.

Consequences (inherent to the DOS bulk source, not parsing losses):
  * No election-day / mail / provisional / absentee breakdown -> plain
    7-column output header.
  * No per-precinct write-in rows exist in the DOS bulk file; per the
    no-invention rule no "Write Ins"/"Write-In Totals" rows are emitted.
  * No per-precinct Registered Voters / Ballots Cast rows; none emitted.
  * The DOS bulk file does not carry State Committee contests, so the three
    State Committee contests in the countywide anchor (DEM: Shelley J.
    Glessner; REP: Fiore/Otten/Harr/Darr/Stewart) have no per-precinct rows
    in any source and are omitted (documented omission, 2022 Somerset
    precedent).

Quirk (recurring): DOS splits Conemaugh Township No. 2 into "CONEMAUGH X 2
(USC 13)" (code 130) and "CONEMAUGH X 2 (USC 14)" (code 135).  As in 2022
(see pa_somerset_primary_2022_results_parser.py), code 135 is an empty
artifact split: its rows are ALL ZERO in 2020/2022 and here in 2026 (10 of 10
rows zero), while the whole precinct's real votes — on both the district-13
ballot (Farnham/Joyce, the only Joyce rows in the county; 88/166 = exactly the
county totals in the anchor) and every other contest — sit on code 130.  The
parser raises if any code-135 row is nonzero, drops those rows (nothing lost;
every value zero), and emits code 130 as "Conemaugh Township No. 2".

Precinct naming: DOS descriptions are bare municipality names that do not
distinguish Somerset's borough vs township precincts.  The DOS code -> county
precinct-name mapping inherited here was verified for 2022 by matching each
code's Governor vote vector against the county's own per-precinct 2022
general report (see the 2022 parser's docstring; e.g. code 10 = Addison
Township, code 20 = Addison Borough); the same DOS codes recur in 2026, and
breakdown descriptions resolve the rest (WINDBER X 1-4 = Windber Borough
No. 1-4, PAINT X 1-3 = Paint Township No. 1-3 with plain PAINT = Paint
Borough, SOMERSET X 1-4 = Somerset Township No. 1-4 with SOMERSET X
EAST/NORTH WEST/SOUTH WEST = Somerset Borough East/Northwest/Southwest).

Usage:
    uv run python parsers/pa_somerset_primary_2026_results_parser.py \
        <dos_prcres_txt> <out_csv> [--verify] [--crosscheck <source_csv>]

Defaults: <dos_prcres_txt> = work2026p/dos/somerset__dos_precinctreturns.txt.
--verify  : checks candidate sums against the 2026 countywide anchor
            (exact match expected; State Committee rows only are omitted).
--crosscheck <path>: verifies that this slice's Somerset rows are
            row-for-row identical to the supplied slice of the statewide
            DOS export (e.g. the "Somerset County 2026 Primary Official
            Precinct Returns (PA DOS).csv" source copy).
"""
from __future__ import annotations

import collections
import csv
import os
import sys

COUNTY_NAME = "Somerset"
COUNTY_CODE = "56"
ELECTION_YEAR = "2026"
ELECTION_TYPE = "P"  # DOS bulk-file election type for the primary

HEADER = ["county", "precinct", "office", "district", "party", "candidate", "votes"]
OFFICE_ORDER = ["Governor", "Lieutenant Governor", "U.S. House",
                "State Senate", "State House"]

OFFICES = {  # DOS office code -> (office, district source field)
    "GOV": ("Governor", None),
    "LTG": ("Lieutenant Governor", None),
    "USC": ("U.S. House", "contest_district"),
    "STS": ("State Senate", "contest_district"),
    "STH": ("State House", "contest_district"),
}

# DOS candidate (Last, First, Middle) -> printed name (anchor-compatible short
# forms, as in the other 2026 primary county files).
NAME_FIX = {
    ("SHAPIRO", "JOSHUA", "D"): "Josh Shapiro",
    ("GARRITY", "STACY", "LORRAINE"): "Stacy Garrity",
    ("DAVIS", "AUSTIN", ""): "Austin Davis",
    ("VENTRE", "JOHN", ""): "John Ventre",
    ("RICHEY", "JASON", "L"): "Jason Richey",
    ("FARNHAM", "ELIZABETH", "RHOADS"): "Beth Farnham",
    ("JOYCE", "JOHN", ""): "John Joyce",
    ("BRADSTOCK", "DAVID", "ALAN"): "Alan Bradstock",
    ("RESCHENTHALER", "GUY", "LORIN"): "Guy Reschenthaler",
    ("STEFANO", "PATRICK", "J"): "Pat Stefano",
    ("COCHRAN", "HARRY", "YOUNG"): "Harry Young Cochran",
    ("METZGAR", "CARL", "WALKER"): "Carl Walker Metzgar",
    ("RIGBY", "JAMES", "PATRICK"): "Jim Rigby",
}

# DOS precinct code -> county precinct name.  Verified for 2022 via the
# county's own per-precinct general report (see module docstring); code 135
# ("CONEMAUGH X 2 (USC 14)", all rows zero) is dropped in read_dos before the
# PRECINCT_NAMES lookup and must not be emitted.
PRECINCT_NAMES = {
    "10": "Addison Township",
    "20": "Addison Borough",
    "30": "Allegheny Township",
    "40": "Benson Borough",
    "50": "Berlin Borough",
    "60": "Black Township",
    "70": "Boswell Borough",
    "80": "Brothersvalley Township",
    "90": "Callimont Borough",
    "100": "Casselman Borough",
    "110": "Central City Borough",
    "120": "Conemaugh Township No. 1",
    "130": "Conemaugh Township No. 2",
    # code 135 (all-zero "(USC 14)" split) is dropped in read_dos before the
    # PRECINCT_NAMES lookup and must not be emitted
    "140": "Conemaugh Township No. 3",
    "150": "Conemaugh Township No. 4",
    "160": "Conemaugh Township No. 5",
    "170": "Confluence Borough",
    "180": "Elk Lick Township",
    "190": "Fairhope Township",
    "200": "Garrett Borough",
    "210": "Greenville Township",
    "220": "Hooversville Borough",
    "230": "Indian Lake Borough",
    "240": "Jefferson Township",
    "250": "Jenner Township No. 1",
    "260": "Jenner Township No. 2",
    "270": "Jenner Township No. 3",
    "280": "Jennerstown Borough",
    "290": "Larimer Township",
    "300": "Lincoln Township",
    "310": "Lower Turkeyfoot Township",
    "320": "Meyersdale Borough",
    "330": "Middlecreek Township",
    "340": "Milford Township",
    "350": "New Baltimore Borough",
    "360": "New Centerville Borough",
    "370": "Northampton Township",
    "380": "Ogle Township",
    "390": "Paint Township No. 1",
    "400": "Paint Township No. 2",
    "410": "Paint Township No. 3",
    "420": "Paint Borough",
    "430": "Quemahoning Township",
    "440": "Rockwood Borough",
    "450": "Salisbury Borough",
    "460": "Seven Springs Borough",
    "470": "Shade Township No. 1",
    "480": "Shade Township No. 2",
    "490": "Shade Township No. 3",
    "500": "Shanksville Borough",
    "510": "Somerset Township No. 1",
    "520": "Somerset Township No. 2",
    "530": "Somerset Township No. 3",
    "540": "Somerset Township No. 4",
    "550": "Somerset Borough East",
    "560": "Somerset Borough Northwest",
    "570": "Somerset Borough Southwest",
    "580": "Southampton Township",
    "590": "Stonycreek Township",
    "600": "Stoystown Borough",
    "610": "Summit Township",
    "620": "Upper Turkeyfoot Township",
    "630": "Ursina Borough",
    "640": "Wellersburg Borough",
    "650": "Windber Borough No. 1",
    "660": "Windber Borough No. 2",
    "670": "Windber Borough No. 3",
    "680": "Windber Borough No. 4",
}

DROP_CODES = {"135": "Conemaugh Township No. 2 (USC 14) all-zero artifact split"}

DEFAULT_SOURCE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "work2026p", "dos", "somerset__dos_precinctreturns.txt")

# 2026 countywide anchor candidate name -> our output name (district keyed;
# State Committee contests, which DOS does not carry, are not listed here).
ANCHOR_KEY = {
    ("Governor", "", "DEM", "JOSH SHAPIRO"): "Josh Shapiro",
    ("Governor", "", "REP", "STACY GARRITY"): "Stacy Garrity",
    ("Lieutenant Governor", "", "DEM", "AUSTIN DAVIS"): "Austin Davis",
    ("Lieutenant Governor", "", "REP", "JOHN VENTRE"): "John Ventre",
    ("Lieutenant Governor", "", "REP", "JASON RICHEY"): "Jason Richey",
    ("U.S. House", "13", "DEM", "BETH FARNHAM"): "Beth Farnham",
    ("U.S. House", "13", "REP", "JOHN JOYCE"): "John Joyce",
    ("U.S. House", "14", "DEM", "ALAN BRADSTOCK"): "Alan Bradstock",
    ("U.S. House", "14", "REP", "GUY RESCHENTHALER"): "Guy Reschenthaler",
    ("State Senate", "32", "REP", "PAT STEFANO"): "Pat Stefano",
    ("State Senate", "32", "REP", "HARRY YOUNG COCHRAN"): "Harry Young Cochran",
    ("State House", "69", "REP", "CARL WALKER METZGAR"): "Carl Walker Metzgar",
    ("State House", "71", "REP", "JIM RIGBY"): "Jim Rigby",
}
ANCHOR_PARTY = {"DEM": "Democratic", "REP": "Republican"}
STATE_COMMITTEE_PREFIX = "Member of "


def read_dos(path: str) -> list[dict]:
    """Read Somerset (county code 56) 2026 primary rows from the DOS bulk file."""
    out = []
    code135_nonzero = 0
    with open(path, newline="", encoding="latin-1") as fh:
        for row in csv.reader(fh):
            if len(row) < 23 or row[:2] != [ELECTION_YEAR, ELECTION_TYPE]:
                continue
            if row[2] != COUNTY_CODE:
                continue
            if row[8] not in OFFICES:
                raise SystemExit("unexpected office code %r" % row[8])
            key = (row[11], row[12], row[13])
            if key not in NAME_FIX:
                raise SystemExit("unmapped candidate %r (suffix %r)"
                                 % (key, row[14]))
            votes = int(row[15])
            if row[3] in DROP_CODES:
                code135_nonzero += votes != 0
                continue  # empty "(USC 14)" split; see module docstring
            if row[3] not in PRECINCT_NAMES:
                raise SystemExit("unknown precinct code %r" % row[3])
            out.append({
                "code": row[3],
                "office_code": row[8],
                "party": row[9],
                "votes": votes,
                "candidate": NAME_FIX[key],
                "district": "" if OFFICES[row[8]][1] is None else str(int(row[5])),
            })
    if code135_nonzero:
        raise SystemExit(
            "code 135 (USC 14 split) carries nonzero votes: %d — the "
            "all-zero artifact-split handling documented in the docstring no "
            "longer holds; revise the parser" % code135_nonzero)
    return out


def to_output_rows(rows: list[dict]) -> list[dict]:
    out = []
    for r in rows:
        office, _ = OFFICES[r["office_code"]]
        out.append({
            "county": COUNTY_NAME,
            "precinct": PRECINCT_NAMES[r["code"]],
            "office": office,
            "district": r["district"],
            "party": r["party"],
            "candidate": r["candidate"],
            "votes": r["votes"],
            "_code": int(r["code"]),
            "_oc": OFFICE_ORDER.index(office),
            "_party": {"DEM": 0, "REP": 1}[r["party"]],
        })
    out.sort(key=lambda x: (x["_code"], x["_oc"], x["_party"], x["candidate"]))
    return out


def sums_for(rows: list[dict]) -> dict[tuple[str, str, str, str], int]:
    """Per (office, district, party, candidate) totals of output rows."""
    sums: dict[tuple[str, str, str, str], int] = collections.defaultdict(int)
    for r in rows:
        sums[(r["office"], r["district"], r["party"], r["candidate"])] += int(r["votes"])
    return sums


def verify_anchor(rows: list[dict], sums: dict, anchor_path: str) -> list[str]:
    """Cross-check named-candidate sums against the 2026 countywide anchor."""
    if not os.path.exists(anchor_path):
        return ["2026 anchor not found at %s" % anchor_path]
    problems = []
    omitted = []
    seen = 0
    with open(anchor_path, newline="") as fh:
        for r in csv.DictReader(fh):
            if r["county"] != COUNTY_NAME.upper():
                continue
            party = {v: k for k, v in ANCHOR_PARTY.items()}.get(r["party"], r["party"])
            key = (r["office"], r["district"], party, r["candidate"])
            want = int(r["votes"])
            if r["office"].startswith(STATE_COMMITTEE_PREFIX):
                omitted.append("%s %s %s (%d votes): DOS bulk file carries no "
                               "State Committee contests — documented omission"
                               % (key[0], key[2], key[3], want))
                continue
            if key not in ANCHOR_KEY:
                problems.append("ANCHOR candidate without a mapping: %r" % (key,))
                continue
            ours_name = ANCHOR_KEY[key]
            mine = sums.get((key[0], key[1], key[2], ours_name), 0)
            seen += 1
            if mine != want:
                problems.append(
                    "ANCHOR MISMATCH %s %s [%s] %s: ours %d vs anchor %d"
                    % (key[0], key[1] or "-", key[2], ours_name, mine, want))
    for o in omitted:
        print("  omitted (documented): " + o)
    if seen != len(ANCHOR_KEY):
        problems.append("anchor check saw %d rows but ANCHOR_KEY maps %d"
                        % (seen, len(ANCHOR_KEY)))
    # every output candidate must have been anchored
    ours_to_anchor = {v: k for k, v in ANCHOR_KEY.items()}
    for (office, district, party, cand) in sorted(sums):
        anchor_key = ours_to_anchor.get(cand)
        if anchor_key is None or anchor_key[:3] != (office, district, party):
            problems.append("output candidate with no anchor counterpart: %s"
                            % ((office, district, party, cand),))
    return problems


def crosscheck(path: str, problems: list[str]) -> int:
    """Row-for-row check of this slice's Somerset rows against another slice
    of the statewide DOS export (e.g. the source repo's Somerset copy)."""
    wanted = set()
    with open(path, newline="", encoding="utf-8-sig") as fh:
        for row in csv.reader(fh):
            if len(row) > 23 and row[:2] == [ELECTION_YEAR, ELECTION_TYPE] \
                    and row[2] == COUNTY_CODE:
                wanted.add(tuple(row))
    own = set()
    with open(DEFAULT_SOURCE, newline="", encoding="latin-1") as fh:
        for row in csv.reader(fh):
            if row[:2] != [ELECTION_YEAR, ELECTION_TYPE] or row[2] != COUNTY_CODE:
                continue
            own.add(tuple(row))
    n = len(own)
    problems += ["%r carries %d Somerset rows, we have %d" % (path, len(wanted), n)] \
        if len(wanted) != n else []
    problems += ["rows only in our slice: %r" % (list(own - wanted)[:5])] \
        if own - wanted else []
    problems += ["rows only in %r: %r" % (path, list(wanted - own)[:5])] \
        if wanted - own else []
    return n


def write_csv(path: str, rows: list[dict]) -> None:
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=HEADER)
        w.writeheader()
        for r in rows:
            w.writerow({k: r[k] for k in HEADER})


def main(argv: list[str]) -> int:
    flags = [a for a in argv if a.startswith("--")]
    args = [a for a in argv if not a.startswith("--")]
    cross_arg = None
    if "--crosscheck" in flags:
        idx = argv.index("--crosscheck")
        cross_arg = argv[idx + 1] if idx + 1 < len(argv) else None
        if not cross_arg or cross_arg.startswith("--"):
            sys.exit("--crosscheck needs a source-csv path argument")
        args = [a for a in args if a != cross_arg]
    src = args[0] if args and os.path.exists(args[0]) else DEFAULT_SOURCE
    if not os.path.exists(src):
        print("No Somerset 2026 primary DOS source found (tried %s)"
              % (args or DEFAULT_SOURCE), file=sys.stderr)
        return 1
    rows = read_dos(src)
    if not rows:
        print("No Somerset 2026 primary rows found in %s" % src, file=sys.stderr)
        return 1
    out_rows = to_output_rows(rows)
    sums = sums_for(out_rows)
    print("read %d DOS rows -> %d output rows over %d precincts"
          % (len(rows), len(out_rows), len({r["precinct"] for r in out_rows})))
    problems = []
    if "--crosscheck" in flags:
        n = crosscheck(cross_arg, problems)
        print("crosscheck: %d Somerset rows compared against %r" % (n, cross_arg))
    if "--verify" in flags:
        anchor = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "2026", "20260519__pa__primary__county.csv")
        problems += verify_anchor(out_rows, sums, anchor)
    outfile = args[1] if len(args) > 1 else ""
    if outfile:
        write_csv(outfile, out_rows)
        print("wrote %d rows -> %s" % (len(out_rows), outfile))
    if problems:
        for p in problems:
            print("  " + p)
        print("PROBLEMS: %d" % len(problems))
        return 1
    if "--verify" in flags:
        print("verify: 2026 anchor match exact for every Somerset contest row")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
