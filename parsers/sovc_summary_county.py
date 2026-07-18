#!/usr/bin/env python3
"""Generic county-level parser for SOVC "Election Summary Report" PDFs
from PA 2026 primary elections (Bedford, Carbon, Clarion, Jefferson).

These reports have a uniform structure: a contest block starts with an
office header like ``Governor (DEM) (Vote for 1)``, followed by a party
line (``DEM``), a ``Precincts Reported:`` line, a column-header line, a
``Times Cast`` row, then candidate rows of the form::

    Name  PARTY  Election-Day  Mail-In  Provisional  Total [pct%]

Clarion is a degenerate variant where only the ``Total`` column appears::

    Name  PARTY  Total

The named-write-in breakdown sub-block that follows ``Total Votes`` is
skipped: only the main candidate rows and the aggregate ``Write-in`` row
are emitted (as ``Write-In Totals``).

Usage:
    python parsers/sovc_summary_county.py <County> <input.pdf> <output.csv>
"""

from __future__ import annotations

import csv
import re
import subprocess
import sys
from pathlib import Path

OFFICE_MAP = {
    "GOVERNOR": ("Governor", False),
    "LIEUTENANT GOVERNOR": ("Lieutenant Governor", False),
    "ATTORNEY GENERAL": ("Attorney General", False),
    "AUDITOR GENERAL": ("Auditor General", False),
    "STATE TREASURER": ("State Treasurer", False),
    "REPRESENTATIVE IN CONGRESS": ("U.S. House", True),
    "SENATOR IN THE GENERAL ASSEMBLY": ("State Senate", True),
    "REPRESENTATIVE IN THE GENERAL ASSEMBLY": ("State House", True),
    "MEMBER OF THE DEMOCRATIC STATE COMMITTEE": ("Member of Democratic State Committee", False),
    "MEMBER OF THE REPUBLICAN STATE COMMITTEE": ("Member of Republican State Committee", False),
    "MEMBER OF DEMOCRATIC STATE COMMITTEE": ("Member of Democratic State Committee", False),
    "MEMBER OF REPUBLICAN STATE COMMITTEE": ("Member of Republican State Committee", False),
    "DEMOCRATIC STATE COMMITTEE": ("Member of Democratic State Committee", False),
    "REPUBLICAN STATE COMMITTEE": ("Member of Republican State Committee", False),
}

# Per-precinct committee race substrings — these are not county-level
# contests and should be skipped.
PER_PRECINCT_COMMITTEE_TOKENS = (
    "COUNTY COMMITTEE", "COUNTY COMMITTEEMAN", "COUNTY COMMITTEEWOMAN",
    "COMMITTEE PERSON", "COMMITTEEPERSON",
)

# Contest header: "Governor (DEM) (Vote for 1)" — office may include a
# trailing district like "Representative in Congress 13th District".
CONTEST_RE = re.compile(
    r"^\s*(.+?)\s+\((DEM|REP|GP|LBR|IND|GRN|WEP|WFP|PGH|CON)\)\s*"
    r"\(Vote for\s+\d+\)\s*$",
    re.IGNORECASE,
)

# "Vote For 1" line (some reports have this as a separate banner line).
VOTE_FOR_RE = re.compile(r"^\s*Vote For\s+\d+\s*$", re.IGNORECASE)

DISTRICT_ORDINAL_RE = re.compile(
    r"\b(\d+)(?:ST|ND|RD|TH)\s+(?:LEGISLATIVE\s+|CONGRESSIONAL\s+|SENATORIAL\s+)?DISTRICT\b",
    re.IGNORECASE,
)
DISTRICT_PLAIN_RE = re.compile(
    r"\b(\d+)\w*\s+DISTRICT\b", re.IGNORECASE
)

# Candidate row: Name (letters/spaces/punct) + party code + 1-4 vote numbers
# + optional percentage. The numbers are Election Day, Mail-In, Provisional,
# Total (in that order, when present). For Clarion only "Total" appears.
# "Write-in" rows omit the party column.
CANDIDATE_RE = re.compile(
    r"^\s*([A-Z][A-Za-z\.\'\-,\s]+?)\s+"
    r"(?:(DEM|REP|GP|LBR|WRITE-IN)\s+)?"
    r"(\d{1,3}(?:,\d{3})*|\d+)\s*"
    r"(?:(\d{1,3}(?:,\d{3})*|\d+)\s*)?"
    r"(?:(\d{1,3}(?:,\d{3})*|\d+)\s*)?"
    r"(?:(\d{1,3}(?:,\d{3})*|\d+)\s*)?"
    r"(?:\d+(?:\.\d+)?%)?\s*$"
)

# Row labels that terminate a contest block or should be skipped.
SKIP_ROWS = {
    "TIMES CAST", "TOTAL VOTES", "UNRESOLVED WRITE-IN",
    "REGISTERED VOTERS", "VOTERS CAST", "BALLOTS CAST",
    "PRECINCTS REPORTED", "UNRESOLVED WRITE IN",
}

FIELDNAMES = [
    "county", "office", "district", "party", "candidate",
    "votes", "election_day", "mail", "provisional",
]

_ROMAN_RE = re.compile(r"^[IVX]+$")


def _finalize_candidate(raw: str) -> str:
    s = raw.replace(",", "").strip()
    up = s.upper()
    if up in ("WRITE-IN", "WRITE IN", "WRITE-IN TOTALS"):
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
    for key, (norm, extract) in OFFICE_MAP.items():
        if upper.startswith(key):
            return (norm, district if extract else "")
    return (raw.title(), district)


