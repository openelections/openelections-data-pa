#!/usr/bin/env python3
"""Sullivan County, PA 2026 Primary — precinct + countywide results parser.

Source situation (documented):
  * Sullivan has NO per-precinct machine report for the 2026 primary.  The
    county's own source, "~/code/openelections-sources-pa/2026/primary/
    Sullivan Summary+Primary+Official+Results+5.19.26.pdf" (Statement of
    Votes Cast by Geography, official results, 12 pages), is:
      - pages 1-4: COUNTYWIDE totals only ("Precinct: All"), plus the 28
        Democratic County Committee F/M single-precinct slots;
      - pages 5-12: per-precinct handwritten DEM write-in tally sheets.
    It is an unsearchable scan (pdftotext yields only form feeds), so pages
    1-4 were transcribed from 150-dpi page renders
    (work2026p/sullivan/sullivan_summary_transcribed.txt — every row read
    off the page image, ED+MI+PR == votes and Total rows cross-checked).
  * Per-precinct figures therefore come from the PA DOS official certified
    precinct-returns fallback: the Sullivan slice (county code 57, election
    type "P") of the statewide 2026 primary certified precinct-returns
    export, kept at work2026p/dos/sullivan__dos_precinctreturns.txt
    (120 rows = 15 precincts x 8 named-candidate contest rows).
  * Per the no-invention rule the DOS quirks carry through to the precinct
    file: no election-day/mail/provisional breakdown (7-column header), no
    per-precinct write-in rows, no per-precinct Registered Voters / Ballots
    Cast, and no State Committee rows.  The pages 5-12 write-in tally sheets
    COULD supply per-precinct write-ins, but they are hand tallies that the
    certified summary does not reconcile per precinct, so they are not
    merged into the precinct file.
  * The countywide summary PDF supplies 20260519__pa__primary__sullivan__
    county.csv: Governor/LG/U.S. House/State House incl. "Write-In Totals"
    rows with ED/MI/provisional breakdowns, and Member of the Democratic /
    Republican State Committee.  The 28 Democratic County Committee F/M
    slots are single-precinct local party offices (one contest per precinct,
    all named candidates are committee write-ins recorded on the hand
    tally sheets) and are OMITTED, following the 2026 sibling precedent
    (monroe committeeperson races) and Sullivan's own 2024 parser.
  * Contests covered: Governor (DEM/REP), Lieutenant Governor (DEM/REP x2),
    U.S. House 9 (DEM/REP), State House 84 (DEM row = write-ins only; REP
    Joe Hamm), Member of the Democratic State Committee (write-ins only),
    Member of the Republican State Committee (Keith Pretti + write-ins).

Precinct codes/naming: DOS names + qualifier + split — 'CHERRY' (code 10),
'CHERRY P BERNICE' ('P'/precinct split, code 20 -> the county's 'Bernice
Precinct'), 'COLLEY'/'COLLEY P LOPEZ' (-> 'Lopez Precinct'), and the other
municipalities.  Two rows print plain 'LAPORTE' (codes 130 and 140): 130 =
Laporte Township, 140 = Laporte Borough — the county's tally sheets list
'Laporte Twp' / 'Laporte Boro', the repo's 2022/2024 Sullivan files use
'Laporte Township'/'Laporte Borough', and the assignment was cross-checked
against per-precinct magnitudes in the 2024 primary SOVC (Trump: Twp 79 /
Boro 32) and the 2022/2024 repo files: in every year the larger Republican
ratio belongs to the Township, matching code 130 (GOP Gov 78 / 29 here).
Names follow the repo precedent list (2020/2022/2024 Sullivan files).

Usage:
    uv run python parsers/pa_sullivan_primary_2026_results_parser.py \
        [dos_returns_csv] [summary_transcribed_txt] [out_precinct_csv] [out_county_csv]
    ... with --verify appended to check sums against the county anchor.

Defaults:
    dos_returns_csv = work2026p/dos/sullivan__dos_precinctreturns.txt
    summary_txt     = work2026p/sullivan/sullivan_summary_transcribed.txt
    precinct out    = 2026/counties/20260519__pa__primary__sullivan__precinct.csv
    county out      = 2026/counties/20260519__pa__primary__sullivan__county.csv
"""
from __future__ import annotations

import csv
import os
import re
import sys

