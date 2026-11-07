#!/usr/bin/env python3
"""Warren County, PA 2024 General Primary — precinct-level results parser.

Source situation (documented):
  * Both county source PDFs in
    "~/code/openelections-sources-pa/2024/primary/" are COUNTYWIDE only,
    with no per-precinct vote rows anywhere:
      - "Warren PA 2024 Primary Election Summary Report.pdf" (Dominion
        "Election Summary Report", 2024-04-23 9:59:37 PM, "Unofficial
        Results", 6 pages) — election-night snapshot.
      - "Warren PA 2024 Primary Unofficial Total Results.pdf" (same report
        family, 2024-04-26 12:51:51 PM, "Unofficial Totals Results", 9
        pages) — post-canvass snapshot; this one also prints per-precinct
        NO rows, only countywide candidate blocks plus the write-in-only
        contests' individual write-in names (State Senate DEM, State House
        65 DEM) with countywide counts.
    (verified by reading the extracted pdftotext -layout text of every page;
    text copies in work2024p/warren/summary.txt and unofficial.txt).

  Precinct-level figures therefore come from the PA Department of State
  certified precinct returns bulk file for the primary:
      ElectionReturns_2024_Primary_PrecinctReturns.txt
  (ASCII comma-delimited; numeric fields formatted as floats, e.g. "2024.0";
  Warren = county code 62).  A copy of the bulk file extracted by DOS on
  2024-07-23 (post-certification) is kept at
  work2024p/warren/electionreturns_2024_primary_precinctreturns.txt — the
  same byte-identical statewide file used for the 2024-primary Indiana parse
  (work2024p/indiana/).

  1,221 Warren rows = 33 precincts x 37 contest-(party-)candidate rows:
  33 x (USP 2 DEM + 2 REP, USS 1 + 1, ATT 5 + 2, AUD 2 + 1, TRE 2 + 1,
  USC 1 + 1, STS 1 + 1 (REP only), STH 1 + 1 (REP only), DED 4 DEM,
  DER 8 REP, ADR 2 REP).

Consequences (all inherent to the DOS bulk source, not parsing losses):
  * No election-day / mail / provisional breakdown -> 7-column output header.
  * No per-precinct or per-county write-in rows (DOS carries none): the
    per-precinct write-ins cannot be distributed, so per the no-invention
    rule no Write-In rows are emitted.  This also means two write-in-only
    contests are entirely ABSENT from the precinct file: State Senate [DEM]
    (county: 0 named-candidate votes, 168 write-ins on the 4/23 snapshot /
    169 on the 4/26 snapshot incl. scattered) and State House 65 [DEM]
    (county: 567 total, Erin Willman 495 / scattered 43).  Their countywide
    write-in figures print on the "Unofficial Totals Results" PDF, pp. 4-6.
  * No per-precinct Registered Voters / Ballots Cast.  County PDF
    (4/26 snapshot): Registered Voters 5,991 of 25,860; Ballots Cast 5,991
    (DEM times cast 1,981 / REP 4,010).
  * Every contest the DOS file does carry sums EXACTLY to the 4/26 county
    snapshot PDF's printed totals -- all 37 countywide named-candidate
    totals match (checked by --verify, see PDF_TOTALS below).
  * The election-night 4/23 9:59 PM snapshot differs only by small
    pre-canvass deltas (e.g. President DEM Biden 1,650 -> 1,660; U.S.
    Senate REP McCormick 3,525 -> 3,543).  The DOS certified vintage matches
    the later 4/26 totals exactly, so they are the verification anchor.

Precinct code -> name mapping: DOS names are trimmed/normalized to the
strings already used in 2024/counties/20241105__pa__general__warren__precinct.csv
(same 33 names as 2022/2025).  The two SUGAR GROVE codes (255/270) carry the
same DOS name; the split was established for the 2022 primary parse (see
parsers/pa_warren_primary_2022_results_parser.py docstring: 255 = TWP, 270 =
BORO, from the county's own committee-race turnout rows) and holds for 2024:
4/26 DEM times cast 1,981 and the DOS DEM rows at 255 vs 270 again identify
the TWP as the larger precinct.

Usage:
    uv run python parsers/pa_warren_primary_2024_results_parser.py <dos_prcres_txt> <out.csv>
    uv run python parsers/pa_warren_primary_2024_results_parser.py --verify <dos_prcres_txt> <out.csv>

If <dos_prcres_txt> is omitted the parser looks for the copy in
work2024p/warren/ relative to the repo root.
"""
from __future__ import annotations

