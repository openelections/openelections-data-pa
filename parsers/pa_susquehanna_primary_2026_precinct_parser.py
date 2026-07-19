#!/usr/bin/env python3
"""Parse Susquehanna County PA 2026 Primary precinct-level results.

Source: Susquehanna County 2026-Primary-Official-Precinct-Report.pdf

The PDF is a two-column "Precinct Summary Report" with one precinct per
2-3 pages. Each contest block looks like::

    OFFICE NAME (possibly multi-line, e.g. "REPRESENTATIVE IN THE GENERAL
    ASSEMBLY 111TH\\nDISTRICT")
    (PARTY)
    Vote For N
    Total Votes N
     NAME  votes  percentage
     NAME (WI)  votes  percentage
     Write-in  votes  percentage

The two columns are at fixed horizontal positions: left column starts at
col 0, right column starts at col 70. Each column is processed as an
independent stream. Per-precinct committee races
("<precinct> MBR DEM/REP COMMITTEE") are skipped. Named write-in
candidates "(WI)" and the literal "Write-in" row are aggregated into a
single "Write-In Totals" row per (precinct, office, district, party).

Usage:
    uv run python parsers/pa_susquehanna_primary_2026_precinct_parser.py <input.pdf> <output.csv>
"""

from __future__ import annotations

import csv
import re
import subprocess
import sys
from pathlib import Path

COLUMN_SPLIT = 70

PARTY_LINE_RE = re.compile(r"^\((DEMOCRATIC|REPUBLICAN|DEMOCRAT|REPUBLIC)\)\s*$",
                            re.IGNORECASE)
VOTE_FOR_RE = re.compile(r"^Vote For\s+(\d+)\s*$", re.IGNORECASE)
TOTAL_VOTES_RE = re.compile(r"^Total Votes\s+(\d[\d,]*)\s*$", re.IGNORECASE)
REGISTERED_VOTERS_RE = re.compile(
    r"^Registered Voters\s+(\d[\d,]*)\s*-\s*Total Ballots\s+(\d[\d,]*)\s*:",
    re.IGNORECASE,
)
# Candidate row: "NAME  votes  percentage" (left col has leading space, right
# col doesn't after the split). Name may include "(WI)" suffix.
CANDIDATE_ROW_RE = re.compile(
    r"^(.+?)\s+(\d[\d,]*)\s+\d+(?:\.\d+)?%\s*$"
)
PAGE_HEADER_RE = re.compile(
    r"^\s*(Precinct Summary Report|SUSQUEHANNA COUNTY, PA|PRIMARY ELECTION|"
    r"MAY 19, 2026|Election Day|OFFICIAL RESULTS|Page\s+\d+/\d+|"
    r"Date:|Time:)",
    re.IGNORECASE,
)

DISTRICT_ORDINAL_RE = re.compile(
    r"\b(\d+)(?:ST|ND|RD|TH)\s+(?:LEGISLATIVE\s+|CONGRESSIONAL\s+|SENATORIAL\s+)?"
    r"DIS(?:TRICT|T)?\b",
    re.IGNORECASE,
)

STATEWIDE_OFFICES: dict[str, tuple[str, bool]] = {
    "PRESIDENT OF THE UNITED STATES": ("President", False),
    "UNITED STATES SENATOR": ("U.S. Senate", False),
    "GOVERNOR": ("Governor", False),
    "LIEUTENANT GOVERNOR": ("Lieutenant Governor", False),
    "ATTORNEY GENERAL": ("Attorney General", False),
    "AUDITOR GENERAL": ("Auditor General", False),
    "STATE TREASURER": ("State Treasurer", False),
    "REPRESENTATIVE IN CONGRESS": ("U.S. House", True),
    "SENATOR IN THE GENERAL ASSEMBLY": ("State Senate", True),
    "REPRESENTATIVE IN THE GENERAL ASSEMBLY": ("State House", True),
    "MEMBER OF DEMOCRATIC STATE COMMITTEE": ("Member of Democratic State Committee", False),
    "MEMBER OF REPUBLICAN STATE COMMITTEE": ("Member of Republican State Committee", False),
    "MEMBER OF THE DEMOCRATIC STATE COMMITTEE": ("Member of Democratic State Committee", False),
    "MEMBER OF THE REPUBLICAN STATE COMMITTEE": ("Member of Republican State Committee", False),
    "DEMOCRATIC STATE COMMITTEE": ("Member of Democratic State Committee", False),
    "REPUBLICAN STATE COMMITTEE": ("Member of Republican State Committee", False),
}

PARTY_NORMALIZE = {
    "DEMOCRATIC": "DEM",
    "DEMOCRAT": "DEM",
    "REPUBLICAN": "REP",
    "REPUBLIC": "REP",
}

FIELDNAMES = [
    "county", "precinct", "office", "district", "party", "candidate",
    "votes", "election_day", "provisional", "absentee",
]

