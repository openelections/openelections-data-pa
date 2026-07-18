#!/usr/bin/env python3
"""Parse Lancaster County PA 2026 Primary precinct results.

Source: Lancaster County Precinct Results.pdf — a "Detailed Results
Report" with one precinct per ~7-page block. Each page repeats the
precinct header (``NNNN <name>``) at the top. Contest blocks have the
shape::

    GOVERNOR
    Vote For 1
    TOTAL
    Josh Shapiro 110
    Write-In 0
    SCATTERED 0
    Total Votes Cast 110

Party is NOT printed in the contest header — statewide/federal offices
(GOVERNOR, LIEUTENANT GOVERNOR, REPRESENTATIVE IN CONGRESS, REPRESENTATIVE
IN THE GENERAL ASSEMBLY) appear twice per precinct, first as the DEM
contest then as the REP contest. We track the occurrence count per
(precinct, office_key) and assign DEM/REP accordingly. Member-of-State-
Committee contests carry the party in the office name and don't need
the occurrence heuristic.

Per-precinct committee races (DEMOCRATIC COMMITTEEPERSON, REPUBLICAN
COMMITTEEMAN, REPUBLICAN COMMITTEEWOMAN, …) are skipped — they're
local party positions and OpenElections doesn't carry them.

Usage:
    uv run python parsers/pa_lancaster_primary_2026_results_parser.py <input.pdf> <output.csv>
"""

from __future__ import annotations

import csv
import re
import sys
from pathlib import Path

import natural_pdf as npdf

from electionware_primary_np import (
    PRIMARY_FIELDNAMES,
    STATEWIDE_OFFICES,
    _finalize_candidate,
)

# Precinct header: "0100 City 1" or "0708-2 CV" — 4-digit code followed by
# either a space or a hyphen (the split-suffix form like "0708-2").
PRECINCT_RE = re.compile(r"^\d{4}[-\s]")

# "Vote For N" line that follows the office header.
VOTE_FOR_RE = re.compile(r"^Vote For\s+(\d+)\s*$", re.IGNORECASE)

# District suffix: "REPRESENTATIVE IN CONGRESS 11TH District"
DISTRICT_RE = re.compile(
    r"\b(\d+)(?:ST|ND|RD|TH)\s+(?:CONGRESSIONAL\s+|LEGISLATIVE\s+|SENATORIAL\s+)?DISTRICT\b",
    re.IGNORECASE,
)

# Per-precinct committee races — Lancaster-specific naming.
SKIP_OFFICE_PREFIXES = (
    "DEMOCRATIC COMMITTEEPERSON",
    "DEMOCRATIC COMMITTEEMAN",
    "DEMOCRATIC COMMITTEEWOMAN",
    "REPUBLICAN COMMITTEEPERSON",
    "REPUBLICAN COMMITTEEMAN",
    "REPUBLICAN COMMITTEEWOMAN",
)

# Offices that come in DEM/REP pairs (no party in name). The occurrence
# counter per precinct decides party (1st=DEM, 2nd=REP).
PAIRED_OFFICES = {
    "GOVERNOR",
    "LIEUTENANT GOVERNOR",
    "REPRESENTATIVE IN CONGRESS",
    "REPRESENTATIVE IN THE GENERAL ASSEMBLY",
}

# Office name normalization. Returns (canonical_office, district, party)
# where party is non-empty only for "Member of … State Committee" (party
# is encoded in the office name itself).
def _normalize_office(raw: str) -> tuple[str, str, str]:
    s = raw.strip()
    upper = s.upper()
    if "MEMBER OF THE DEMOCRATIC STATE COMMITTEE" in upper or \
       "MEMBER OF DEMOCRATIC STATE COMMITTEE" in upper:
        return ("Member of Democratic State Committee", "", "DEM")
    if "MEMBER OF THE REPUBLICAN STATE COMMITTEE" in upper or \
       "MEMBER OF REPUBLICAN STATE COMMITTEE" in upper:
        return ("Member of Republican State Committee", "", "REP")
    dm = DISTRICT_RE.search(upper)
    district = str(int(dm.group(1))) if dm else ""
    key = DISTRICT_RE.sub("", upper).strip() if dm else upper
    if key in STATEWIDE_OFFICES:
        norm, extract = STATEWIDE_OFFICES[key]
        return (norm, district if extract else "", "")
    for k, (norm, extract) in STATEWIDE_OFFICES.items():
        if key == k or key.startswith(k + " "):
            return (norm, district if extract else "", "")
    # Local fallback: title-case.
    return (s.title(), district, "")


# Candidate row: "<name> <votes>" — name may contain spaces and digits.
# The trailing integer is the vote count.
CANDIDATE_RE = re.compile(r"^(.+?)\s+(\d[\d,]*)\s*$")

SKIP_LINES = {
    "TOTAL", "Statistics",
}

# Lines that should be skipped even when they have a trailing vote count
# (the "TOTAL" header above is exact-match; these need prefix matches).
SKIP_PREFIXES = (
    "Ballots Cast ", "Voter Registration ", "Voter Turnout ",
    "Total Votes Cast ", "Write-In", "Last Updated", "2026 Primary",
)


