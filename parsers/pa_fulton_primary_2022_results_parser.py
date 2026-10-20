#!/usr/bin/env python3
"""Fulton County, PA 2022 Primary — precinct-level results parser.

Source situation (documented):
  * The county's certified report "~/code/openelections-sources-pa/2022/primary/
    Fulton PA 2022a Official Results.pdf" (Cumulative Results Report, official,
    run 5/17/2022, run date 05/23/2022, 31 pages) is COUNTY-LEVEL only: pages
    1-12 are the countywide cumulative results for every contest (with
    Provisional / Election Day / Mail-In breakdowns and per-name write-in
    rows), pages 13-31 are scanned DOS ballot-certification pages and a
    certification affidavit.  Text extraction copy:
    work2022p/fulton/county_cumulative.txt.
  * Per-precinct figures therefore come from the PA Department of State
    official certified precinct-returns bulk file for the primary:
        ElectionReturns_2022_Primary_PrecinctReturns.txt
    (county code 29 = Fulton; 481 rows = 13 precincts x 37 named-candidate
    contest rows).  Slice kept at work2022p/fulton/fulton_prcres_slice.txt.
    A slice of the certified county PDF (pages 1-12) is kept at
    work2022p/fulton/county_cumulative.txt and used for (a) verification of
    the DOS sums and (b) the township Committeeperson contests, which the DOS
    bulk file does not carry.

Recount: the June 2022 GOP U.S. Senate recount changed no Fulton candidate
totals — the county PDF's 5/23 totals (Oz 763, McCormick 647, Barnette 904,
...) equal both the DOS bulk sums (verified below) and the ENR county anchor
(2022/20220517__pa__primary__county.csv), so no amendment merge was needed.

Inherent to the DOS bulk source (documented, not parsing losses):
  * No election-day / mail / provisional breakdown in the DOS file -> the
    per-precinct contest rows use the 7-column header.
  * No per-precinct write-in rows in the DOS file.  The DEM U.S. House 13
    contest is write-in-only and therefore has NO rows at all (matches DOS,
    which carries none); per-precinct write-in votes for the other contests
    cannot be assigned from the county-level cumulative report, so no Write
    Ins rows are emitted for the state/federal contests (Cameron precedent).
  * No per-precinct Registered Voters / Ballots Cast (the county PDF carries
    the county turnout header only: 3,315 of 8,207).
  * The two State Committee contests (REP: Kerlin 1,289 / Swain 1,285; DEM
    all write-ins) are countywide in both sources and cannot be assigned to
    precincts, so no rows are emitted for them (documented omission).

Included beyond the Cameron-style DOS rows: the 13 township Committeeperson
contests from the county PDF (office code AYR/BELFAST/... COMMITTEEPERSON).
These are single-precinct contests — the cumulative report's totals for each
township ARE that precinct's totals — so they are emitted exactly, with each
contest's named write-in variants aggregated into one party-tagged "Write Ins"
row per the 2022 primary convention.  Every committee block is checked against
its printed Cast Votes total.  (Franklin/Erie/Northumberland 2022 primaries
carry the same kind of committee rows.)

Usage:
    uv run python parsers/pa_fulton_primary_2022_results_parser.py <dos_prcres_txt> <out_csv> [county_pdf_text]

Defaults: <dos_prcres_txt> = work2022p/fulton/fulton_prcres_slice.txt
          [county_pdf_text] = work2022p/fulton/county_cumulative.txt
"""
from __future__ import annotations

import csv
import os
import re
import sys

COUNTY_NAME = "Fulton"
COUNTY_CODE = "29"  # matches the certification cover page: FULTON COUNTY (29)
ELECTION_TYPE = "P"

HEADER = ["county", "precinct", "office", "district", "party", "candidate", "votes"]

OFFICES = {  # DOS office code -> (office, district field)
    "USS": ("U.S. Senate", "cand_district"),
    "GOV": ("Governor", "cand_district"),
    "LTG": ("Lieutenant Governor", "cand_district"),
    "USC": ("U.S. House", "uscd"),
    "STS": ("State Senate", "ssd"),
    "STH": ("State House", "shd"),
}
OFFICE_ORDER = {name: i for i, name in enumerate(
    ["U.S. Senate", "Governor", "Lieutenant Governor", "U.S. House",
     "State Senate", "State House"])}

