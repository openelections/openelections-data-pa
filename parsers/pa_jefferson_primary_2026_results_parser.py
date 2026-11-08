#!/usr/bin/env python3
"""Parse Jefferson County PA 2026 Primary precinct results.

Sources (both needed):

  * ``Jefferson County StatementOfVotesCastRPTADJ-Final-Web.pdf`` (SOVC
    crosstab with rotated column headers, OFFICIAL RESULTS, generated
    2026-05-27) -- supplies the Member of Democratic/Republican State
    Committee and Democratic Party Chairperson row values.

  * PA DOS certified precinct-returns bulk file (``PA DOS 2026 General
    Primary Certified Precinct Returns Statewide.txt``, split per county to
    ``work2026p/dos/jefferson__dos_precinctreturns.txt``, Jefferson county
    code 33; 342 rows = 38 precincts x 9 named-candidate rows) -- supplies
    the Governor / Lieutenant Governor / U.S. House 15 / State House
    66 & 75 values.  DOS fallback is used because the county EMS report
    falls short of the statewide county anchor
    (``2026/20260519__pa__primary__county.csv``):

      - precincts whose totals are below the county's confidentiality
        threshold print "**** - Insufficient Turnout to Protect Voter
        Privacy" instead of numbers, so the SOVC is missing Governor DEM
        rows for Beaver/Clover/Porter/Worthville Townships and Governor
        REP rows for Timblin Borough, etc.;
      - Falls Creek Borough 2 is reported with 0 voters by the county EMS
        report ("Precincts Reported: 38 of 38") while the certified DOS
        bulk file carries its votes (Shapiro 1 / Garrity 2 / Davis 1 /
        Richey 1 / Ventre 1 / Bilger 1 / Thompson 2);
      - the 75th Legislative District rows (Kim Kohlhepp 1 / Mike
        Armanini 2) exist only in the DOS bulk file -- it reports
        Falls Creek Borough 2 split by district, which the county EMS
        sources do not.

    Everywhere the SOVC is unredacted the DOS values are digit-exact the
    same, and the DOS per-contest county sums equal the statewide county
    anchor digit-exact (1457 / 3319 / 1418 / 2054 / 1345 / 1404 / 3337 /
    1440 / 3429 / 1 / 2).

DOS quirks (no write-in rows, no RV/BC rows, no vote breakdowns, no State
Committee) mean: state committee rows come from the SOVC where unredacted,
and stay short of the anchor where a precinct's SOVC cells are redacted
(the 4 small DEM precincts and Timblin) or unreported (Falls Creek
Borough 2) -- documented residuals:
  Member of Democratic State Committee: John Adduci 1417 vs anchor 1446
  Member of Republican State Committee: Paul Bishop 2765 vs 2769,
                                        Karen McNutt 2889 vs 2895
  Democratic Party Chairperson: Bridget R. Shaffer 1403 (SOVC county total
  1,431; no anchor row).  No precinct-level source carries those votes.

Contest coverage: Governor, Lieutenant Governor, U.S. House 15, State House
66, State House 75, Member of Democratic State Committee, Member of
Republican State Committee, Democratic Party Chairperson.  Omitted (county
party committee races, write-in-only): Democratic/Republican Party
Committeeman/Committeewoman - <precinct> blocks; candidate write-in and
"Unresolved" columns; Times Cast / Registered Voters turnout tables (the
pre-fix file also carried no RV/BC rows).

Candidate naming: U.S. House REP is normalized to "Glenn GT Thompson"
(the SOVC prints "Glenn Thompson"; the statewide county anchor keys
"GLENN GT THOMPSON", 34 other 2026 county precinct files use
"Glenn GT Thompson", and Jefferson's own 2022/2025 files use
"Glenn GT Thompson" -- the pre-fix file's "Glenn Thompson 'gt'" and the
county-CSV's "Glenn 'gt' Thompson" were one-off variants).

Adapted from pa_jefferson_primary_2024_results_parser.py with an updated
HEADER_RE / OFFICE_MAP for the 2026 contest names.

Usage:
    python parsers/pa_jefferson_primary_2026_results_parser.py <input.pdf> <output.csv>
"""

from __future__ import annotations

import csv
import os
import re
import subprocess
import sys
from pathlib import Path

