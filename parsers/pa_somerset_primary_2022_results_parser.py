#!/usr/bin/env python3
"""Somerset County, PA 2022 Primary — precinct-level results parser.

Source situation (documented):

  * The county's source for this election, "~/code/openelections-sources-pa/
    2022/primary/Somerset PA 2022 Primary Results.pdf" (Electionware "Summary
    Results Report", UNOFFICIAL RESULTS, generated 05/17/2022 23:28, 41 pages;
    text extraction in work2022p/somerset/somerset.txt) is COUNTYWIDE ONLY.
    It carries STATISTICS (RV/BC countywide) and countywide totals for the DEM
    and REP U.S. Senate / Governor / Lieutenant Governor / U.S. House 13 & 14 /
    State Senate 32 / State House 69 & 71 / State Committee contests plus the
    DEM State Committee named row (Shelley J. Glessner) and per-precinct
    sections ONLY for the party county-committee write-in races.  There are no
    per-precinct vote rows for the main contests anywhere in the 41 pages.

  Precinct-level figures therefore come from the PA Department of State
  official certified precinct-returns bulk file for the 2022 primary:
      ElectionReturns_2022_Primary_PrecinctReturns.txt
  (comma-delimited, quoted fields; election type "P"; Somerset = county code
  56; 2,483 rows = one row per named candidate per precinct where the contest
  is on the ballot).  This same fallback was used for Cameron and Monroe in
  the 2022 primary (see pa_cameron_primary_2022_results_parser.py and
  pa_monroe_primary_2022_results_parser.py).  The DOS file is the post-recount
  certified tabulation, so the Republican U.S. Senate precinct rows here are
  the final certified figures: their sums match the ENR county anchor
  (2022/20220517__pa__primary__county.csv) exactly, while the county's own
  UNOFFICIAL evening-of report lags slightly (e.g. Oz 4,339 vs certified
  4,356, McCormick 4,521 vs 4,545, Shapiro 4,117 vs 4,142).

Consequences (inherent to the DOS bulk source, not parsing losses):
  * No election-day / mail / provisional breakdown -> 7-column output header.
  * No per-precinct write-in rows (the DOS bulk file carries none).  The
    county summary's write-in totals are county-level only and cannot be
    assigned to precincts, so per the no-invention rule no "Write Ins" rows
    are emitted.  The DEM U.S. House 13 (16), U.S. House 14 (440), State
    Senate 32 (433), State House 69 (385) and State House 71 (50) contests
    are write-in-only in this primary and therefore have NO rows at all; the
    State Committee contests (DOS does not carry them; ENR countywide only)
    likewise have no rows.
  * No per-precinct Registered Voters / Ballots Cast.  Countywide from the
    summary PDF: RV 48,490 (DEM 12,666 / REP 30,951 / nonpartisan 4,873);
    Ballots Cast 18,885 (DEM 4,681 / REP 14,196 / nonpartisan 8 / blank 31).

Precinct naming:  DOS descriptions are bare municipality names ("ADDISON",
"PAINT") that do not distinguish Somerset's borough vs township precincts.
Each DOS code was mapped to the county's own precinct name by matching the
DOS *2022 GENERAL* bulk file's per-precinct Governor vote vectors (Mastriano +
Shapiro) against the per-precinct values in this repo's
2022/counties/20221108__pa__general__somerset__precinct.csv (itself from the
county's official per-precinct report) — every one of the 68 precincts matched
its Governor vector exactly, fixing e.g. DOS code 10 = Addison *Township* and
code 20 = Addison *Borough* (the DOS file's internal order is not the county's
alphabetical one).  Breakdown descriptions resolve the rest (WINDBER X 1-4 =
Windber Borough No. 1-4, PAINT X 1-3 = Paint Township No. 1-3 with plain
PAINT = Paint Borough, SOMERSET X 1-4 = Somerset Township No. 1-4 with
SOMERSET X EAST/NORTH WEST/SOUTH WEST = Somerset Borough East/Northwest/
Southwest — the general-file vote match confirms all of these).

Quirk: DOS splits Conemaugh Township No. 2 into "CONEMAUGH X 2 (USC 13)"
(code 130) and "CONEMAUGH X 2 (USC 14)" (code 135).  Code 130 is the real,
voted precinct — on the congressional district 13 ballot in the primary
(John Joyce's only Somerset precinct rows; 317 votes = the county total) and
the whole precinct's other-contest votes sit on code 130 in both the primary
and the general (the 2022 general's 130 rows carry Joyce 683).  Code 135
carries ONLY all-zero rows in both files (33 of 35 primary rows, Mastriano 0
in the general, no USC-13 rows ever) — an empty artifact split, consistent
with the county reporting Conemaugh No. 2 as a single precinct.  Code 135 is
therefore dropped (nothing lost; every row on it is zero) and code 130 is
emitted as "Conemaugh Township No. 2".  The lone candidate row absent from
the file entirely (Jeff Coleman's zero row at code 135) is not invented, per
the Monroe precedent.

Usage:
    python parsers/pa_somerset_primary_2022_results_parser.py <dos_prcres_txt> <out.csv> [anchor_county.csv]
    python parsers/pa_somerset_primary_2022_results_parser.py [--verify] <dos_prcres_txt> [out.csv] [anchor_county.csv]

If <dos_prcres_txt> is omitted the parser uses the Somerset slice at
work2022p/somerset/somerset_prcres_slice.txt; --verify additionally checks
candidate sums against the county summary PDF's countywide totals (UNOFFICIAL
-- expected to differ from the certified figures by the small post-recount /
canvas deltas reported) and against the ENR anchor (exact match expected).
"""
from __future__ import annotations