COUNTY_NAME = "Sullivan"
COUNTY_CODE = "57"
YEAR = "2026"
ELECTION_TYPE = "P"

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_DOS = os.path.join(
    REPO_ROOT, "work2026p", "dos", "sullivan__dos_precinctreturns.txt"
)
DEFAULT_SUMMARY = os.path.join(
    REPO_ROOT, "work2026p", "sullivan", "sullivan_summary_transcribed.txt"
)
DEFAULT_PRECINCT_OUT = os.path.join(
    REPO_ROOT, "2026", "counties", "20260519__pa__primary__sullivan__precinct.csv"
)
DEFAULT_COUNTY_OUT = os.path.join(
    REPO_ROOT, "2026", "counties", "20260519__pa__primary__sullivan__county.csv"
)

PRECINCT_HEADER = ["county", "precinct", "office", "district", "party", "candidate", "votes"]
# The counties/ countywide CSVs omit the precinct column (anchor-style rows;
# see 2026/counties/20260519__pa__primary__bedford__county.csv precedent).
COUNTY_HEADER = ["county", "office", "district", "party", "candidate", "votes",
                 "election_day", "mail", "provisional"]

OFFICES = {  # DOS office code -> office
    "GOV": "Governor",
    "LTG": "Lieutenant Governor",
    "USC": "U.S. House",
    "STS": "State Senate",
    "STH": "State House",
}
OFFICE_ORDER = ["Governor", "Lieutenant Governor", "U.S. House", "State House",
                "Member of Democratic State Committee",
                "Member of Republican State Committee"]

# DOS candidate number -> printed name (as the county's summary prints them;
# matches the statewide county anchor 2026/20260519__pa__primary__county.csv).
NAME_FIX = {
    "2026C0403": "Josh Shapiro",      # SHAPIRO, JOSHUA D
    "2026C0735": "Stacy Garrity",     # GARRITY, STACY LORRAINE
    "2026C0092": "Austin Davis",      # DAVIS, AUSTIN
    "2026C1021": "John Ventre",       # VENTRE, JOHN
    "2026C0645": "Jason Richey",      # RICHEY, JASON L
    "2026C0340": "Rachel Wallace",    # WALLACE, RACHEL LILLIAN
    "2026C0454": "Dan Meuser",        # MEUSER, DANIEL PHILIP
    "2026C0234": "Joe Hamm",          # HAMM, JOSEPH D
}

# DOS precinct code -> clean precinct name (repo 2020/2022/2024 precedent list).
PRECINCT_NAMES = {
    "10": "Cherry Township",
    "20": "Bernice Precinct",
    "30": "Colley Township",
    "40": "Lopez Precinct",
    "50": "Davidson Township",
    "60": "Dushore Borough",
    "70": "Eagles Mere Borough",
    "80": "Elkland Township",
    "90": "Forks Township",
    "100": "Forksville Borough",
    "110": "Fox Township",
    "120": "Hillsgrove Township",
    "130": "Laporte Township",
    "140": "Laporte Borough",
    "150": "Shrewsbury Township",
}

# Summary-transcription contest header -> (office, district).  Only offices
# emitted to the county CSV; "Democratic County Committee*" is deliberately
# unmapped (omitted — see docstring).
SUMMARY_CONTESTS = {
    "GOVERNOR DEM": ("Governor", "", "DEM"),
    "GOVERNOR REP": ("Governor", "", "REP"),
    "LIEUTENANT GOVERNOR DEM": ("Lieutenant Governor", "", "DEM"),
    "LIEUTENANT GOVERNOR REP": ("Lieutenant Governor", "", "REP"),
    "REPRESENTATIVE IN CONGRESS DEM": ("U.S. House", "9", "DEM"),
    "REPRESENTATIVE IN CONGRESS REP": ("U.S. House", "9", "REP"),
    "REPRESENTATIVE IN THE GENERAL ASSEMBLY DEM": ("State House", "84", "DEM"),
    "REPRESENTATIVE IN THE GENERAL ASSEMBLY REP": ("State House", "84", "REP"),
    "MEMBER OF THE DEMOCRATIC STATE COMMITTEE": (
        "Member of Democratic State Committee", "", "DEM"),
    "MEMBER OF THE REPUBLICAN STATE COMMITTEE": (
        "Member of Republican State Committee", "", "REP"),
}