import csv
import os
import sys

COUNTY_NAME = "Warren"
COUNTY_CODE = "62"
COUNTY_CODE_FLOAT = "62.0"  # numeric fields are float-formatted in the 2024 file
YEAR = "2024"
ELECTION_TYPE = "P"  # DOS bulk-file election type for the primary

HEADER = ["county", "precinct", "office", "district", "party", "candidate", "votes"]

# DOS office code -> office (ballot order within each party's ballot)
OFFICES = {
    "USP": "President",
    "USS": "U.S. Senate",
    "ATT": "Attorney General",
    "AUD": "Auditor General",
    "TRE": "State Treasurer",
    "USC": "U.S. House",
    "STS": "State Senate",
    "STH": "State House",
    "DED": "Delegate to the Democratic National Convention",
    "DER": "Delegate to the Republican National Convention",
    "ADR": "Alternate Delegate to the Republican National Convention",
}
OFFICE_ORDER = list(OFFICES)  # ballot order; sorting key

# Districted contests -> which DOS field supplies the district:
#   "cand_dist" = Candidate District col (USC 15, STS 21, STH 65, delegates
#   15), all populated per row for these contests; "" for statewide contests.
DISTRICT_SOURCE = {
    "USC": "cand_dist",
    "STS": "cand_dist",
    "STH": "cand_dist",
    "DED": "cand_dist",
    "DER": "cand_dist",
    "ADR": "cand_dist",
}

# DOS candidate (Last, First, Middle)[+(Suffix)] -> printed name, matching
# the county summary PDFs (Warren 2024 Primary Election Summary Report /
# Unofficial Totals Results) and repo naming precedent for these offices.
NAME_FIX = {
    ("BIDEN", "JOSEPH", "R"): "Joseph R Biden Jr",          # suffix JR
    ("PHILLIPS", "DEAN", "BENSON"): "Dean Phillips",
    ("HALEY", "NIKKI", "R"): "Nikki R Haley",
    ("TRUMP", "DONALD", "JOHN"): "Donald J Trump",
    ("CASEY", "ROBERT", "P"): "Robert P Casey Jr",          # suffix JR
    ("MCCORMICK", "DAVID", "H"): "Dave McCormick",
    ("STOLLSTEIMER", "JACK", ""): "Jack Stollsteimer",
    ("DEPASQUALE", "EUGENIO", ""): "Eugene Depasquale",
    ("KHAN", "JOSEPH", "JAMIL"): "Joe Khan",
    ("BRADFORD-GREY", "KEIR", "N"): "Keir Bradford-Grey",
    ("SOLOMON", "JARED", "G"): "Jared Solomon",
    ("SUNDAY", "DAVID", ""): "Dave Sunday",
    ("WILLIAMS", "WENDELL", "CRAIG"): "Craig Williams",
    ("KENYATTA", "MALCOLM", ""): "Malcolm Kenyatta",
    ("PINSLEY", "MARK", "E"): "Mark Pinsley",
    ("DEFOOR", "TIMOTHY", "LIONEL"): "Tim DeFoor",
    ("BIZZARRO", "RYAN", ""): "Ryan Bizzarro",
    ("MCCLELLAND", "ERIN", "R"): "Erin McClelland",
    ("GARRITY", "STACY", "L"): "Stacy Garrity",
    ("WOMER", "ZACHERAY", "WILLIAM"): "Zach Womer",
    ("THOMPSON", "GLENN", "WILLIAM"): "Glenn GT Thompson",  # suffix JR.
    ("HUTCHINSON", "SCOTT", "E"): "Scott E Hutchinson",
    ("RAPP", "KATHY", "L"): "Kathy L Rapp",
    ("NANES", "EZRA", "J"): "Ezra Nanes",
    ("WHITE", "BRAXTON", "A"): "Braxton White",
    ("SWOBODA", "MARGARET", "M"): "Margie Swoboda",
    ("SHADLE", "LAURA", ""): "Laura Shadle",
    ("KUNSELMAN", "DANIEL", "S"): "Danny Kunselman",
    ("HARRIS", "MARK", "J"): "Mark J Harris",
    ("SMAIL", "DARLENE", ""): "Darlene Smail",
    ("ROWE", "DAVID", "HUMMER"): "David H Rowe",
    ("CAUSER", "MATTHEW", "MARTIN"): "Matthew M Causer",
    ("KELLER", "FREDERICK", "B"): "Fred Keller",
    ("LAYNG", "KENNETH", ""): "Ken Layng",
    ("HAAS", "JOYCE", "C"): "Joyce C Haas",
    ("CLARK", "DANIEL", "F"): "Daniel F Clark",
    ("HARRIS", "IRENE", "C"): "Irene Harris",
}