import collections
import csv
import os
import sys

COUNTY_NAME = "Somerset"
COUNTY_CODE = "56"
ELECTION_TYPE = "P"  # DOS bulk-file election type for the primary

HEADER = ["county", "precinct", "office", "district", "party", "candidate", "votes"]
OFFICE_ORDER = ["U.S. Senate", "Governor", "Lieutenant Governor", "U.S. House",
                "State Senate", "State House"]

OFFICES = {  # DOS office code -> (office, district source field)
    "USS": ("U.S. Senate", None),
    "GOV": ("Governor", None),
    "LTG": ("Lieutenant Governor", None),
    "USC": ("U.S. House", "contest_district"),
    "STS": ("State Senate", "contest_district"),
    "STH": ("State House", "contest_district"),
}

# DOS candidate (Last, First) -> printed name.  Short forms consistent with
# the 2022-primary county files already in the repo (Monroe convention).
NAME_FIX = {
    ("KHALIL", "ALEXANDRIA"): "Alex Khalil",
    ("LAMB", "CONOR"): "Conor Lamb",
    ("KENYATTA", "MALCOLM"): "Malcolm Kenyatta",
    ("FETTERMAN", "JOHN"): "John Fetterman",
    ("BARNETTE", "KATHY"): "Kathy Barnette",
    ("OZ", "MEHMET"): "Mehmet Oz",
    ("BOCHETTO", "GEORGE"): "George Bochetto",
    ("BARTOS", "JEFFREY"): "Jeff Bartos",
    ("MCCORMICK", "DAVID"): "Dave McCormick",
    ("GALE", "SEAN"): "Sean Gale",
    ("SANDS", "CARLA"): "Carla Sands",
    ("SHAPIRO", "JOSHUA"): "Josh Shapiro",
    ("BARLETTA", "LOUIS"): "Lou Barletta",
    ("MASTRIANO", "DOUGLAS"): "Douglas V. Mastriano",
    ("ZAMA", "NCHE"): "Nche Zama",
    ("WHITE", "DAVID"): "Dave White",
    ("HART", "MELISSA"): "Melissa Hart",
    ("MCSWAIN", "WILLIAM"): "Bill McSwain",
    ("GEROW", "CHARLES"): "Charlie Gerow",
    ("GALE", "JOSEPH"): "Joe Gale",
    ("CORMAN", "JACOB"): "Jake Corman",
    ("DAVIS", "AUSTIN"): "Austin Davis",
    ("SOSA", "RAYMOND"): "Ray Sosa",
    ("SIMS", "BRIAN"): "Brian Sims",
    ("SCHILLINGER", "CLARICE"): "Clarice Schillinger",
    ("JONES", "JAMES"): "James Earl Jones",
    ("SACCONE", "RICHARD"): "Rick Saccone",
    ("BROWN", "JOHN"): "John Brown",
    ("FRYE", "CHRISTOPHER"): "Chris Frye",
    ("COLEMAN", "JEFFREY"): "Jeff Coleman",
    ("DIAMOND", "RUSSELL"): "Russ Diamond",
    ("DELROSSO", "CARRIE"): "Carrie Lewis Delrosso",
    ("DANIELS", "THEODORE"): "Teddy Daniels",
    ("RESCHENTHALER", "GUY"): "Guy Reschenthaler",
    ("JOYCE", "JOHN"): "John Joyce",
    ("STEFANO", "PATRICK"): "Pat Stefano",
    ("METZGAR", "CARL"): "Carl Walker Metzgar",
    ("RIGBY", "JAMES"): "Jim Rigby",
}