_ROMAN_RE = re.compile(r"^[IVX]+$")


def _finalize_candidate(raw: str) -> str:
    s = raw.strip()
    s = re.sub(r"\s*\(WI\)\s*$", "", s, flags=re.IGNORECASE).strip()
    if s.upper() in ("WRITE-IN", "WRITE IN"):
        return "Write-In Totals"
    s = s.replace(",", "").strip()
    out = []
    for w in s.split():
        if _ROMAN_RE.match(w.upper()):
            out.append(w.upper())
        elif w.upper() in ("JR", "SR"):
            out.append(w.upper().replace("JR", "Jr.").replace("SR", "Sr."))
        elif len(w) >= 3 and w[:2].lower() == "mc":
            out.append("Mc" + w[2:].capitalize())
        else:
            out.append(w.capitalize())
    return " ".join(out)


def _normalize_office(raw: str) -> tuple[str, str]:
    upper = re.sub(r"\s+", " ", raw.upper()).strip()
    dm = DISTRICT_ORDINAL_RE.search(upper)
    district = str(int(dm.group(1))) if dm else ""
    key = DISTRICT_ORDINAL_RE.sub("", upper).strip() if dm else upper
    key = re.sub(r"\s+", " ", key).strip()
    if key in STATEWIDE_OFFICES:
        norm, extract = STATEWIDE_OFFICES[key]
        return (norm, district if extract else "")
    for k, (norm, extract) in STATEWIDE_OFFICES.items():
        if key == k or key.startswith(k + " "):
            return (norm, district if extract else "")
    return (raw.title(), district)


def _is_per_precinct_committee(office_text: str) -> bool:
    upper = office_text.upper()
    if "STATE COMMITTEE" in upper:
        return False
    return "COMMITTEE" in upper


def _is_office_name_line(s: str) -> bool:
    """Heuristic: ALL CAPS, no parens, no digits-only, not a page header."""
    if not s:
        return False
    if s[0].isspace():
        return False
    if "(" in s or ")" in s:
        return False
    if s.upper() != s:
        return False
    if PAGE_HEADER_RE.match(s):
        return False
    if VOTE_FOR_RE.match(s):
        return False
    if TOTAL_VOTES_RE.match(s):
        return False
    if REGISTERED_VOTERS_RE.match(s):
        return False
    if CANDIDATE_ROW_RE.match(s):
        return False
    if not re.search(r"[A-Z]", s):
        return False
    return True


def _process_column(lines: list[str], county: str, precinct: str,
                    writein_agg: dict) -> list[dict]:
    """Process one column's lines into candidate rows."""
    rows: list[dict] = []
    current_office = ""
    current_district = ""
    current_party = ""
    skip_block = False
    office_buffer: list[str] = []

    i = 0
    n = len(lines)
    while i < n:
        line = lines[i]
        s = line.rstrip()
        stripped = s.strip()
        if not stripped:
            i += 1
            continue
        pm = PARTY_LINE_RE.match(stripped)
        if pm:
            current_party = PARTY_NORMALIZE.get(pm.group(1).upper(), "")
            i += 1
            continue
        vm = VOTE_FOR_RE.match(stripped)
        if vm:
            i += 1
            continue
        tm = TOTAL_VOTES_RE.match(stripped)
        if tm:
            i += 1
            continue
        cm = CANDIDATE_ROW_RE.match(s)
        if cm:
            if skip_block or not current_office:
                i += 1
                continue
            name_raw = cm.group(1).strip()
            votes = int(cm.group(2).replace(",", ""))
            is_writein = ("(WI)" in name_raw.upper()
                          or name_raw.upper().startswith("WRITE-IN"))
            if is_writein:
                key = (precinct, current_office, current_district, current_party)
                agg = writein_agg.setdefault(key, {
                    "county": county, "precinct": precinct,
                    "office": current_office, "district": current_district,
                    "party": current_party, "candidate": "Write-In Totals",
                    "votes": 0, "election_day": 0, "provisional": 0, "absentee": 0,
                })
                agg["votes"] += votes
                agg["election_day"] += votes
                i += 1
                continue
            candidate = _finalize_candidate(name_raw)
            rows.append({
                "county": county, "precinct": precinct,
                "office": current_office, "district": current_district,
                "party": current_party, "candidate": candidate,
                "votes": votes, "election_day": votes,
                "provisional": 0, "absentee": 0,
            })
            i += 1
            continue
        if _is_office_name_line(stripped):
            office_buffer = [stripped]
            j = i + 1
            while j < n:
                nxt = lines[j].rstrip()
                nxt_stripped = nxt.strip()
                if not nxt_stripped:
                    j += 1
                    continue
                if (PARTY_LINE_RE.match(nxt_stripped)
                        or VOTE_FOR_RE.match(nxt_stripped)
                        or TOTAL_VOTES_RE.match(nxt_stripped)
                        or CANDIDATE_ROW_RE.match(nxt)
                        or REGISTERED_VOTERS_RE.match(nxt_stripped)):
                    break
                if not _is_office_name_line(nxt_stripped):
                    break
                office_buffer.append(nxt_stripped)
                j += 1
            office_text = " ".join(office_buffer)
            if _is_per_precinct_committee(office_text):
                skip_block = True
                current_office = ""
                i = j
                continue
            skip_block = False
            current_office, current_district = _normalize_office(office_text)
            i = j
            continue
        i += 1
    return rows