import pdfplumber


REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOS_SLICE = os.path.join(REPO_ROOT, "work2026p", "dos",
                         "jefferson__dos_precinctreturns.txt")
COUNTY_CODE = "33"

OFFICE_MAP = {
    "GOVERNOR": ("Governor", False),
    "LIEUTENANT GOVERNOR": ("Lieutenant Governor", False),
    "REPRESENTATIVE IN CONGRESS": ("U.S. House", True),
    "SENATOR IN THE GENERAL ASSEMBLY": ("State Senate", True),
    "REPRESENTATIVE IN THE GENERAL ASSEMBLY": ("State House", True),
}

# Contests parsed from the PDF and emitted.
KEEP_OFFICE_RE = re.compile(
    r"^(MEMBER OF THE (DEMOCRATIC|REPUBLICAN) STATE COMMITTEE"
    r"|DEMOCRATIC PARTY CHAIRPERSON)$", re.IGNORECASE)

DISTRICT_RE = re.compile(r"-\s*(\d+)\w*\s+DISTRICT\b", re.IGNORECASE)

HEADER_RE = re.compile(
    r"^(Governor|Lieutenant Governor|Representative in Congress"
    r"|Representative in the General Assembly|Senator in the General Assembly"
    r"|Member of the (?:Democratic|Republican) State Committee"
    r"|Democratic Party Chairperson"
    r"|(?:Democratic|Republican) Party Committe(?:eman|ewoman) .+?)"
    r"\s*(?:[-\s]+(\d+)\w*\s+District)?\s*\((DEM|REP)\)\s*\(Vote for\s+\d+\)\s*$"
)

FIELDNAMES = [
    "county", "precinct", "office", "district", "party",
    "candidate", "votes", "election_day", "provisional", "absentee",
]

SKIP_HEADER_TOKENS = (
    "PRECINCT", "TIMES CAST", "REGISTERED VOTERS", "TOTAL VOTES",
    "JEFFERSON COUNTY",
)

VOTE_METHODS = ("Election Day", "Mail-In", "Provisional", "Total")

# DOS office code -> office; the (USC|STH) contests carry the district in
# field 5 of the bulk row.
DOS_OFFICE = {
    "GOV": "Governor", "LTG": "Lieutenant Governor", "USC": "U.S. House",
    "STH": "State House", "STS": "State Senate",
}

# DOS candidate (last, first, middle) -> printed name.  U.S. House REP is
# normalized to "Glenn GT Thompson": the statewide county anchor keys
# GLENN GT THOMPSON and 30+ other 2026 county files / Jefferson's own
# 2022+2025 files print it that way.
DOS_NAME_FIX = {
    ("SHAPIRO", "JOSHUA", "D", ""): "Josh Shapiro",
    ("GARRITY", "STACY", "LORRAINE", ""): "Stacy Garrity",
    ("DAVIS", "AUSTIN", "", ""): "Austin Davis",
    ("RICHEY", "JASON", "L", ""): "Jason Richey",
    ("VENTRE", "JOHN", "", ""): "John Ventre",
    ("BILGER", "RAY", "", ""): "Ray Bilger",
    ("THOMPSON", "GLENN", "WILLIAM", "JR"): "Glenn GT Thompson",
    ("GOURLEY", "LISA", "M", ""): "Lisa Gourley",
    ("SMITH", "BRIAN", "A", ""): "Brian Smith",
    ("KOHLHEPP", "KIMBERLY", "", ""): "Kim Kohlhepp",
    ("ARMANINI", "MICHAEL", "J", ""): "Mike Armanini",
}