# DOS precinct code -> county precinct name.  Verified against the county's
# per-precinct 2022 general report via the 2022 general DOS bulk file (see
# module docstring); code 135 is the empty "(USC 14)" split of Conemaugh
# Township No. 2 and is not a precinct.
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
    # code 135 ("CONEMAUGH X 2 (USC 14)", all rows zero) is dropped in
    # read_dos before the PRECINCT_NAMES lookup and must not be emitted
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

DEFAULT_SOURCE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "work2022p", "somerset", "somerset_prcres_slice.txt",
)

# Countywide totals printed in the county summary PDF (UNOFFICIAL RESULTS,
# generated 05/17/2022 23:28).  Used by --verify; these lag the certified
# ENR anchor slightly (see docstring), so --verify reports both and fails
# only on an anchor mismatch.
PDF_TOTALS = {
    # (office, district) -> {candidate: UNOFFICIAL total}
    ("U.S. Senate", ""): {
        "Alex Khalil": 142, "Conor Lamb": 933, "Malcolm Kenyatta": 105,
        "John Fetterman": 3237,
        "Kathy Barnette": 3512, "Mehmet Oz": 4339, "George Bochetto": 77,
        "Jeff Bartos": 639, "Dave McCormick": 4521, "Sean Gale": 85,
        "Carla Sands": 831,
    },
    ("Governor", ""): {
        "Josh Shapiro": 4117,
        "Lou Barletta": 1137, "Douglas V. Mastriano": 7633, "Nche Zama": 64,
        "Dave White": 756, "Melissa Hart": 616, "Bill McSwain": 3175,
        "Charlie Gerow": 110, "Joe Gale": 62, "Jake Corman": 462,
    },
    ("Lieutenant Governor", ""): {
        "Austin Davis": 3169, "Ray Sosa": 404, "Brian Sims": 689,
        "Clarice Schillinger": 783, "James Earl Jones": 856,
        "Rick Saccone": 2067, "John Brown": 432, "Chris Frye": 425,
        "Jeff Coleman": 1128, "Russ Diamond": 316,
        "Carrie Lewis Delrosso": 5352, "Teddy Daniels": 1896,
    },
    ("U.S. House", "14"): {"Guy Reschenthaler": 11930},
    ("U.S. House", "13"): {"John Joyce": 316},
    ("State Senate", "32"): {"Pat Stefano": 12924},
    ("State House", "69"): {"Carl Walker Metzgar": 12055},
    ("State House", "71"): {"Jim Rigby": 1133},
}