# DOS precinct code -> clean precinct name (same strings as the 2024 general
# / 2022 / 2025 general Warren precinct files; see module docstring for the
# SUGAR GROVE split).
PRECINCT_NAMES = {
    "10": "BEAR LAKE BORO",
    "20": "BROKENSTRAW TWP",
    "30": "CHERRY GROVE TWP",
    "40": "CLARENDON BORO",
    "50": "COLUMBUS TWP",
    "60": "CONEWANGO #1 TWP",
    "70": "CONEWANGO #2 TWP",
    "80": "DEERFIELD TWP",
    "90": "ELDRED TWP",
    "100": "ELK TWP",
    "110": "FARMINGTON TWP",
    "125": "FREEHOLD TWP",
    "140": "GLADE TWP",
    "150": "LIMESTONE TWP",
    "165": "MEAD TWP",
    "180": "PINE GROVE TWP",
    "195": "PITTSFIELD TWP",
    "210": "PLEASANT TWP",
    "220": "SHEFFIELD TWP",
    "230": "SOUTHWEST TWP",
    "240": "SPRING CREEK TWP",
    "255": "SUGAR GROVE TWP",
    "270": "SUGAR GROVE BORO",
    "280": "TIDIOUTE BORO",
    "290": "TRIUMPH TWP",
    "300": "WARREN CENTRAL",
    "310": "WARREN EAST",
    "320": "WARREN NORTH",
    "330": "WARREN SOUTH",
    "340": "WARREN SOUTHEAST",
    "350": "WARREN WEST",
    "360": "WATSON TWP",
    "370": "YOUNGSVILLE BORO",
}

DEFAULT_SOURCE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "work2024p", "warren",
    "electionreturns_2024_primary_precinctreturns.txt",
)

# Countywide totals printed in the county's "Unofficial Totals Results" PDF
# (2024-04-26 12:51:51 PM snapshot; every candidate the DOS bulk file
# carries).  Used by --verify.  Write-ins and the two write-in-only DEM
# contests (State Senate, State House 65) are NOT here -- no per-precinct
# source, no output rows (documented above).  The 4/23 9:59 PM
# "Election Summary Report" snapshot's deltas from these totals are listed
# in PDF_0423_DELTAS for documentation only (all are small pre-canvass
# differences; the DOS certified vintage matches 4/26 exactly).
PDF_TOTALS = {
    ("President", "DEM"): {"Joseph R Biden Jr": 1660, "Dean Phillips": 174},
    ("President", "REP"): {"Nikki R Haley": 631, "Donald J Trump": 3246},
    ("U.S. Senate", "DEM"): {"Robert P Casey Jr": 1820},
    ("U.S. Senate", "REP"): {"Dave McCormick": 3543},
    ("Attorney General", "DEM"): {
        "Jack Stollsteimer": 172,
        "Eugene Depasquale": 1102,
        "Joe Khan": 266,
        "Keir Bradford-Grey": 152,
        "Jared Solomon": 109,
    },
    ("Attorney General", "REP"): {"Dave Sunday": 2394, "Craig Williams": 1165},
    ("Auditor General", "DEM"): {"Malcolm Kenyatta": 756, "Mark Pinsley": 981},
    ("Auditor General", "REP"): {"Tim DeFoor": 3423},
    ("State Treasurer", "DEM"): {"Ryan Bizzarro": 1248, "Erin McClelland": 614},
    ("State Treasurer", "REP"): {"Stacy Garrity": 3493},
    ("U.S. House", "DEM"): {"Zach Womer": 1606},
    ("U.S. House", "REP"): {"Glenn GT Thompson": 3633},
    ("State Senate", "REP"): {"Scott E Hutchinson": 3627},
    ("State House", "REP"): {"Kathy L Rapp": 3511},
    ("Delegate to the Democratic National Convention", "DEM"): {
        "Ezra Nanes": 1379,
        "Braxton White": 1391,
        "Margie Swoboda": 1448,
        "Laura Shadle": 1463,
    },
    ("Delegate to the Republican National Convention", "REP"): {
        "Danny Kunselman": 1401,
        "Mark J Harris": 1336,
        "Darlene Smail": 1302,
        "David H Rowe": 1200,
        "Matthew M Causer": 945,
        "Fred Keller": 1112,
        "Ken Layng": 375,
        "Joyce C Haas": 1394,
    },
    ("Alternate Delegate to the Republican National Convention", "REP"): {
        "Daniel F Clark": 2598,
        "Irene Harris": 2337,
    },
}