# DOS candidate (Last, First, Middle) -> printed name (matches the county
# cumulative report and the ENR county anchor).
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
    ("TAYLOR", "CAROL", "EVON"): "Carol Taylor",
    ("WARD", "JUDITH", "F"): "Judy Ward",
    ("JOYCE", "JOHN", ""): "John Joyce",
    ("TOPPER", "JESSE", "W"): "Jesse Topper",
}

# DOS precinct code -> precinct name (same names as the 2022 general file
# 20221108__pa__general__precinct.csv rows for Fulton).
PRECINCT_NAMES = {
    "10": "Ayr Township",
    "20": "Belfast Township",
    "30": "Bethel Township",
    "40": "Brush Creek Township",
    "50": "Dublin Township",
    "60": "Licking Creek Township",
    "70": "McConnellsburg Borough",
    "80": "Taylor Township",
    "90": "Thompson Township",
    "100": "Todd Township",
    "110": "Union Township",
    "120": "Valley Hi Borough",
    "130": "Wells Township",
}

# County-level totals printed in the certified county cumulative report
# (run date 05/23/2022), used to verify the DOS precinct sums.  Every value
# also equals the ENR county anchor.
PDF_TOTALS = {
    ("U.S. Senate", "DEM"): {
        "Alex Khalil": 18, "Conor Lamb": 74, "Malcolm Kenyatta": 19,
        "John Fetterman": 373,
    },
    ("U.S. Senate", "REP"): {
        "Kathy Barnette": 904, "Mehmet Oz": 763, "George Bochetto": 18,
        "Jeff Bartos": 129, "Dave McCormick": 647, "Sean Gale": 21,
        "Carla Sands": 217,
    },
    ("Governor", "DEM"): {"Josh Shapiro": 441},
    ("Governor", "REP"): {
        "Lou Barletta": 179, "Douglas V Mastriano": 2194, "Nche Zama": 27,
        "Dave White": 78, "Melissa Hart": 26, "Bill McSwain": 144,
        "Charlie Gerow": 25, "Joe Gale": 30, "Jake Corman": 28,
    },
    ("Lieutenant Governor", "DEM"): {
        "Austin Davis": 275, "Ray Sosa": 75, "Brian Sims": 102,
    },
    ("Lieutenant Governor", "REP"): {
        "Clarice Schillinger": 149, "James Earl Jones": 217,
        "Rick Saccone": 427, "John Brown": 92, "Chris Frye": 104,
        "Jeff Coleman": 409, "Russ Diamond": 108, "Carrie Lewis Delrosso": 290,
        "Teddy Daniels": 660,
    },
    ("U.S. House", "REP"): {"John Joyce": 2630},
    ("State Senate", "DEM"): {"Carol Taylor": 422},
    ("State Senate", "REP"): {"Judy Ward": 2515},
    ("State House", "REP"): {"Jesse Topper": 2586},
}

# Township committee contests (from the county PDF) -> precinct name.
COMMITTEE_OFFICE_BY_PRECINCT = {
    "Ayr Township": "Ayr Township Committeeperson",
    "Belfast Township": "Belfast Township Committeeperson",
    "Bethel Township": "Bethel Township Committeeperson",
    "Brush Creek Township": "Brush Creek Township Committeeperson",
    "Dublin Township": "Dublin Township Committeeperson",
    "Licking Creek Township": "Licking Creek Township Committeeperson",
    "McConnellsburg Borough": "McConnellsburg Borough Committeeperson",
    "Taylor Township": "Taylor Township Committeeperson",
    "Thompson Township": "Thompson Township Committeeperson",
    "Todd Township": "Todd Township Committeeperson",
    "Union Township": "Union Township Committeeperson",
    "Valley Hi Borough": "Valley Hi Borough Committeeperson",
    "Wells Township": "Wells Township Committeeperson",
}
PRE_ORDER = {p: int(c) for c, p in PRECINCT_NAMES.items()}

DEFAULT_DOS = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "work2022p", "fulton", "fulton_prcres_slice.txt")
DEFAULT_PDF_TEXT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "work2022p", "fulton", "county_cumulative.txt")

