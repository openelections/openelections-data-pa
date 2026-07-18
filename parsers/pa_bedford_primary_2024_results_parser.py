#!/usr/bin/env python3
"""Parse Bedford County PA 2024 Primary precinct results.

Source: Bedford PA OfficalPrimaryStatementOfVotesCastRPT.pdf
(SOVC report with rotated column headers. Each contest spans multiple
pages; each page has 1-2 tables with candidate columns. Candidate names
appear in headers rotated 180° — we decode by reversing line order and
each line's characters. Vote method rows: Election Day/Mail-In/
Provisional/Total. We take only Total rows for the final per-candidate
votes. Named ballot candidates + a single Write-In Totals entry per
precinct are emitted; qualified write-in candidate columns and the
"Total Votes" column are skipped.)

Usage:
    python parsers/pa_bedford_primary_2024_results_parser.py <input.pdf> <output.csv>
"""

import csv
import re
import sys
from pathlib import Path

import pdfplumber


OFFICE_MAP = {
    "PRESIDENT OF THE UNITED STATES": ("President", False),
    "UNITED STATES SENATOR": ("U.S. Senate", False),
    "ATTORNEY GENERAL": ("Attorney General", False),
    "AUDITOR GENERAL": ("Auditor General", False),
    "STATE TREASURER": ("State Treasurer", False),
    "REPRESENTATIVE IN CONGRESS": ("U.S. House", True),
    "SENATOR IN THE GENERAL ASSEMBLY": ("State Senate", True),
    "REPRESENTATIVE IN THE GENERAL ASSEMBLY": ("State House", True),
}

DISTRICT_RE = re.compile(r"-\s*(\d+)\w*\s+DISTRICT\b", re.IGNORECASE)

HEADER_RE = re.compile(
    r"^(President of the United States|United States Senator|Attorney General"
    r"|Auditor General|State Treasurer|Representative in Congress"
    r"|Representative in the General Assembly|Senator in the General Assembly)"
    r"\s*(?:-\s*(\d+)\w*\s+District)?\s*\((DEM|REP)\)\s*\(Vote for\s+\d+\)\s*$"
)

FIELDNAMES = [
    "county", "precinct", "office", "district", "party",
    "candidate", "votes", "election_day", "provisional", "absentee",
]

SKIP_HEADER_TOKENS = (
    "PRECINCT", "TIMES CAST", "REGISTERED VOTERS", "TOTAL VOTES",
    "BEDFORD COUNTY",
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
    """Decode a 180°-rotated header cell.

    The cell text is split into lines, each char-reversed, and the line
    order is reversed. Returns the decoded text joined by spaces."""
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
    """A real ballot candidate (not a write-in variant, not Total Votes)."""
    upper = name.upper()
    if upper in ("TOTAL VOTES", "WRITE-IN", "WRITE IN", "WRITE-IN TOTALS"):
        return False
    if upper.startswith("QUALIFIED WRITE IN"):
        return False
    return True


def parse_table_candidate(header_cell: str) -> str:
    """Given a header cell, return the decoded candidate name (without
    party marker) or "" if it's not a candidate column."""
    decoded = decode_rotated(header_cell)
    if not decoded:
        return ""
    # Strip "(DEM)" / "(REP)" party markers
    decoded = re.sub(r"\((DEM|REP)\)", "", decoded).strip()
    decoded = re.sub(r"\s+", " ", decoded)
    return decoded


def parse_contest(pdf, start_page: int, office_raw: str, party: str
                  ) -> list[dict]:
    office, district = normalize_office(office_raw)
    # Collect candidate data across all pages of this contest.
    # candidate_votes[precinct][candidate] = total_votes
    candidate_votes: dict[str, dict[str, int]] = {}
    precinct_order: list[str] = []
    candidate_order: list[str] = []

    i = start_page
    n = len(pdf.pages)
    while i < n:
        page = pdf.pages[i]
        text = page.extract_text() or ""
        # Stop if we hit the next contest header
        first_nonblank = ""
        for line in text.split("\n"):
            s = line.strip()
            if s:
                first_nonblank = s
                break
        if i != start_page and HEADER_RE.match(first_nonblank):
            break
        tables = page.extract_tables()
        for tbl in tables:
            if not tbl or len(tbl) < 2:
                continue
            header = tbl[0]
            # Find candidate columns (skip Precinct column 0)
            col_candidates: list[tuple[int, str]] = []
            for ci, cell in enumerate(header):
                if ci == 0:
                    continue
                if cell is None:
                    continue
                name = parse_table_candidate(cell)
                if not name:
                    continue
                if name.upper() in ("TIMES CAST", "REGISTERED VOTERS"):
                    continue
                col_candidates.append((ci, name))
            if not col_candidates:
                continue
            # Walk rows; track current precinct; capture Total row
            current_precinct = None
            for row in tbl[1:]:
                if not row:
                    continue
                label = (row[0] or "").strip()
                if not label:
                    continue
                if label in VOTE_METHODS:
                    if label != "Total" or current_precinct is None:
                        continue
                    for ci, cname in col_candidates:
                        if ci >= len(row):
                            continue
                        v = (row[ci] or "").strip()
                        try:
                            votes = int(v.replace(",", "")) if v else 0
                        except ValueError:
                            votes = 0
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
                elif label.upper() == "BEDFORD COUNTY":
                    continue
                elif label.upper().startswith("PAGE"):
                    continue
                else:
                    # precinct name (may have wrapped; join with next row
                    # if it's a continuation). For now, take as-is.
                    current_precinct = label
        i += 1
        # Safety: stop after 200 pages
        if i - start_page > 200:
            break

    # Merge write-in variants into Write-In Totals
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
                "county": "Bedford", "precinct": precinct,
                "office": office, "district": district,
                "party": party, "candidate": cand,
                "votes": merged[cand], "election_day": "",
                "provisional": "", "absentee": "",
            })
    return out


def parse_bedford(pdf_path: Path) -> list[dict]:
    rows: list[dict] = []
    with pdfplumber.open(str(pdf_path)) as pdf:
        n = len(pdf.pages)
        for i in range(n):
            page = pdf.pages[i]
            text = page.extract_text() or ""
            for line in text.split("\n"):
                m = HEADER_RE.match(line.strip())
                if m:
                    office_raw = m.group(1)
                    district_grp = m.group(2) or ""
                    party = m.group(3)
                    office_raw_full = office_raw
                    if district_grp:
                        office_raw_full = f"{office_raw} -{district_grp} District"
                    rows.extend(parse_contest(pdf, i, office_raw_full, party))
                    break
    return rows


def main(argv: list[str]) -> None:
    if len(argv) != 3:
        sys.exit(f"Usage: {Path(argv[0]).name} <input.pdf> <output.csv>")
    pdf_path = Path(argv[1])
    out_path = Path(argv[2])
    if not pdf_path.exists():
        sys.exit(f"Missing PDF: {pdf_path}")
    rows = parse_bedford(pdf_path)
    with out_path.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDNAMES)
        writer.writeheader()
        for r in rows:
            writer.writerow({k: r.get(k, "") for k in FIELDNAMES})
    print(f"Wrote {len(rows)} rows to {out_path}")


if __name__ == "__main__":
    main(sys.argv)