# Contest rows deliberately absent from any output (documented above):
# State Senate [DEM] and State House 65 [DEM] -- write-in-only contests.
# (No zero-row sentinel needed for 2024: the DOS file simply carries no
# such rows for Warren.)

# Election-night (4/23 9:59 PM) snapshot deltas vs the 4/26 totals, for the
# report/PR documentation.  Format: contest [party] candidate: 4/23 -> 4/26.
PDF_0423_DELTAS = {
    "President [DEM] Joseph R Biden Jr 1,650 -> 1,660",
    "President [REP] Nikki R Haley 628 -> 631",
    "President [REP] Donald J Trump 3,230 -> 3,246",
    "U.S. Senate [DEM] Robert P Casey Jr 1,810 -> 1,820",
    "U.S. Senate [REP] Dave McCormick 3,525 -> 3,543 / Joseph J Vodvarka (write-in) 238 -> 239",
    "Attorney General [DEM] Stollsteimer 171 -> 172 / Depasquale 1,098 -> 1,102 / Khan 263 -> 266",
    "Attorney General [REP] Sunday 2,384 -> 2,394 / Williams 1,158 -> 1,165",
    "Auditor General [DEM] Kenyatta 752 -> 756 / Pinsley 975 -> 981; [REP] DeFoor 3,407 -> 3,423",
    "State Treasurer [DEM] Bizzarro 1,243 -> 1,248 / McClelland 611 -> 614; [REP] Garrity 3,478 -> 3,493",
    "U.S. House [DEM] Womer 1,599 -> 1,606; [REP] Thompson 3,616 -> 3,633",
    "State Senate [REP] Hutchinson 3,610 -> 3,627",
    "State House 65 [REP] Rapp 3,495 -> 3,511",
    "Delegates [DEM] Nanes 1,372 -> 1,379 / White 1,385 -> 1,391 / Swoboda 1,441 -> 1,448 / Shadle 1,457 -> 1,463",
    "Delegates [REP] all eight candidates +0/+1/+2..+6 (countywide print)",
    "Alt Delegates [REP] Clark 2,584 -> 2,598 / Irene Harris 2,328 -> 2,337",
}


def county_rows_from_dos(path: str) -> list[dict]:
    """Read the PA DOS prcres comma-delimited structure (float-formatted
    numerics), keep Warren (county code 62), 2024 primary."""
    out = []
    with open(path, newline="", encoding="latin-1") as fh:
        for row in csv.reader(fh):
            if len(row) < 23 or not row[0].startswith(YEAR) or row[1] != ELECTION_TYPE:
                continue
            if row[2] not in (COUNTY_CODE, COUNTY_CODE_FLOAT):
                continue
            if row[8] not in OFFICES:
                raise SystemExit(
                    "unexpected office code %r in %s (new contest? re-examine)" % (row[8], path)
                )
            out.append(
                {
                    "precinct_code": str(int(float(row[3]))),
                    "office_code": row[8],
                    "party": row[9],
                    "cand_last": row[11].strip(),
                    "cand_first": row[12].strip(),
                    "cand_middle": row[13].strip(),
                    "cand_suffix": row[14].strip(),
                    "votes": int(float(row[15])),
                    "ballot_position": int(float(row[7])),
                    "cand_dist": row[5],
                }
            )
    return out


