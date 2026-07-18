#!/usr/bin/env python3
"""County-level parser for Bradford and Columbia Counties PA 2026 primary
"Election Summary Report" PDFs.

These reports use a two-column layout where each column contains an
independent contest block. Contest headers span two lines::

    GOVERNOR
    (DEMOCRATIC)

followed by ``Number of Precincts`` / ``Precincts Reporting`` / ``Vote For
1`` / ``Total Votes`` metadata lines, then candidate rows of the form::

    NAME  VOTES  PCT%

This parser reads the PDF via ``pdftotext -layout``, splits each line at
the column boundary (~col 55), and runs a state machine on each column
stream to extract contests and candidates.

Usage:
    python parsers/pa_bradford_columbia_primary_2026_county_parser.py <County> <input.pdf> <output.csv>
"""

from __future__ import annotations

import csv
import re
import subprocess
import sys
from pathlib import Path

OFFICE_MAP = {
    "GOVERNOR": ("Governor", False),
    "LT GOVERNOR": ("Lieutenant Governor", False),
    "LIEUTENANT GOVERNOR": ("Lieutenant Governor", False),
    "ATTORNEY GENERAL": ("Attorney General", False),
    "AUDITOR GENERAL": ("Auditor General", False),
    "STATE TREASURER": ("State Treasurer", False),
    "REP IN CONGRESS": ("U.S. House", True),
    "REPRESENTATIVE IN CONGRESS": ("U.S. House", True),
    "SENATOR IN THE GENERAL ASSEMBLY": ("State Senate", True),
    "SENATOR IN GEN ASSEMBLY": ("State Senate", True),
    "REP IN THE GENERAL ASSEMBLY": ("State House", True),
    "REP IN GEN": ("State House", True),
    "REPRESENTATIVE IN THE GENERAL ASSEMBLY": ("State House", True),
    "MEMBER OF THE DEMOCRATIC STATE COMMITTEE": ("Member of Democratic State Committee", False),
    "MEMBER OF THE REPUBLICAN STATE COMMITTEE": ("Member of Republican State Committee", False),
    "MEMBER OF DEMOCRATIC STATE COMMITTEE": ("Member of Democratic State Committee", False),
    "MEMBER OF REPUBLICAN STATE COMMITTEE": ("Member of Republican State Committee", False),
    "MEMBER OF DEM STATE COMMITTEE": ("Member of Democratic State Committee", False),
    "MEMBER OF REP STATE COMMITTEE": ("Member of Republican State Committee", False),
    "DEMOCRATIC STATE COMMITTEE": ("Member of Democratic State Committee", False),
    "REPUBLICAN STATE COMMITTEE": ("Member of Republican State Committee", False),
}

PARTY_MAP = {
    "DEMOCRATIC": "DEM", "DEM": "DEM",
    "REPUBLICAN": "REP", "REP": "REP",
    "GP": "GP", "LBR": "LBR", "IND": "IND", "GRN": "GRN",
    "WEP": "WEP", "WFP": "WFP", "CON": "CON",
}

DISTRICT_ORDINAL_RE = re.compile(
    r"\b(\d+)(?:ST|ND|RD|TH)\s+(?:LEGISLATIVE\s+|CONGRESSIONAL\s+|SENATORIAL\s+)?DISTRICT\b",
    re.IGNORECASE,
)
DISTRICT_PLAIN_RE = re.compile(
    r"\b(\d+)\w*\s+DISTRICT\b", re.IGNORECASE
)
# Bare ordinal: "9TH", "68TH", "109TH" — used in "REP IN CONGRESS 9TH DISTRICT"
# but also as standalone suffix on "REP IN GEN 68TH" style headers.
BARE_ORDINAL_RE = re.compile(r"\b(\d+)(?:ST|ND|RD|TH)\b", re.IGNORECASE)

# Party line under office: "(DEMOCRATIC)" or "(REPUBLICAN)".
PARTY_LINE_RE = re.compile(r"^\s*\((DEMOCRATIC|REPUBLICAN|DEM|REP|GP|LBR|IND|GRN|WEP|WFP|CON)\)\s*$", re.IGNORECASE)

# Candidate row: "NAME  VOTES  PCT%" (percentage optional, and the % sign
# may be missing if the column split truncated it). Name may include "(WI)"
# suffix for write-in candidates. Name starts with a letter.
CANDIDATE_RE = re.compile(
    r"^\s*([A-Z][A-Za-z\.\'\-,\s]*?(?:\s*\(WI\))?)\s+"
    r"(\d{1,3}(?:,\d{3})*|\d+)\s+"
    r"(\d+(?:\.\d+)?%?)?\s*$"
)

# Looser candidate-row detector for the backward search: matches any line
# that looks like "NAME  NUMBER" (a candidate row with votes), used to
# stop the backward office-name search.
CANDIDATE_LIKE_RE = re.compile(
    r"^\s*[A-Z][A-Za-z\.\'\-,\s]+?\s+\d[\d,]*\s*$"
)