# Statewide county anchor totals (2026/20260519__pa__primary__county.csv,
# SULLIVAN rows) — named candidates only; the anchor carries no write-ins.
# PRECINCT anchors are the keys the DOS slice must reproduce (Governor/LG/
# U.S. House/State House — DOS bulk carries no State Committee); the county
# CSV additionally carries the two State Committee contests.
ANCHOR_PRECINCT = {
    ("Governor", "DEM", ""): {"Josh Shapiro": 377},
    ("Governor", "REP", ""): {"Stacy Garrity": 711},
    ("Lieutenant Governor", "DEM", ""): {"Austin Davis": 359},
    ("Lieutenant Governor", "REP", ""): {"John Ventre": 246, "Jason Richey": 432},
    ("U.S. House", "DEM", "9"): {"Rachel Wallace": 356},
    ("U.S. House", "REP", "9"): {"Dan Meuser": 719},
    ("State House", "REP", "84"): {"Joe Hamm": 746},
}
ANCHOR_TOTALS = {
    **ANCHOR_PRECINCT,
    ("Member of Republican State Committee", "REP", ""): {"Keith Pretti": 723},
}


# ---------------------------------------------------------------- DOS slice --
def precinct_rows_from_dos(path: str) -> list[dict]:
    """Read the Sullivan slice of the PA DOS 2026 primary certified
    precinct-returns export (CSV, quoted fields)."""
    out = []
    with open(path, newline="", encoding="latin-1") as fh:
        for row in csv.reader(fh):
            if len(row) < 23 or row[0] != YEAR or row[1] != ELECTION_TYPE:
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
                    "cand_no": row[10],
                    "votes": int(row[15]),
                    "district": row[5],
                }
            )
    return out


def cand_name(r: dict) -> str:
    return NAME_FIX.get(r["cand_no"], "UNKNOWN %s" % r["cand_no"])


def district_for(r: dict) -> str:
    office = OFFICES[r["office_code"]]
    if office in ("Governor", "Lieutenant Governor"):
        return ""
    return str(int(r["district"]))  # U.S. House / State Senate / State House


def to_precinct_rows(rows: list[dict]) -> list[list]:
    out = []
    order = {"Governor": 0, "Lieutenant Governor": 1, "U.S. House": 2, "State House": 3}
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
            }
        )
    out.sort(
        key=lambda x: (x["_code"], order[x["office"]], x["candidate"])
    )
    return [
        [o["county"], o["precinct"], o["office"], o["district"],
         o["party"], o["candidate"], str(o["votes"])]
        for o in out
    ]


# ------------------------------------------------------------------- summary --
def county_rows_from_summary(path: str) -> list[dict]:
    """Read the transcribed countywide summary (tab-separated
    candidate / votes / ED / MI / PR under ## <contest header> blocks)."""
    out = []
    contest = None
    contest_n = 0  # keep source order within a contest
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.rstrip("\n")
            if not line or line.startswith("#"):
                m = re.match(r"^##\s+(.+)$", line)
                if m:
                    contest = m.group(1).strip()
                    contest_n = 0
                continue
            parts = line.split("\t")
            parts = [p.strip() for p in parts]
            if len(parts) != 5:
                raise SystemExit("bad summary row: %r" % line)
            cand, votes, ed, mi, pr = parts
            if cand == "Total":
                continue  # source sum row, not emitted
            contest_n += 1
            out.append(
                {
                    "contest": contest,
                    "candidate": cand,
                    "votes": int(votes),
                    "ed": ed,
                    "mi": mi,
                    "pr": pr,
                    "_n": contest_n,
                }
            )
    return out


def to_county_rows(recs: list[dict]) -> list[list]:
    out = []
    for rec in recs:
        if rec["contest"] not in SUMMARY_CONTESTS:
            continue  # Democratic County Committee single-precinct slots
        office, district, party = SUMMARY_CONTESTS[rec["contest"]]
        out.append(
            {
                "office": office,
                "district": district,
                "party": party,
                "candidate": rec["candidate"],
                "votes": rec["votes"],
                "ed": rec["ed"],
                "mi": rec["mi"],
                "pr": rec["pr"],
                "_contest": rec["contest"],
                "_n": rec["_n"],
            }
        )
    out.sort(key=lambda x: (OFFICE_ORDER.index(x["office"]), x["_contest"], x["_n"]))
    return [
        [COUNTY_NAME, o["office"], o["district"], o["party"], o["candidate"],
         str(o["votes"]), o["ed"], o["mi"], o["pr"]]
        for o in out
    ]