def _is_skip_line(line: str) -> bool:
    s = line.strip()
    if not s:
        return True
    up = s.upper()
    for token in SKIP_ROWS:
        if up.startswith(token):
            return True
    if re.match(r"^\s*Precincts Reported:", s, re.IGNORECASE):
        return True
    if re.match(r"^\s*Page:\s+\d+\s+of\s+\d+", s, re.IGNORECASE):
        return True
    if re.match(r"^\s*\d+/\d+/\d+\s+\d+", s):
        return True
    # Column header lines: only header words and whitespace
    if re.match(
        r"^\s*(Candidate|Party|Election\s+Day|Mail-In|Provisional|Total|"
        r"Voters\s+Cast|Registered\s+Voters|Turnout|"
        r"Elector\s+Group|Counting\s+Group)\b[\s\w/%\-]*$",
        s, re.IGNORECASE,
    ):
        return True
    return False


def _numbers_from_match(m: re.Match) -> tuple[int, int, int, int]:
    """Return (total, election_day, mail, provisional) from a candidate
    regex match. The last numeric group is always the total. Earlier
    groups (if present) are ed, mail, prov in order."""
    nums = [int(g.replace(",", "")) for g in m.groups()[2:] if g]
    if not nums:
        return (0, 0, 0, 0)
    total = nums[-1]
    ed = nums[0] if len(nums) >= 4 else 0
    mail = nums[1] if len(nums) >= 4 else 0
    prov = nums[2] if len(nums) >= 4 else 0
    # If we have 2 numbers: (something, total) — treat first as ed only.
    if len(nums) == 2:
        ed = nums[0]
        mail = 0
        prov = 0
    elif len(nums) == 3:
        ed = nums[0]
        mail = nums[1]
        prov = 0
    elif len(nums) == 1:
        ed = 0
        mail = 0
        prov = 0
    return (total, ed, mail, prov)


def parse_summary(county: str, pdf_path: Path) -> list[dict]:
    proc = subprocess.run(
        ["pdftotext", "-layout", str(pdf_path), "-"],
        capture_output=True, text=True, check=True,
    )
    lines = proc.stdout.split("\n")

    rows: list[dict] = []
    i = 0
    n = len(lines)
    while i < n:
        line = lines[i]
        m = CONTEST_RE.match(line)
        if not m:
            i += 1
            continue
        office_raw = m.group(1).strip()
        # Join wrapped header: if the previous line ends with " -" or
        # contains "Committee" and doesn't itself end with the contest
        # pattern, prepend it. Carbon has headers like "Democratic County
        # Committeeman Mahoning Township -\nPackerton/Jamestown (DEM) ...".
        j = i - 1
        while j > max(i - 3, -1):
            prev = lines[j].rstrip()
            if not prev:
                break
            if CONTEST_RE.search(prev):
                break
            if prev.endswith("-") or "Committee" in prev:
                office_raw = f"{prev} {office_raw}".strip(" -")
                j -= 1
            else:
                break
        party = m.group(2).upper()
        # Skip per-precinct committee races (not county-level).
        office_up = re.sub(r"\s+", " ", office_raw.upper())
        if any(tok in office_up for tok in PER_PRECINCT_COMMITTEE_TOKENS):
            i += 1
            continue
        office, district = normalize_office(office_raw)

        # Walk forward looking for the candidate row block. Skip precincts
        # reported, column headers, Times Cast, etc.
        k = i + 1
        # Limit scan to ~30 lines before giving up.
        end = min(n, i + 40)
        while k < end:
            s = lines[k]
            up = s.strip().upper()
            # Stop at next contest header
            if CONTEST_RE.match(s):
                break
            if up == "TOTAL VOTES":
                # End of main candidate block
                k += 1
                break
            cm = CANDIDATE_RE.match(s)
            if cm:
                name_raw = cm.group(1).strip()
                name_up = name_raw.upper()
                if name_up in ("CANDIDATE", "TIMES CAST", "TOTAL VOTES"):
                    k += 1
                    continue
                # Skip "Candidate  Party  Election Day..." header lines
                if name_up == "CANDIDATE":
                    k += 1
                    continue
                party_cell = cm.group(2)
                # Skip named-write-in detail rows (party cell == WRITE-IN and
                # name != "Write-in"). These follow "Total Votes".
                if party_cell and party_cell.upper() == "WRITE-IN":
                    k += 1
                    continue
                # Skip "Unresolved Write-In" placeholder rows.
                if name_up.startswith("UNRESOLVED WRITE"):
                    k += 1
                    continue
                total, ed, mail, prov = _numbers_from_match(cm)
                rows.append({
                    "county": county,
                    "office": office,
                    "district": district,
                    "party": party,
                    "candidate": _finalize_candidate(name_raw),
                    "votes": total,
                    "election_day": ed,
                    "mail": mail,
                    "provisional": prov,
                })
            elif _is_skip_line(s):
                pass
            k += 1
        i = k

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
    if len(argv) < 4:
        sys.exit(f"Usage: {Path(argv[0]).name} <County> <input.pdf...> <output.csv>")
    county = argv[1]
    out_path = Path(argv[-1])
    pdf_paths = [Path(p) for p in argv[2:-1]]
    for p in pdf_paths:
        if not p.exists():
            sys.exit(f"Missing PDF: {p}")
    rows: list[dict] = []
    for pdf_path in pdf_paths:
        rows.extend(parse_summary(county, pdf_path))
    seen = set()
    deduped: list[dict] = []
    for r in rows:
        key = (r["office"], r["district"], r["party"], r["candidate"])
        if key in seen:
            continue
        seen.add(key)
        deduped.append(r)
    with out_path.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDNAMES)
        w.writeheader()
        for r in deduped:
            w.writerow(r)
    offices = len({(r["office"], r["district"]) for r in deduped})
    print(f"Wrote {len(deduped)} rows across {offices} contests to {out_path}")


if __name__ == "__main__":
    main(sys.argv)