SKIP_NAMES = {"WRITE-IN", "WRITE IN", "TOTAL", "TIMES CAST", "OVERVOTES",
              "UNDERVOTES", "NOT ASSIGNED", "UNRESOLVED WRITE-IN"}

PER_PRECINCT_COMMITTEE_TOKENS = (
    "COUNTY COMMITTEE", "COUNTY COMMITTEEMAN", "COUNTY COMMITTEEWOMAN",
    "COMMITTEE PERSON", "COMMITTEEPERSON", "COMMITTEEMAN", "COMMITTEEWOMAN",
)

FIELDNAMES = [
    "county", "office", "district", "party", "candidate",
    "votes", "election_day", "mail", "provisional",
]

_ROMAN_RE = re.compile(r"^[IVX]+$")


def _finalize_candidate(raw: str) -> str:
    s = raw.replace(",", "").strip()
    # Strip "(WI)" suffix — it marks write-in candidates, but we treat them
    # as regular candidates by name (matching how the source reports them).
    s = re.sub(r"\s*\(WI\)\s*$", "", s).strip()
    up = s.upper()
    if up in ("WRITE-IN", "WRITE IN"):
        return "Write-In Totals"
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


def normalize_office(raw: str) -> tuple[str, str]:
    upper = re.sub(r"\s+", " ", raw.upper()).strip()
    district = ""
    dm = DISTRICT_ORDINAL_RE.search(upper)
    if dm:
        district = str(int(dm.group(1)))
        upper = DISTRICT_ORDINAL_RE.sub("", upper).strip()
    else:
        dm = DISTRICT_PLAIN_RE.search(upper)
        if dm:
            district = str(int(dm.group(1)))
            upper = DISTRICT_PLAIN_RE.sub("", upper).strip()
    # Try each OFFICE_MAP key. For district-extracting offices, also try
    # bare ordinal suffix if no explicit "DISTRICT" word was found.
    for key, (norm, extract) in OFFICE_MAP.items():
        if upper.startswith(key):
            if extract and not district:
                bm = BARE_ORDINAL_RE.search(upper)
                if bm:
                    district = str(int(bm.group(1)))
            return (norm, district if extract else "")
    return (raw.title(), district)


def _is_skip_line(s: str) -> bool:
    s = s.strip()
    if not s:
        return True
    up = s.upper()
    if up in SKIP_NAMES:
        return True
    if up.startswith(("NUMBER OF PRECINCTS", "PRECINCTS REPORTING",
                       "VOTE FOR", "TOTAL VOTES", "REGISTERED VOTERS",
                       "BALLOTS CAST", "VOTER TURNOUT", "PAGE",
                       "ELECTION SUMMARY", "PARTY DISTRIBUTION",
                       "GENERAL PRIMARY", "MAY 19, 2026",
                       "BRADFORD COUNTY", "COLUMBIA COUNTY",
                       "FINAL RESULTS", "OFFICIAL RESULTS",
                       "PRECINCTS REPORTING", "TOTAL BALLOTS",
                       "TWO YEAR TERM", "FOUR YEAR TERM",
                       "DATE:", "TIME:")):
        return True
    # Numbers-only line (metadata values like "61", "100.00%", "2,181")
    if re.match(r"^\s*\d[\d,\.]*\s*%?\s*$", s):
        return True
    # "61 of 61 Precincts Reporting"
    if re.match(r"^\s*\d+\s+of\s+\d+\s+", s, re.IGNORECASE):
        return True
    return False


def _looks_like_office_header(s: str) -> bool:
    """Heuristic: line looks like an office name (all caps, may include
    digits/ordinals/words like DISTRICT)."""
    s = s.strip()
    if not s:
        return False
    up = s.upper()
    for key in OFFICE_MAP:
        if up.startswith(key):
            return True
    return False