def parse_lancaster_pdf(pdf_path: Path) -> list[dict]:
    pdf = npdf.PDF(str(pdf_path))
    rows: list[dict] = []
    current_precinct: str | None = None
    current_office = ""
    current_district = ""
    current_party = ""
    current_vote_for = 0
    seen_paired: dict[tuple[str, str], int] = {}
    skip_contest = False

    for page in pdf.pages:
        text = page.extract_text() or ""
        for raw_line in text.split("\n"):
            line = raw_line.strip()
            if not line:
                continue
            # Skip footer noise.
            if "Last Updated" in line or line.startswith("2026 Primary"):
                continue
            if line == "Lancaster County Detailed Results Report":
                continue
            if line.startswith("May 19, 2026"):
                continue
            # Precinct header: "0100 City 1" or "0708-2 CV". The precinct
            # line repeats on every page of a precinct — only reset state
            # when the precinct actually changes.
            if PRECINCT_RE.match(line):
                if line != current_precinct:
                    current_precinct = line
                    current_office = ""
                    seen_paired = {}
                continue
            if current_precinct is None:
                continue
            # "Vote For N" line: confirm the previous line was an office
            # header and finalize the contest.
            vfm = VOTE_FOR_RE.match(line)
            if vfm:
                current_vote_for = int(vfm.group(1))
                continue
            # Skip noise rows (exact-match and prefix-match).
            if line in SKIP_LINES:
                continue
            if any(line.startswith(p) for p in SKIP_PREFIXES):
                continue
            # Office header: all-caps line that's a known office name or
            # contains "District" / "COMMITTEE" etc.
            if _looks_like_office_header(line):
                if any(line.upper().startswith(p) for p in SKIP_OFFICE_PREFIXES):
                    current_office = ""
                    skip_contest = True
                    continue
                skip_contest = False
                office, district, party = _normalize_office(line)
                if not party and office.upper() in {o.upper() for o in PAIRED_OFFICES}:
                    # Use occurrence counter for this (precinct, office) pair.
                    key = (current_precinct, office)
                    count = seen_paired.get(key, 0)
                    party = "DEM" if count == 0 else "REP"
                    seen_paired[key] = count + 1
                current_office = office
                current_district = district
                current_party = party
                continue
            if skip_contest or not current_office:
                continue
            # Candidate row: "<name> <votes>"
            cm = CANDIDATE_RE.match(line)
            if not cm:
                continue
            name = cm.group(1).strip()
            try:
                votes = int(cm.group(2).replace(",", ""))
            except ValueError:
                continue
            if name.upper() == "SCATTERED":
                candidate = "Scattered"
            elif name.upper().startswith("WRITE-IN"):
                candidate = "Write-In Totals"
            else:
                candidate = _finalize_candidate(name)
            rows.append({
                "county": "Lancaster",
                "precinct": current_precinct,
                "office": current_office,
                "district": current_district,
                "party": current_party,
                "candidate": candidate,
                "votes": votes,
                "election_day": "",
                "provisional": "",
                "absentee": "",
            })
    return rows


def _looks_like_office_header(line: str) -> bool:
    s = line.strip()
    if not s:
        return False
    upper = s.upper()
    if PRECINCT_RE.match(s):
        return False
    # Known per-precinct committee races take priority even when the line
    # ends in a precinct-split digit (e.g. "DEMOCRATIC COMMITTEEPERSON
    # COLUMBIA BOROUGH 1") — that "1" isn't a vote count.
    if any(upper.startswith(p) for p in SKIP_OFFICE_PREFIXES):
        return True
    # Lines ending in " <integer>" are candidate rows (e.g. "SCATTERED 0",
    # "JOSH SHAPIRO 110"), not office headers.
    if CANDIDATE_RE.match(s):
        return False
    if any(c.isdigit() for c in s.split()[0]):
        return False
    # "District" alone is a continuation of a wrapped contest header
    # (e.g. "... East Petersburg Borough - North\nDistrict"), not a new
    # office. Require " District" to appear mid-line (with leading content).
    if s == "District" or upper == "DISTRICT":
        return False
    if "COMMITTEE" in upper or " District" in s or "DISTRICT" in upper:
        return True
    if s.isupper() and any(c.isalpha() for c in s):
        return True
    return False


def write_csv(rows: list[dict], out_path: Path) -> None:
    with out_path.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=PRIMARY_FIELDNAMES)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in PRIMARY_FIELDNAMES})


def main(argv: list[str]) -> None:
    if len(argv) != 3:
        sys.exit(f"Usage: {Path(argv[0]).name} <input.pdf> <output.csv>")
    pdf_path = Path(argv[1])
    out_path = Path(argv[2])
    if not pdf_path.exists():
        sys.exit(f"Missing PDF: {pdf_path}")
    rows = parse_lancaster_pdf(pdf_path)
    write_csv(rows, out_path)
    precincts = len({r["precinct"] for r in rows})
    offices = len({(r["office"], r["district"]) for r in rows})
    print(
        f"Wrote {len(rows)} rows across {offices} contests / "
        f"{precincts} precincts to {out_path}"
    )


if __name__ == "__main__":
    main(sys.argv)