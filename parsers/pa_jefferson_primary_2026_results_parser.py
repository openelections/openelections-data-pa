#!/usr/bin/env python3
"""Parse Jefferson County PA 2026 Primary precinct results.

Source: Jefferson County Certified SOVC Report 2026 Primary 06.08.2026.pdf
(SOVC crosstab with rotated column headers — same shape as the 2024 primary
report but with 2026's contest list: Governor, Lieutenant Governor,
Representative in Congress, Senator in the General Assembly, Representative
in the General Assembly, and per-precinct Member of County Committee races.

Adapted from pa_jefferson_primary_2024_results_parser.py with an updated
HEADER_RE / OFFICE_MAP for the 2026 contest names.

Usage:
    python parsers/pa_jefferson_primary_2026_results_parser.py <input.pdf> <output.csv>
"""

import csv
import re
import subprocess
import sys
from pathlib import Path

import pdfplumber


OFFICE_MAP = {
    "GOVERNOR": ("Governor", False),
    "LIEUTENANT GOVERNOR": ("Lieutenant Governor", False),
    "REPRESENTATIVE IN CONGRESS": ("U.S. House", True),
    "SENATOR IN THE GENERAL ASSEMBLY": ("State Senate", True),
    "REPRESENTATIVE IN THE GENERAL ASSEMBLY": ("State House", True),
}

DISTRICT_RE = re.compile(r"-\s*(\d+)\w*\s+DISTRICT\b", re.IGNORECASE)

HEADER_RE = re.compile(
    r"^(Governor|Lieutenant Governor|Representative in Congress"
    r"|Representative in the General Assembly|Senator in the General Assembly"
    r"|Member of the (?:Democratic|Republican) County Committee .+?)"
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
    return (raw.title(), district)


def decode_rotated(cell: str) -> str:
    if not cell:
        return ""
    lines = cell.split("\n")
    decoded = [line[::-1] for line in lines]
    decoded.reverse()
    return " ".join(decoded).strip()


def finalize_candidate(name: str) -> str:
    name = name.replace(",", "").strip()
    if name.lower() == "write-in" or name.lower() == "write in":
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
    """Collapse embedded newlines and whitespace in a precinct label."""
    return re.sub(r"\s+", " ", name).strip()


def parse_contest(pdf, start_page: int, end_page: int, office_raw: str, party: str
                  ) -> list[dict]:
    office, district = normalize_office(office_raw)
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
        merged: dict[str, int] = {}
        order: list[str] = []
        for cand in candidate_order:
            v = cvotes.get(cand, 0)
            if cand == "Write-In Totals":
                if "Write-In Totals" not in merged:
                    merged["Write-In Totals"] = 0
                    order.append("Write-In Totals")
                merged["Write-In Totals"] += v
            elif cand == "Total Votes":
                continue
            else:
                if cand not in merged:
                    merged[cand] = 0
                    order.append(cand)
                merged[cand] += v
        for cand in order:
            out.append({
                "county": "Jefferson", "precinct": precinct,
                "office": office, "district": district,
                "party": party, "candidate": cand,
                "votes": merged[cand], "election_day": "",
                "provisional": "", "absentee": "",
            })
    return out


def parse_jefferson(pdf_path: Path) -> list[dict]:
    rows: list[dict] = []
    # pdftotext sees the contest header banner (e.g. "Governor (DEM) (Vote
    # for 1)") which pdfplumber's extract_text misses on these pages. Use
    # pdftotext -layout to find header line numbers, then map each to a
    # pdfplumber page index by counting "Page: N of M" markers.
    proc = subprocess.run(
        ["pdftotext", "-layout", str(pdf_path), "-"],
        capture_output=True, text=True, check=True,
    )
    text = proc.stdout
    # Build a map: line_index -> pdfplumber page index (0-based). The
    # "Page: N of M" marker precedes each page's content.
    line_to_page: list[int] = []
    current_page = 0
    for line in text.split("\n"):
        # pdftotext prefixes page markers with a form-feed char (\x0c).
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
            office_raw_full = f"{office_raw} -{district_grp} District"
        page_idx = line_to_page[li] if li < len(line_to_page) else 0
        key = (page_idx, office_raw_full)
        if key in seen:
            continue
        seen.add(key)
        header_pages.append((page_idx, office_raw_full, party))

    # Sort by start page and compute end page for each contest (the page
    # index of the next contest header, or total pages for the last one).
    header_pages.sort(key=lambda t: t[0])
    with pdfplumber.open(str(pdf_path)) as pdf:
        n_pages = len(pdf.pages)
        for idx, (page_idx, office_raw_full, party) in enumerate(header_pages):
            end_page = header_pages[idx + 1][0] if idx + 1 < len(header_pages) else n_pages
            rows.extend(parse_contest(pdf, page_idx, end_page, office_raw_full, party))
    return rows


def main(argv: list[str]) -> None:
    if len(argv) != 3:
        sys.exit(f"Usage: {Path(argv[0]).name} <input.pdf> <output.csv>")
    pdf_path = Path(argv[1])
    out_path = Path(argv[2])
    if not pdf_path.exists():
        sys.exit(f"Missing PDF: {pdf_path}")
    rows = parse_jefferson(pdf_path)
    with out_path.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDNAMES)
        writer.writeheader()
        for r in rows:
            writer.writerow({k: r.get(k, "") for k in FIELDNAMES})
    precincts = len({r["precinct"] for r in rows})
    print(f"Wrote {len(rows)} rows across {precincts} precincts to {out_path}")


if __name__ == "__main__":
    main(sys.argv)