# DOS precinct code -> printed precinct name (matches the county SOVC's
# precinct labels, which this file has always used).
DOS_PRECINCT_NAMES = {
    "10": "Barnett Township", "20": "Beaver Township", "35": "Bell Township",
    "50": "Big Run Borough", "60": "Brockway Borough",
    "85": "Brookville Borough 1", "95": "Brookville Borough 2",
    "120": "Clover Township", "130": "Corsica Borough",
    "145": "Eldred Township", "160": "Falls Creek Borough",
    "165": "Falls Creek Borough 2", "170": "Gaskill Township",
    "180": "Heath Township", "190": "Henderson Township",
    "205": "Knox Township", "225": "McCalmont Township",
    "240": "Oliver Township", "275": "Perry Township",
    "315": "Pinecreek Township", "330": "Polk Township",
    "340": "Porter Township", "355": "Punxsutawney Borough 1",
    "375": "Punxsutawney Borough 2", "395": "Punxsutawney Borough 3",
    "475": "Reynoldsville Borough", "510": "Ringgold Township",
    "525": "Rose Township", "540": "Snyder Township",
    "550": "Summerville Borough", "565": "Sykesville Borough",
    "580": "Timblin Borough", "590": "Union Township",
    "605": "Warsaw Township", "620": "Washington Township",
    "655": "Winslow Township", "690": "Worthville Borough",
    "700": "Young Township",
}

CONTEST_ORDER = [
    "Governor", "Lieutenant Governor", "U.S. House", "State House",
    "State Senate", "Member of Democratic State Committee",
    "Member of Republican State Committee", "Democratic Party Chairperson",
]


def dos_statewide_rows() -> list[dict]:
    """Governor/US House/State House rows from the certified DOS bulk slice."""
    if not os.path.exists(DOS_SLICE):
        raise RuntimeError(f"DOS slice missing: {DOS_SLICE}")
    rows: list[dict] = []
    with open(DOS_SLICE, newline="", encoding="utf-8-sig") as fh:
        for fields in csv.reader(fh):
            if fields[1] != "P" or fields[2] != COUNTY_CODE:
                continue
            office = DOS_OFFICE.get(fields[8])
            if office is None:
                raise RuntimeError(f"unexpected DOS office {fields[8]!r}")
            key = (fields[11], fields[12], fields[13], fields[14])
            if key not in DOS_NAME_FIX:
                raise RuntimeError(f"unmapped DOS candidate {key!r}")
            name = DOS_NAME_FIX[key]
            precinct = DOS_PRECINCT_NAMES.get(fields[3])
            if precinct is None:
                raise RuntimeError(
                    f"unmapped DOS precinct code {fields[3]!r} "
                    f"({fields[22]!r})")
            district = ""
            if office in ("U.S. House", "State House", "State Senate"):
                district = str(int(fields[5]))
            rows.append({
                "county": "Jefferson", "precinct": precinct,
                "office": office, "district": district,
                "party": fields[9], "candidate": name,
                "votes": str(int(fields[15])),
                "election_day": "", "provisional": "", "absentee": "",
            })
    return rows


def normalize_office(raw: str) -> tuple[str, str]:
    upper = re.sub(r"\s+", " ", raw.upper()).strip()
    district = ""
    dm = DISTRICT_RE.search(upper)
    if dm:
        district = str(int(dm.group(1)))
        upper = DISTRICT_RE.sub("", upper).strip()
    for key, (norm, extract) in OFFICE_MAP.items():
        if upper.startswith(key):
            return (norm, district if extract else "")
    if upper.startswith("MEMBER OF THE "):
        # State Committee offices; the statewide county anchor / county
        # CSV name them WITHOUT "the".
        return (upper.replace("MEMBER OF THE ", "MEMBER OF ", 1)
                     .title().replace(" State Committee",
                                      " State Committee"), "")
    return (raw.title(), district)


def office_name(raw: str, party: str) -> str:
    upper = re.sub(r"\s+", " ", raw.upper()).strip()
    if upper.startswith("MEMBER OF THE DEMOCRATIC STATE"):
        return "Member of Democratic State Committee"
    if upper.startswith("MEMBER OF THE REPUBLICAN STATE"):
        return "Member of Republican State Committee"
    if upper.startswith("DEMOCRATIC PARTY CHAIRPERSON"):
        return "Democratic Party Chairperson"
    office, district = normalize_office(raw)
    if district:
        return district
    return office


def decode_rotated(cell: str) -> str:
    if not cell:
        return ""
    lines = cell.split("\n")
    decoded = [line[::-1] for line in lines]
    decoded.reverse()
    return " ".join(decoded).strip()