def parse_precincts(pdf_path: Path) -> list[dict]:
    proc = subprocess.run(
        ["pdftotext", "-layout", str(pdf_path), "-"],
        capture_output=True, text=True, check=True,
    )
    all_lines = proc.stdout.split("\n")

    # Split into pages on "Precinct Summary Report" banner.
    pages: list[list[str]] = []
    current: list[str] = []
    for line in all_lines:
        if "Precinct Summary Report" in line and current:
            pages.append(current)
            current = []
        current.append(line)
    if current:
        pages.append(current)

    rows: list[dict] = []
    writein_agg: dict[tuple, dict] = {}
    seen_precinct_meta: set[tuple] = set()

    for page_lines in pages:
        precinct = None
        registered_voters = None
        ballots_cast = None
        next_is_precinct = False
        for line in page_lines:
            s = line.strip()
            if not s:
                continue
            if "Election Day" in s and len(s) < 30:
                next_is_precinct = True
                continue
            if next_is_precinct:
                precinct = s
                next_is_precinct = False
                continue
            rm = REGISTERED_VOTERS_RE.match(s)
            if rm:
                registered_voters = int(rm.group(1).replace(",", ""))
                ballots_cast = int(rm.group(2).replace(",", ""))
                break

        if precinct is None:
            continue

        # Strip page header: keep only lines from "Registered Voters" onward.
        content_start = 0
        for idx, line in enumerate(page_lines):
            if REGISTERED_VOTERS_RE.match(line.strip()):
                content_start = idx
                break
        page_lines = page_lines[content_start:]

        if (precinct, "Registered Voters") not in seen_precinct_meta:
            if registered_voters is not None:
                rows.append({
                    "county": "Susquehanna", "precinct": precinct,
                    "office": "Registered Voters", "district": "",
                    "party": "", "candidate": "",
                    "votes": registered_voters, "election_day": "",
                    "provisional": "", "absentee": "",
                })
                seen_precinct_meta.add((precinct, "Registered Voters"))
        if (precinct, "Ballots Cast") not in seen_precinct_meta:
            if ballots_cast is not None:
                rows.append({
                    "county": "Susquehanna", "precinct": precinct,
                    "office": "Ballots Cast", "district": "",
                    "party": "", "candidate": "",
                    "votes": ballots_cast, "election_day": ballots_cast,
                    "provisional": "", "absentee": "",
                })
                seen_precinct_meta.add((precinct, "Ballots Cast"))

        left_lines: list[str] = []
        right_lines: list[str] = []
        for line in page_lines:
            if len(line) < COLUMN_SPLIT:
                left_lines.append(line)
                right_lines.append("")
            else:
                left_lines.append(line[:COLUMN_SPLIT])
                right_lines.append(line[COLUMN_SPLIT:])

        rows.extend(_process_column(left_lines, "Susquehanna", precinct, writein_agg))
        rows.extend(_process_column(right_lines, "Susquehanna", precinct, writein_agg))

    rows.extend(writein_agg.values())

    # Deduplicate identical rows (defensive — same contest shouldn't repeat
    # across pages, but guard anyway).
    seen: set[tuple] = set()
    deduped: list[dict] = []
    for r in rows:
        key = (r["precinct"], r["office"], r["district"], r["party"],
               r["candidate"], r["votes"])
        if key in seen:
            continue
        seen.add(key)
        deduped.append(r)
    return deduped


def main(argv: list[str]) -> None:
    if len(argv) != 3:
        sys.exit(f"Usage: {Path(argv[0]).name} <input.pdf> <output.csv>")
    pdf_path = Path(argv[1])
    out_path = Path(argv[2])
    if not pdf_path.exists():
        sys.exit(f"Missing PDF: {pdf_path}")
    rows = parse_precincts(pdf_path)
    with out_path.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDNAMES)
        w.writeheader()
        for r in rows:
            w.writerow(r)
    offices = len({(r["office"], r["district"]) for r in rows})
    precincts = len({r["precinct"] for r in rows if r["precinct"]})
    print(f"Wrote {len(rows)} rows across {offices} contests / "
          f"{precincts} precincts to {out_path}")


if __name__ == "__main__":
    main(sys.argv)