# A choice row: NAME [prov] # x% [# x%] [# x%] [# x%]  (1..4 vote totals)
CHOICE_RE = re.compile(
    r"^(?P<name>.+?)\s+(?P<nums>(?:\d[\d,]*\s+\d+\.\d+%\s*)+)$")
CAST_RE = re.compile(r"Cast Votes:\s+(?:(?:\d[\d,]*\s+\d+\.\d+%\s*)+)$")
COMMITTEE_HEADER_RE = re.compile(
    r"^(?P<place>[A-Z][A-Z ]+?)\s+COMMITTEEPERSON\s+-\s+Democratic Party")
PAGE_TOKENS = ("Cumulative Results Report", "Registered Voters", "Precincts Reporting",
               "Run Time", "Run Date", "Official Results")


def dos_rows(path: str) -> list[dict]:
    """Read the PA DOS prcres quoted-CSV structure, keep Fulton (code 29)."""
    out = []
    with open(path, newline="", encoding="latin-1") as fh:
        for row in csv.reader(fh):
            if len(row) < 26 or row[:2] != ["2022", ELECTION_TYPE]:
                continue
            if row[2] != COUNTY_CODE:
                continue
            if row[8] not in OFFICES:
                raise SystemExit("unexpected office code %r in %s" % (row[8], path))
            out.append({
                "precinct_code": row[3],
                "contest_no": int(row[6]),
                "ballot_order": int(row[7]),
                "office_code": row[8],
                "party": row[9],
                "cand_last": row[11],
                "cand_first": row[12],
                "cand_middle": row[13],
                "cand_suffix": row[14],
                "votes": int(row[15]),
                "uscd": row[18],
                "ssd": row[19],
                "shd": row[20],
            })
    return out


def cand_name(r: dict) -> str:
    name = NAME_FIX.get((r["cand_last"], r["cand_first"], r["cand_middle"]))
    if name is None:
        raise SystemExit("unmapped DOS candidate: %r" % (
            (r["cand_last"], r["cand_first"], r["cand_middle"]),))
    return name


def district_for(r: dict) -> str:
    office, _ = OFFICES[r["office_code"]]
    if office in ("Governor", "Lieutenant Governor", "U.S. Senate"):
        return ""
    if office == "U.S. House":
        return str(int(r["uscd"]))
    if office == "State Senate":
        return str(int(r["ssd"]))
    return str(int(r["shd"]))  # State House


def committee_rows(pdf_text: str):
    """Extract the 13 township Committeeperson contests from the county
    cumulative report text.  Named write-in variants are aggregated into one
    'Write Ins' row per contest.  Returns (rows, checks) where checks are
    per-contest (emitted_sum, printed Cast Votes total)."""
    lines = [l.rstrip() for l in pdf_text.split("\n")]
    contests = []          # (place, [choice dicts], cast_total)
    cur = None
    for line in lines:
        s = line.strip()
        if not s:
            continue
        hm = COMMITTEE_HEADER_RE.match(s)
        if hm:
            place = hm.group("place").title().strip()
            place = place.replace("Mc Connellsburg", "McConnellsburg")
            place = place.replace("Mcconnellsburg", "McConnellsburg")
            cur = {"place": place, "choices": [], "cast": None}
            contests.append(cur)
            continue
        if cur is None:
            continue
        # page-boundary / footer junk
        if any(t in s for t in PAGE_TOKENS) or s == "*** End of report ***":
            continue
        if re.match(r"^Page \d+$", s):
            continue
        cm = CAST_RE.match(s)
        if cm:
            cur["cast"] = int(re.findall(r"(\d[\d,]*)\s+\d+\.\d+%", s)[-1].replace(",", ""))
            cur = None
            continue
        m = CHOICE_RE.match(s)
        if m and re.search(r"[A-Za-z]", m.group("name")):
            name = re.sub(r"^(.+?)\s+\(W\)$", r"\1", m.group("name").strip())
            nums = re.findall(r"(\d[\d,]*)\s+\d+\.\d+%", m.group("nums"))
            total = int(nums[-1].replace(",", ""))
            cur["choices"].append({"name": name, "writein": "(W)" in s, "total": total})
    rows = []
    checks = []
    for c in contests:
        place = c["place"]
        office = COMMITTEE_OFFICE_BY_PRECINCT[place]
        emitted = 0
        for ch in c["choices"]:
            if ch["writein"]:
                continue
            rows.append({
                "precinct": place, "precinct_code": PRE_ORDER[place],
                "office": office, "district": "", "party": "DEM",
                "candidate": ch["name"].title(), "votes": ch["total"], "sort": 0,
            })
            emitted += ch["total"]
        wi = sum(ch["total"] for ch in c["choices"] if ch["writein"])
        if wi:
            rows.append({
                "precinct": place, "precinct_code": PRE_ORDER[place],
                "office": office, "district": "", "party": "DEM",
                "candidate": "Write Ins", "votes": wi, "sort": 1,
            })
            emitted += wi
        checks.append((office, place, emitted, c["cast"], len(c["choices"])))
    return rows, checks