def finalize_candidate(name: str) -> str:
    name = name.replace(",", "").strip()
    if name.lower() in ("write-in", "write in"):
        return "Write-In Totals"
    parts = name.split()
    out = []
    for w in parts:
        if w.upper() in ("JR", "SR", "II", "III", "IV"):
            out.append(w.upper().replace("JR", "Jr.").replace("SR", "Sr."))
        elif "-" in w and w.upper() != w:
            out.append("-".join(p.capitalize() for p in w.split("-")))
        elif w[:2].upper() == "MC" and len(w) > 2:
            out.append("Mc" + w[2:].capitalize())
        else:
            out.append(w.capitalize())
    return " ".join(out)


def is_ballot_candidate(name: str) -> bool:
    upper = name.upper()
    if upper in ("TOTAL VOTES", "WRITE-IN", "WRITE IN", "WRITE-IN TOTALS"):
        return False
    if upper.startswith("QUALIFIED WRITE IN"):
        return False
    return True


def parse_table_candidate(header_cell: str) -> str:
    decoded = decode_rotated(header_cell)
    if not decoded:
        return ""
    decoded = re.sub(r"\((DEM|REP)\)", "", decoded).strip()
    decoded = re.sub(r"\s+", " ", decoded)
    return decoded


def is_non_candidate_column(name: str) -> bool:
    """True for turnout/total/write-in-aggregate columns to skip."""
    upper = name.upper()
    if upper in (
        "TIMES CAST", "REGISTERED VOTERS", "TOTAL VOTES", "VOTERS CAST",
        "VOTERS REGISTERED", "CAST VOTERS", "REGISTERED CAST",
    ):
        return True
    # "Qualified Write In <name>" columns are individual write-in candidate
    # columns; the rotated decode produces jumbled word order like
    # "Stacy Qualified Garrity Write In". Skip any column whose decoded
    # name contains QUALIFIED or WRITE IN / WRITE-IN / UNRESOLVED.
    if "QUALIFIED" in upper or "WRITE IN" in upper or "WRITE-IN" in upper:
        return True
    if "UNRESOLVED" in upper or "UNSOLVED" in upper:
        return True
    return False


def clean_precinct(name: str) -> str:
    return re.sub(r"\s+", " ", name).strip()


def parse_contest(pdf, start_page: int, end_page: int, office: str, district: str,
                  party: str) -> list[dict]:
    candidate_votes: dict[str, dict[str, int]] = {}
    precinct_order: list[str] = []
    candidate_order: list[str] = []

    n = len(pdf.pages)
    last = min(end_page, n)
    for i in range(start_page, last):
        page = pdf.pages[i]
        tables = page.extract_tables()
        for tbl in tables:
            if not tbl or len(tbl) < 2:
                continue
            header = tbl[0]
            col_candidates: list[tuple[int, str]] = []
            for ci, cell in enumerate(header):
                if ci == 0:
                    continue
                if cell is None:
                    continue
                name = parse_table_candidate(cell)
                if not name:
                    continue
                if is_non_candidate_column(name):
                    continue
                col_candidates.append((ci, name))
            if not col_candidates:
                continue
            current_precinct = None
            for row in tbl[1:]:
                if not row:
                    continue
                label = (row[0] or "").strip()
                if not label:
                    continue
                upper_label = label.upper()
                if upper_label in ("JEFFERSON COUNTY", "PAGE") or upper_label.startswith("PAGE"):
                    continue
                if "JEFFERSON COUNTY - TOTAL" in upper_label or "COUNTY - TOTAL" in upper_label:
                    continue
                if upper_label.startswith("CUMULATIVE"):
                    continue
                # Jefferson format: each precinct has 4 sub-rows
                # (Election Day, Mail-In, Provisional, Total). Only emit
                # the Total sub-row's votes.
                if label in VOTE_METHODS:
                    if label != "Total" or current_precinct is None:
                        continue
                    for ci, cname in col_candidates:
                        if ci >= len(row):
                            continue
                        v = (row[ci] or "").strip()
                        if not v:
                            continue
                        try:
                            votes = int(v.replace(",", ""))
                        except ValueError:
                            continue
                        final_name = finalize_candidate(cname)
                        if current_precinct not in candidate_votes:
                            candidate_votes[current_precinct] = {}
                            precinct_order.append(current_precinct)
                        if final_name not in candidate_votes[current_precinct]:
                            candidate_votes[current_precinct][final_name] = 0
                        if final_name not in candidate_order:
                            candidate_order.append(final_name)
                        candidate_votes[current_precinct][final_name] += votes
                    current_precinct = None
                else:
                    current_precinct = clean_precinct(label)

    out: list[dict] = []
    for precinct in precinct_order:
        cvotes = candidate_votes[precinct]
        for cand in candidate_order:
            if is_non_candidate_column(cand) or not is_ballot_candidate(cand):
                continue
            out.append({
                "county": "Jefferson", "precinct": precinct,
                "office": office, "district": district,
                "party": party, "candidate": cand,
                "votes": cvotes.get(cand, 0), "election_day": "",
                "provisional": "", "absentee": "",
            })
    return out