# -------------------------------------------------------------------- checks --
def run_checks(precinct_recs: list[dict], county_recs: list[dict]) -> list[str]:
    problems = []
    # 1) DOS precinct sums vs the county anchor
    sums, n_anchor = {}, 0
    for r in precinct_recs:
        key = (OFFICES[r["office_code"]], r["party"], district_for(r), cand_name(r))
        sums[key] = sums.get(key, 0) + r["votes"]
    for key, cands in ANCHOR_PRECINCT.items():
        for cand, total in cands.items():
            n_anchor += 1
            mine = sums.get((key[0], key[1], key[2], cand), 0)
            if mine != total:
                problems.append(
                    "%s [%s]: %s: precinct sum %d != county anchor %d"
                    % (key[0], key[1], cand, mine, total)
                )
    # 2) county CSV per contest/party/candidate vs the anchor (named only;
    #    the anchor carries no write-ins) — write-ins check only ED+MI+PR
    county_sums, n_county = {}, 0
    for r in to_county_rows(county_recs):
        key = (r[1], r[2], r[3], r[4])
        county_sums[key] = county_sums.get(key, 0) + int(r[5])
    for key, total in county_sums.items():
        office, district, party, cand = key
        if cand == "Write-In Totals":
            continue
        n_county += 1
        anchor = ANCHOR_TOTALS.get((office, party, district), {}).get(cand)
        if anchor is None:
            problems.append("county row %s not in anchor map" % (key,))
            continue
        if total != anchor:
            problems.append(
                "%s [%s]: %s: county sum %d != anchor %d"
                % (office, party, cand, total, anchor)
            )
    # 3) breakdown sums: ED + MI + PR == votes per county CSV row
    for r in to_county_rows(county_recs):
        s = int(r[6]) + int(r[7]) + int(r[8])
        if s != int(r[5]):
            problems.append(
                "breakdown: %s [%s] %s: ED+MI+PR %d != votes %d"
                % (r[1], r[3], r[4], s, int(r[5]))
            )
    return problems, n_anchor, n_county


def main(argv: list[str]) -> int:
    argv = list(argv[1:])
    verify = "--verify" in argv
    args = [a for a in argv if not a.startswith("--")]
    dos_src = args[0] if len(args) > 0 else DEFAULT_DOS
    summary_src = args[1] if len(args) > 1 else DEFAULT_SUMMARY
    precinct_out = args[2] if len(args) > 2 else DEFAULT_PRECINCT_OUT
    county_out = args[3] if len(args) > 3 else DEFAULT_COUNTY_OUT

    problems = []

    precinct_recs = []
    if dos_src and os.path.exists(dos_src):
        precinct_recs = precinct_rows_from_dos(dos_src)
        unknown = sorted({r["precinct_code"] for r in precinct_recs}
                         - set(PRECINCT_NAMES))
        if unknown:
            print("Unknown DOS precinct codes: %s" % unknown, file=sys.stderr)
            return 1
    elif dos_src:
        print("No Sullivan 2026 primary DOS source found (tried %s)" % dos_src,
              file=sys.stderr)

    county_recs = []
    if summary_src and os.path.exists(summary_src):
        county_recs = county_rows_from_summary(summary_src)
        bad = [
            r["contest"] for r in county_recs
            if r["contest"] not in SUMMARY_CONTESTS
            and not r["contest"].upper().startswith("DEMOCRATIC COUNTY COMMITTEE")
            and not r["contest"].upper().startswith("DEMOCRATIC COMMITTEE")
        ]
        if bad:
            print("Unmapped summary contests: %s" % sorted(set(bad)), file=sys.stderr)
            return 1
    elif summary_src:
        print("No Sullivan 2026 primary summary transcription (tried %s)" % summary_src,
              file=sys.stderr)

    if verify:
        problems, n_anchor, n_county = run_checks(precinct_recs, county_recs)
        print("verify: %d contest-key checks vs the 2026 county anchor (precinct sums)"
              % n_anchor)
        print("verify: %d county-CSV candidate checks vs anchor/summary" % n_county)
        if problems:
            for p in problems:
                print("  MISMATCH " + p)
            return 1
        print("verify: all sums match")

    if not precinct_recs and not county_recs:
        print("nothing parsed", file=sys.stderr)
        return 1

    writes = []
    if precinct_recs:
        writes.append((precinct_out, to_precinct_rows(precinct_recs), PRECINCT_HEADER))
    if county_recs:
        writes.append((county_out, to_county_rows(county_recs), COUNTY_HEADER))
    for path, rows, header in writes:
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with open(path, "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(header)
            w.writerows(rows)
        src = dos_src if path == precinct_out else summary_src
        print("wrote %s (%d data rows) from %s" % (path, len(rows), src))
    return 0 if not problems else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