def parse_column_stream(county: str, lines: list[str]) -> list[dict]:
    """Run a state machine on a single column's lines, returning rows.

    Strategy: walk lines looking for a party line ``(DEMOCRATIC)`` or
    ``(REPUBLICAN)``. When found, look backwards for the office name
    (one or two preceding non-blank, non-metadata lines) and forwards
    for candidate rows. Stop the candidate block at the next party line.
    """
    rows: list[dict] = []
    n = len(lines)
    i = 0
    while i < n:
        line = lines[i]
        pm = PARTY_LINE_RE.match(line)
        if not pm:
            i += 1
            continue
        party = PARTY_MAP.get(pm.group(1).upper(), pm.group(1).upper())
        # Look backwards for office name. Skip blank lines and metadata.
        office_parts: list[str] = []
        j = i - 1
        while j >= 0 and len(office_parts) < 2:
            prev = lines[j].strip()
            if not prev:
                j -= 1
                continue
            if _is_skip_line(prev):
                break
            if PARTY_LINE_RE.match(prev):
                break
            # Stop if this looks like a candidate row (NAME VOTES PCT%).
            if CANDIDATE_RE.match(prev) or CANDIDATE_LIKE_RE.match(prev):
                break
            office_parts.insert(0, prev)
            j -= 1
        if not office_parts:
            i += 1
            continue
        office_raw = " ".join(office_parts)
        # Strip trailing "Two Year Term" / "Four Year Term" suffixes that
        # appear on Bradford's multi-line headers.
        office_raw = re.sub(r"\s+(Two|Four|Six)\s+Year\s+Term\s*$", "", office_raw, flags=re.IGNORECASE)
        office_up = re.sub(r"\s+", " ", office_raw.upper())
        if any(tok in office_up for tok in PER_PRECINCT_COMMITTEE_TOKENS):
            i += 1
            continue
        if not _looks_like_office_header(office_raw):
            i += 1
            continue
        office, district = normalize_office(office_raw)
        # Walk forward looking for candidate rows. Stop at next party line.
        k = i + 1
        while k < n:
            s = lines[k]
            if PARTY_LINE_RE.match(s):
                break
            cm = CANDIDATE_RE.match(s)
            if cm:
                name_raw = cm.group(1).strip()
                name_up = name_raw.upper()
                if name_up in SKIP_NAMES:
                    k += 1
                    continue
                if name_up.startswith(("NUMBER OF PRECINCTS", "PRECINCTS REPORTING",
                                        "VOTE FOR", "TOTAL VOTES")):
                    k += 1
                    continue
                votes = int(cm.group(2).replace(",", ""))
                rows.append({
                    "county": county,
                    "office": office,
                    "district": district,
                    "party": party,
                    "candidate": _finalize_candidate(name_raw),
                    "votes": votes,
                    "election_day": "",
                    "mail": "",
                    "provisional": "",
                })
            k += 1
        i = k
    return rows


def _split_line(line: str) -> tuple[str, str]:
    """Split a layout-preserved line into left and right column halves.

    The right column starts with alphabetic content (office names like
    "GOVERNOR", metadata like "Number of Precincts", or candidate names).
    The left column's percentage (e.g. "97.98%") sits in the gap between
    columns — we must not mistake it for right-column content.

    Strategy: scan columns 40-80 for a gap of 3+ spaces where the next
    non-space character is a letter. Split at the first such gap.
    """
    if len(line) < 55:
        return (line, "")
    # Find first gap of 3+ spaces between cols 40-80 followed by a letter.
    i = 40
    end = min(80, len(line))
    while i < end:
        if line[i] == " ":
            j = i
            while j < len(line) and line[j] == " ":
                j += 1
            if j - i >= 3 and j < len(line) and (line[j].isalpha() or line[j] == "("):
                return (line[:i].rstrip(), line[j:].rstrip())
            i = j if j > i else i + 1
        else:
            i += 1
    # No gap found — entire line is left column (or right column only).
    if line[:40].strip() == "":
        return ("", line.rstrip())
    return (line.rstrip(), "")


def parse_summary(county: str, pdf_path: Path, split_col: int = 65) -> list[dict]:
    proc = subprocess.run(
        ["pdftotext", "-layout", str(pdf_path), "-"],
        capture_output=True, text=True, check=True,
    )
    raw_lines = proc.stdout.split("\n")

    # Split each line into left and right halves at the first wide gap.
    left_lines: list[str] = []
    right_lines: list[str] = []
    for line in raw_lines:
        left, right = _split_line(line)
        left_lines.append(left)
        right_lines.append(right)

    rows: list[dict] = []
    rows.extend(parse_column_stream(county, left_lines))
    rows.extend(parse_column_stream(county, right_lines))

    # Deduplicate: keep first occurrence of (office, district, party, candidate)
    seen = set()
    deduped: list[dict] = []
    for r in rows:
        key = (r["office"], r["district"], r["party"], r["candidate"])
        if key in seen:
            continue
        seen.add(key)
        deduped.append(r)
    return deduped


def main(argv: list[str]) -> None:
    if len(argv) != 4:
        sys.exit(f"Usage: {Path(argv[0]).name} <County> <input.pdf> <output.csv>")
    county = argv[1]
    pdf_path = Path(argv[2])
    out_path = Path(argv[3])
    if not pdf_path.exists():
        sys.exit(f"Missing PDF: {pdf_path}")
    rows = parse_summary(county, pdf_path)
    with out_path.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDNAMES)
        w.writeheader()
        for r in rows:
            w.writerow(r)
    offices = len({(r["office"], r["district"]) for r in rows})
    print(f"Wrote {len(rows)} rows across {offices} contests to {out_path}")


if __name__ == "__main__":
    main(sys.argv)