def verify_dos(rows: list[dict]) -> tuple[int, list[str]]:
    sums: dict[tuple, int] = {}
    for r in rows:
        office, _ = OFFICES[r["office_code"]]
        key = (office, r["party"], cand_name(r))
        sums[key] = sums.get(key, 0) + r["votes"]
    problems, n = [], 0
    for (office, party), cands in PDF_TOTALS.items():
        for cand, total in cands.items():
            n += 1
            mine = sums.get((office, party, cand), 0)
            if mine != total:
                problems.append("%s [%s]: %s: DOS precinct sum %d != county PDF total %d"
                                % (office, party, cand, mine, total))
    return n, problems


ENR_NAME_FIX = {  # our printed name -> ENR anchor formal name (named candidates only)
    "Alex Khalil": "Alexandria Gloria Khalil", "Conor Lamb": "Conor J. Lamb",
    "Malcolm Kenyatta": "Malcolm Kenyatta", "John Fetterman": "John K. Fetterman",
    "Carla Sands": "Carla Herd Sands", "Dave McCormick": "David H. McCormick",
    "George Bochetto": "George A. Bochetto", "Jeff Bartos": "Jeffrey A. Bartos",
    "Kathy Barnette": "Kathy J. Barnette", "Mehmet Oz": "Mehmet C. Oz",
    "Sean Gale": "Sean Peter Gale",
    "Josh Shapiro": "Joshua D. Shapiro",
    "Lou Barletta": "Louis Barletta", "Douglas V Mastriano": "Douglas V. Mastriano",
    "Nche Zama": "Nche Zama", "Dave White": "David J. White",
    "Melissa Hart": "Melissa A. Hart", "Bill McSwain": "William M. McSwain",
    "Charlie Gerow": "Charles R. Gerow", "Joe Gale": "Joseph Charles Gale",
    "Jake Corman": "Jacob Doyle Corman III",
    "Austin Davis": "Austin Davis", "Ray Sosa": "Raymond L. Sosa",
    "Brian Sims": "Brian Sims",
    "Clarice Schillinger": "Clarice D. Schillinger",
    "James Earl Jones": "James E. Jones", "Rick Saccone": "Richard Saccone",
    "John Brown": "John A. Brown", "Chris Frye": "Christopher C. Frye Jr",
    "Jeff Coleman": "Jeffrey H. Coleman", "Russ Diamond": "Russell H. Diamond",
    "Carrie Lewis Delrosso": "Carrie Delrosso", "Teddy Daniels": "Theodore Daniels",
    "John Joyce": "John Joyce", "Carol Taylor": "Carol Evon Taylor",
    "Judy Ward": "Judith F. Ward", "Jesse Topper": "Jesse W. Topper",
}