def cand_name(r: dict) -> str:
    key = (r["cand_last"], r["cand_first"], r["cand_middle"])
    name = NAME_FIX.get(key)
    if name is None:
        # Fallback: "First Middle Last", suffix appended, title-cased.
        parts = [r["cand_first"].title(), r["cand_middle"].title(), r["cand_last"].title()]
        name = " ".join(p for p in parts if p)
        if r["cand_suffix"]:
            name += " " + r["cand_suffix"].rstrip(".").title()
    return name


def district_for(r: dict) -> str:
    src = DISTRICT_SOURCE.get(r["office_code"])
    if src is None:
        return ""
    val = {"cand_dist": r["cand_dist"]}[src]
    return str(int(float(val)))


def to_output_rows(rows: list[dict]) -> list[list]:
    out = []
    for r in rows:
        out.append(
            {
                "county": COUNTY_NAME,
                "precinct": PRECINCT_NAMES[r["precinct_code"]],
                "office": OFFICES[r["office_code"]],
                "district": district_for(r),
                "party": r["party"],
                "candidate": cand_name(r),
                "votes": r["votes"],
                "_code": int(float(r["precinct_code"])),
                "_party_ord": 0 if r["party"] == "DEM" else 1,
                "_office_ord": OFFICE_ORDER.index(r["office_code"]),
                "_pos": r["ballot_position"],
            }
        )
    out.sort(key=lambda x: (x["_code"], x["_party_ord"], x["_office_ord"], x["_pos"], x["candidate"]))
    return [
        [o["county"], o["precinct"], o["office"], o["district"], o["party"], o["candidate"], str(o["votes"])]
        for o in out
    ]


def run_checks(rows: list[dict]) -> tuple[int, list[str]]:
    """Sum precinct votes per (office, party, candidate) and compare with the
    county "Unofficial Totals Results" PDF's printed countywide totals."""
    sums: dict[tuple, int] = {}
    for r in rows:
        key = (OFFICES[r["office_code"]], r["party"], cand_name(r))
        sums[key] = sums.get(key, 0) + r["votes"]
    problems = []
    n = 0
    for (office, party), cands in PDF_TOTALS.items():
        for cand, total in cands.items():
            n += 1
            mine = sums.get((office, party, cand), 0)
            if mine != total:
                problems.append(
                    f"{office} [{party}]: {cand}: precinct sum {mine} != PDF total {total}"
                )
    # Report any output-side contest blocks not anchored by the PDF totals
    extra = sorted(set(sums) - {(o, p, c) for (o, p), cs in PDF_TOTALS.items() for c in cs})
    for e in extra:
        problems.append(f"NOT IN PDF CHECK: {e} (county sum {sums[e]})")
    return n, problems


def main(argv: list[str]) -> int:
    args = [a for a in argv if a != "--verify"]
    src = args[0] if args and os.path.exists(args[0]) else DEFAULT_SOURCE
    if not os.path.exists(src):
        print("No Warren 2024 primary DOS source found (tried %s)" % (args or DEFAULT_SOURCE),
              file=sys.stderr)
        return 1
    rows = county_rows_from_dos(src)
    if not rows:
        print("No Warren 2024 primary rows found in source %s" % src, file=sys.stderr)
        return 1
    missing_precincts = sorted({r["precinct_code"] for r in rows} - set(PRECINCT_NAMES),
                               key=lambda x: int(float(x)))
    if missing_precincts:
        print("Unknown precinct codes: %s" % missing_precincts, file=sys.stderr)
        return 1

    out_rows = to_output_rows(rows)

    if "--verify" in argv:
        n, problems = run_checks(rows)
        print("verify: %d candidate totals checked against the 4/26 'Unofficial Totals "
              "Results' county PDF" % n)
        print("verify: precinct source is the DOS certified bulk file "
              "(ElectionReturns_2024_Primary_PrecinctReturns.txt, extracted 2024-07-23)")
        print("verify: documented absences: State Senate [DEM] + State House 65 [DEM] "
              "(write-in-only), all write-ins, RV/BC rows (no per-precinct source)")
        if problems:
            for p in problems:
                print("  MISMATCH " + p)
            return 1
        print("verify: all contest sums match the county PDF totals exactly")

    if len(args) > 1:
        with open(args[1], "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(HEADER)
            w.writerows(out_rows)
        print("wrote %s (%d data rows, 33 precincts) from %s" % (args[1], len(out_rows), src))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