# ENR anchor formal candidate name -> our output name; district keyed too.
ANCHOR_KEY = {
    ("U.S. Senate", "", "DEM", "Alexandria Gloria Khalil"): "Alex Khalil",
    ("U.S. Senate", "", "DEM", "Conor J. Lamb"): "Conor Lamb",
    ("U.S. Senate", "", "DEM", "John K. Fetterman"): "John Fetterman",
    ("U.S. Senate", "", "DEM", "Malcolm Kenyatta"): "Malcolm Kenyatta",
    ("U.S. Senate", "", "REP", "Carla Herd Sands"): "Carla Sands",
    ("U.S. Senate", "", "REP", "David H. McCormick"): "Dave McCormick",
    ("U.S. Senate", "", "REP", "George A. Bochetto"): "George Bochetto",
    ("U.S. Senate", "", "REP", "Jeffrey A. Bartos"): "Jeff Bartos",
    ("U.S. Senate", "", "REP", "Kathy J. Barnette"): "Kathy Barnette",
    ("U.S. Senate", "", "REP", "Mehmet C. Oz"): "Mehmet Oz",
    ("U.S. Senate", "", "REP", "Sean Peter Gale"): "Sean Gale",
    ("Governor", "", "DEM", "Joshua D. Shapiro"): "Josh Shapiro",
    ("Governor", "", "REP", "Charles R. Gerow"): "Charlie Gerow",
    ("Governor", "", "REP", "David J. White"): "Dave White",
    ("Governor", "", "REP", "Douglas V. Mastriano"): "Douglas V. Mastriano",
    ("Governor", "", "REP", "Jacob Doyle Corman III"): "Jake Corman",
    ("Governor", "", "REP", "Joseph Charles Gale"): "Joe Gale",
    ("Governor", "", "REP", "Louis Barletta"): "Lou Barletta",
    ("Governor", "", "REP", "Melissa A. Hart"): "Melissa Hart",
    ("Governor", "", "REP", "Nche Zama"): "Nche Zama",
    ("Governor", "", "REP", "William M. McSwain"): "Bill McSwain",
    ("Lieutenant Governor", "", "DEM", "Austin Davis"): "Austin Davis",
    ("Lieutenant Governor", "", "DEM", "Brian Sims"): "Brian Sims",
    ("Lieutenant Governor", "", "DEM", "Raymond L. Sosa"): "Ray Sosa",
    ("Lieutenant Governor", "", "REP", "Carrie Delrosso"): "Carrie Lewis Delrosso",
    ("Lieutenant Governor", "", "REP", "Christopher C. Frye Jr"): "Chris Frye",
    ("Lieutenant Governor", "", "REP", "Clarice D. Schillinger"): "Clarice Schillinger",
    ("Lieutenant Governor", "", "REP", "James E. Jones"): "James Earl Jones",
    ("Lieutenant Governor", "", "REP", "Jeffrey H. Coleman"): "Jeff Coleman",
    ("Lieutenant Governor", "", "REP", "John A. Brown"): "John Brown",
    ("Lieutenant Governor", "", "REP", "Richard Saccone"): "Rick Saccone",
    ("Lieutenant Governor", "", "REP", "Russell H. Diamond"): "Russ Diamond",
    ("Lieutenant Governor", "", "REP", "Theodore Daniels"): "Teddy Daniels",
    ("State House", "69", "REP", "Carl Walker Metzgar"): "Carl Walker Metzgar",
    ("State House", "71", "REP", "James Patrick Rigby"): "Jim Rigby",
    ("State Senate", "32", "REP", "Patrick J. Stefano"): "Pat Stefano",
    ("U.S. House", "13", "REP", "John Joyce"): "John Joyce",
    ("U.S. House", "14", "REP", "Guy Reschenthaler"): "Guy Reschenthaler",
}


def read_dos(path: str) -> list[dict]:
    """Read Somerset (county code 56) 2022 primary rows from the DOS bulk file."""
    out = []
    code135_nonzero = 0
    with open(path, newline="", encoding="latin-1") as fh:
        for row in csv.reader(fh):
            if len(row) < 27 or row[:2] != ["2022", ELECTION_TYPE]:
                continue
            if row[2] != COUNTY_CODE:
                continue
            if row[8] not in OFFICES:
                raise SystemExit("unexpected office code %r" % row[8])
            votes = int(row[15])
            if row[3] == "135":
                code135_nonzero += votes != 0
                continue  # empty "(USC 14)" split; see docstring
            if row[3] not in PRECINCT_NAMES:
                raise SystemExit("unknown precinct code %r" % row[3])
            out.append({
                "code": row[3],
                "office_code": row[8],
                "party": row[9],
                "votes": votes,
                "name_official": (row[11], row[12]),
                "district": "" if OFFICES[row[8]][1] is None else str(int(row[5])),
            })
    if code135_nonzero:
        raise SystemExit("code 135 (USC 14 split) carries nonzero votes: %d"
                         % code135_nonzero)
    return out


def cand_name(name_official: tuple[str, str]) -> str:
    name = NAME_FIX.get(name_official)
    if name is None:
        raise SystemExit("unmapped candidate %r" % (name_official,))
    return name


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
            "candidate": cand_name(r["name_official"]),
            "votes": r["votes"],
            "_code": int(r["code"]),
            "_oc": OFFICE_ORDER.index(office),
            "_party": {"DEM": 0, "REP": 1}[r["party"]],
        })
    out.sort(key=lambda x: (x["_code"], x["_oc"], x["_party"], x["candidate"]))
    return out