def pdf_contest_rows(pdf_path: str) -> list[dict]:
    """Member of State Committee + party chairperson rows from the SOVC."""
    proc = subprocess.run(
        ["pdftotext", "-layout", pdf_path, "-"],
        capture_output=True, text=True, check=True,
    )
    text = proc.stdout
    line_to_page: list[int] = []
    current_page = 0
    for line in text.split("\n"):
        pm = re.search(r"Page:\s+(\d+)\s+of\s+\d+", line)
        if pm:
            current_page = int(pm.group(1)) - 1
        line_to_page.append(current_page)

    header_pages: list[tuple[int, str, str]] = []
    seen: set[tuple[int, str]] = set()
    for li, line in enumerate(text.split("\n")):
        m = HEADER_RE.match(line.strip())
        if not m:
            continue
        office_raw = m.group(1)
        district_grp = m.group(2) or ""
        party = m.group(3)
        office_raw_full = office_raw
        if district_grp:
            office_raw_full = f"{office_raw} -{ district_grp} District"
        page_idx = line_to_page[li] if li < len(line_to_page) else 0
        key = (page_idx, office_raw_full)
        if key in seen:
            continue
        seen.add(key)
        header_pages.append((page_idx, office_raw_full, party))

    header_pages.sort(key=lambda t: t[0])
    rows: list[dict] = []
    with pdfplumber.open(pdf_path) as pdf:
        n_pages = len(pdf.pages)
        for idx, (page_idx, office_raw_full, party) in enumerate(header_pages):
            upper = re.sub(r"\s+", " ", office_raw_full.upper())
            if not KEEP_OFFICE_RE.match(upper):
                continue
            office = office_name(office_raw_full, party)
            district = ""
            dm = DISTRICT_RE.search(office_raw_full)
            if dm:
                district = str(int(dm.group(1)))
            end_page = (header_pages[idx + 1][0]
                        if idx + 1 < len(header_pages) else n_pages)
            rows.extend(parse_contest(pdf, page_idx, end_page, office,
                                      district, party))
    return rows


def merge_and_order(rows: list[dict]) -> list[dict]:
    """Precinct-major ordering: within a precinct, contests in CONTEST_ORDER,
    then DEM candidates before REP."""
    party_rank = {"DEM": 0, "REP": 1}
    by_office_order = {o: i for i, o in enumerate(CONTEST_ORDER)}
    rows.sort(key=lambda r: (
        r["precinct"],
        by_office_order.get(r["office"], 99),
        party_rank.get(r["party"], 9),
        r["candidate"].lower(),
    ))
    return rows


def parse_jefferson(pdf_path: str) -> list[dict]:
    rows = dos_statewide_rows()
    rows.extend(pdf_contest_rows(pdf_path))
    return merge_and_order(rows)


def main(argv: list[str]) -> None:
    if len(argv) != 3:
        sys.exit(f"Usage: {Path(argv[0]).name} <input.pdf> <output.csv>")
    pdf_path = Path(argv[1])
    out_path = Path(argv[2])
    if not pdf_path.exists():
        sys.exit(f"Missing PDF: {pdf_path}")
    rows = parse_jefferson(str(pdf_path))
    with out_path.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDNAMES)
        writer.writeheader()
        for r in rows:
            writer.writerow({k: r.get(k, "") for k in FIELDNAMES})
    precincts = len({r["precinct"] for r in rows})
    print(f"Wrote {len(rows)} rows across {precincts} precincts to {out_path}")


if __name__ == "__main__":
    main(sys.argv)