def verify_enr(out_rows) -> tuple[int, list[str]]:
    """Cross-check per (office, district, party, candidate) against the ENR
    county anchor (named candidates only; anchor has no RV/BC/write-ins)."""
    enr_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "2022", "20220517__pa__primary__county.csv")
    if not os.path.exists(enr_path):
        return 0, ["ENR anchor not found at %s" % enr_path]
    enr = {}
    with open(enr_path, newline="", encoding="utf-8") as fh:
        for row in csv.reader(fh):
            if row and row[0] == COUNTY_NAME:
                key = (row[1], row[2], row[3], row[4])
                enr[key] = int(row[5])
    mine = {}
    for r in out_rows:
        key = (r["office"], r["district"], r["party"], r["candidate"])
        mine[key] = mine.get(key, 0) + int(r["votes"])
    problems, n = [], 0
    for key, total in sorted(mine.items()):
        enr_key = (key[0], key[1], key[2], ENR_NAME_FIX.get(key[3], key[3]))
        if enr_key not in enr:
            continue  # e.g. township Committeeperson rows — countywide ENR n/a
        n += 1
        if enr[enr_key] != total:
            problems.append("%s %s [%s] %s (ENR %r): ours %d != ENR %d"
                            % (key[0], key[1], key[2], key[3], enr_key[3], total, enr[enr_key]))
    # every anchored candidate should have been covered; exceptions are the
    # two Republican State Committee rows, which both sources (the DOS bulk
    # file and the county cumulative report) carry countywide only — with no
    # precinct attribution — so no rows are emitted (documented omission)
    intentional = {
        ("Member of Republican State Committee", "29", "REP", "Daniel H. Swain Jr"),
        ("Member of Republican State Committee", "29", "REP", "Stanley Jay Kerlin"),
    }
    for key, total in sorted(enr.items()):
        if key in intentional:
            continue
        if key not in mine:
            covered = any((k[0], k[1], k[2], ENR_NAME_FIX.get(k[3], k[3])) == key for k in mine)
            if not covered:
                problems.append("ENR candidate not covered by ours: %s" % (key,))
    return n, problems


def main(argv: list[str]) -> int:
    dos = argv[0] if len(argv) > 0 else DEFAULT_DOS
    out_csv = argv[1] if len(argv) > 1 else None
    pdf_text = argv[2] if len(argv) > 2 else DEFAULT_PDF_TEXT
    if not os.path.exists(dos):
        print("No Fulton 2022 primary DOS source found (tried %s)" % (args or DEFAULT_DOS),
              file=sys.stderr)
        return 1
    rows = dos_rows(dos)
    if not rows:
        print("No Fulton primary rows found in source %s" % dos, file=sys.stderr)
        return 1
    missing = sorted({r["precinct_code"] for r in rows} - set(PRECINCT_NAMES))
    if missing:
        print("Unknown precinct codes: %s" % missing, file=sys.stderr)
        return 1

    out_rows = []
    for r in rows:
        office, _ = OFFICES[r["office_code"]]
        out_rows.append({
            "precinct": PRECINCT_NAMES[r["precinct_code"]],
            "precinct_code": int(r["precinct_code"]),
            "office": office, "district": district_for(r), "party": r["party"],
            "candidate": cand_name(r), "votes": r["votes"],
            "sort": 0,
            "sk": (OFFICE_ORDER[office], r["contest_no"], r["ballot_order"]),
        })

    committee_ok = None
    if pdf_text and os.path.exists(pdf_text):
        crow, checks = committee_rows(open(pdf_text, encoding="latin-1").read())
        out_rows.extend(crow)
        committee_ok = checks
    else:
        print("committee section skipped (county cumulative text not found: %s)" % pdf_text)

    out_rows.sort(key=lambda x: (x["precinct_code"],
                                 x.get("sk") or (99, 0, 0),
                                 x["sort"], x["candidate"]))

    n, problems = verify_dos(rows)
    print("verify: %d named-candidate DOS precinct sums checked against county PDF totals" % n)
    if problems:
        for p in problems:
            print("  MISMATCH " + p)
        return 1
    print("verify: all DOS sums match the county cumulative report (also the ENR anchor values)")

    if committee_ok is not None:
        bad = [c for c in committee_ok if c[3] != c[2]]
        ok = [c for c in committee_ok if c[3] == c[2]]
        print("verify: %d committee blocks checked vs printed Cast Votes, %d exact%s"
              % (len(committee_ok), len(ok),
                 "" if not bad else "; MISMATCH: " + "; ".join(str(c) for c in bad)))
        if bad:
            return 1

    n2, enr_problems = verify_enr(out_rows)
    print("verify: %d (office, district, party, candidate) sums checked vs ENR county anchor" % n2)
    if enr_problems:
        for p in enr_problems:
            print("  MISMATCH " + p)
        return 1

    if out_csv:
        with open(out_csv, "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(HEADER)
            for o in out_rows:
                w.writerow([COUNTY_NAME, o["precinct"], o["office"], o["district"],
                            o["party"], o["candidate"], str(o["votes"])])
        print("wrote %s (%d data rows) from %s" % (out_csv, len(out_rows), dos))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))