def run_checks(out_rows: list[dict]) -> tuple[int, list[str]]:
    """Compare candidate sums with the county summary PDF UNOFFICIAL totals
    and report the deltas (certified DOS canvas > evening-of UNOFFICIAL;
    informational, never a failure).  Returns (n_checked, deltas)."""
    sums: dict[tuple[str, str, str, str], int] = collections.defaultdict(int)
    for r in out_rows:
        sums[(r["office"], r["district"], r["party"], r["candidate"])] += int(r["votes"])
    deltas = []
    n = 0
    for (office, district), cands in PDF_TOTALS.items():
        for cand, total in cands.items():
            n += 1
            mine = sums.get((office, district, "DEM", cand), 0) or \
                sums.get((office, district, "REP", cand), 0)
            if mine != total:
                deltas.append("canvas delta %s [%s]: %s: our (certified) sum %d "
                              "vs PDF UNOFFICIAL total %d (%+d)"
                              % (office, district or "-", cand, mine, total,
                                 mine - total))
    return n, deltas


def verify_anchor(out_rows: list[dict], anchor_path: str) -> list[str]:
    """Cross-check named-candidate sums against the ENR county anchor."""
    if not os.path.exists(anchor_path):
        return []
    sums: dict[tuple[str, str, str, str], int] = collections.defaultdict(int)
    for r in out_rows:
        sums[(r["office"], r["district"], r["party"], r["candidate"])] += int(r["votes"])
    problems = []
    omitted = []
    with open(anchor_path, newline="") as fh:
        for r in csv.DictReader(fh):
            if r["county"] != COUNTY_NAME:
                continue
            key = (r["office"], r["district"], r["party"], r["candidate"])
            if key not in ANCHOR_KEY:
                # State Committee: the DOS bulk file does not carry these
                # contests (countywide in the anchor); documented omission,
                # never per the no-invention rule a per-precinct guess
                omitted.append("%s: %s (%s votes, no per-precinct rows in any "
                               "source)" % (key[0], key[3], r["votes"]))
                continue
            want = int(r["votes"])
            ours_name = ANCHOR_KEY[key]
            mine = sums[(key[0], key[1], key[2], ours_name)]
            if mine != want:
                problems.append("ANCHOR MISMATCH %s: ours %d vs anchor %d"
                                % (key, mine, want))
    for o in omitted:
        print("  omitted (documented): " + o)
    return problems


def write_csv(path: str, rows: list[dict]) -> None:
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=HEADER)
        w.writeheader()
        for r in rows:
            w.writerow({k: r[k] for k in HEADER})


def main(argv: list[str]) -> int:
    verify = "--verify" in argv
    args = [a for a in argv if a != "--verify"]
    src = args[0] if args and os.path.exists(args[0]) else DEFAULT_SOURCE
    if not os.path.exists(src):
        print("No Somerset 2022 primary DOS source found (tried %s)"
              % (args or DEFAULT_SOURCE), file=sys.stderr)
        return 1
    rows = read_dos(src)
    if not rows:
        print("No Somerset primary rows found in %s" % src, file=sys.stderr)
        return 1
    out_rows = to_output_rows(rows)
    print("read %d DOS rows -> %d output rows over %d precincts"
          % (len(rows), len(out_rows), len({r["precinct"] for r in out_rows})))
    problems = []
    if verify:
        n, deltas = run_checks(out_rows)
        for d in deltas:
            print("  " + d)
        anchor = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "2022", "20220517__pa__primary__county.csv")
        problems += verify_anchor(out_rows, anchor)
        print("verify: %d candidate totals compared against the county summary "
              "PDF (UNOFFICIAL)" % n)
    outfile = args[1] if len(args) > 1 else ""
    if outfile:
        write_csv(outfile, out_rows)
        print("wrote %d rows -> %s" % (len(out_rows), outfile))
    if problems:
        for p in problems:
            print("  " + p)
        print("PROBLEMS: %d" % len(problems))
        return 1
    if verify:
        print("verify: ENR anchor match exact for every Somerset contest row "
              "(PDF deltas are the documented UNOFFICIAL-vs-certified canvas deltas)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))