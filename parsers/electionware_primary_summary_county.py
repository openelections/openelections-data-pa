#!/usr/bin/env python3
"""Generic county-level parser for Electionware "Summary Results Report"
PDFs from PA 2026 primary elections.

These reports have a uniform structure: a contest block starts with a
party-prefixed office header (``DEM GOVERNOR``, ``REP REPRESENTATIVE IN
CONGRESS 8TH DISTRICT``), then a ``Vote For N`` line, then candidate rows
with four vote columns (TOTAL, Election Day, Mail, Provisional).

This parser is the county-level analogue of ``electionware_primary_np``:
same office/party/district normalization, but it emits one row per
candidate per county (no precinct column) and reads the PDF via
``pdftotext -layout``.

Usage:
    python parsers/electionware_primary_summary_county.py <County> <input.pdf> <output.csv>
"""

from __future__ import annotations

import csv
import re
import subprocess
import sys
from pathlib import Path

# Standard 2026 PA primary office names -> (normalized_office, extract_district)
STATEWIDE_OFFICES: dict[str, tuple[str, bool]] = {
    "PRESIDENT OF THE UNITED STATES": ("President", False),
    "UNITED STATES SENATOR": ("U.S. Senate", False),
    "GOVERNOR": ("Governor", False),
    "LIEUTENANT GOVERNOR": ("Lieutenant Governor", False),
    "LT. GOVERNOR": ("Lieutenant Governor", False),
    "LT GOVERNOR": ("Lieutenant Governor", False),
    "ATTORNEY GENERAL": ("Attorney General", False),
    "AUDITOR GENERAL": ("Auditor General", False),
    "STATE TREASUR": ("State Treasurer", False),  # matches "STATE TREASURER"
    "REPRESENTATIVE IN CONGRESS": ("U.S. House", True),
    "REP. IN CONGRESS": ("U.S. House", True),
    "REP IN CONGRESS": ("U.S. House", True),
    "SENATOR IN THE GENERAL ASSEMBLY": ("State Senate", True),
    "SEN. IN THE GEN. ASSEMBLY": ("State Senate", True),
    "SEN IN THE GENERAL ASSEMBLY": ("State Senate", True),
    "REPRESENTATIVE IN THE GENERAL ASSEMBLY": ("State House", True),
    "REP. IN GEN. ASSEMBLY": ("State House", True),
    "REP IN GEN ASSEMBLY": ("State House", True),
    "REP. IN THE GENERAL ASSEMBLY": ("State House", True),
    "MEMBER OF THE DEMOCRATIC STATE COMMITTEE": ("Member of Democratic State Committee", False),
    "MEMBER OF THE REPUBLICAN STATE COMMITTEE": ("Member of Republican State Committee", False),
    "MEMBER OF DEMOCRATIC STATE COMMITTEE": ("Member of Democratic State Committee", False),
    "MEMBER OF REPUBLICAN STATE COMMITTEE": ("Member of Republican State Committee", False),
    "DEMOCRATIC STATE COMMITTEE": ("Member of Democratic State Committee", False),
    "REPUBLICAN STATE COMMITTEE": ("Member of Republican State Committee", False),
    "STATE COMMITTEE": ("__STATE_COMMITTEE__", False),  # resolved by party tracker
}

# Party code prefix on office header. PA 2026 primary reports use DEM/REP.
PARTY_CODES = ("DEM", "REP", "GP", "LBR", "IND", "GRN", "WEP", "WFP", "PGH", "CON")

PRIMARY_OFFICE_PARTY_RE = re.compile(
    r"^\s*(" + "|".join(re.escape(p) for p in PARTY_CODES) + r")\s+(.+?)\s*$",
    re.IGNORECASE,
)

DISTRICT_ORDINAL_RE = re.compile(
    r"\b(\d+)(?:ST|ND|RD|TH)\s+(?:LEGISLATIVE\s+|CONGRESSIONAL\s+|SENATORIAL\s+)?DISTRICT\b",
    re.IGNORECASE,
)
# Mercer writes districts as "DISTRICT 16" (no ordinal suffix). Accept
# that form too so the district is extracted and stripped from the key.
DISTRICT_N_RE = re.compile(
    r"\bDISTRICT\s+(\d+)\b",
    re.IGNORECASE,
)

VOTE_FOR_RE = re.compile(r"^\s*Vote For\s+\d+\s*$", re.IGNORECASE)

# Per-precinct committee race header (e.g. "DEMOCRATIC COMMITTEEMAN ALIQUIPPA 1",
# "REPUBLICAN COMMITTEEWOMAN BENTON TWP",
# "DEM Democratic County Committee Member 005 Atglen" (Chester),
# "DEM Democratic Precinct Committeeman Adams Twp" (Butler)).
# These appear after county-level Member of State Committee contests and must
# stop the candidate walk, otherwise the per-precinct race name leaks in as a
# candidate. State committee headers ("Member of ... State Committee") do NOT
# match — "Committee" comes last there, not followed by Man/Woman/Person/Member.
PER_PRECINCT_COMMITTEE_RE = re.compile(
    r"^\s*(?:DEMOCRATIC|REPUBLICAN|DEM|REP)\s+"
    r"(?:"
    # Format 1: party + (Dem/Rep) + (location) + Committee[Man/Woman/Person/Member]S?
    # e.g. "DEM DEMOCRATIC PRECINCT COMMITTEEMAN ADAMS TWP",
    #      "DEM DEMOCRATIC COUNTY COMMITTEE MEMBER 005 ATGLEN" (Chester),
    #      "REP REPUBLICAN COMMITTEE PERSONS 010001-1 ALLEN TWSP" (Northampton),
    #      "DEMOCRATIC COMMITTEEMAN ALIQUIPPA 1" (Beaver),
    #      "DEM COMMITTEEPERSON - 4 YEAR TERM CAMP HILL 1" (Cumberland).
    r"(?:DEMOCRATIC\s+|REPUBLICAN\s+)?"
    r"(?:COUNTY\s+|PRECINCT\s+|BOROUGH\s+|TOWNSHIP\s+|WARD\s+|DISTRICT\s+)*"
    r"COMMITTEE\s*(?:MAN|WOMAN|PERSON|MEMBER)S?\b"
    # Format 2: Lawrence's "DEM MEMBER OF DEMOCRATIC COUNTY COMMITTEE - MALE ...".
    # "COUNTY COMMITTEE" (per-precinct) never appears in State Committee headers.
    r"|.*COUNTY\s+COMMITTEE\b"
    r")",
    re.IGNORECASE,
)

# Office header without party prefix (e.g. Juniata: "GOVERNOR", "LIEUTENANT
# GOVERNOR", "REPRESENTATIVE IN CONGRESS 13TH DISTRICT"). Party will be left
# blank and inferred in post-processing. Snyder uses abbreviated forms
# ("LT. GOVERNOR", "REP. IN CONGRESS", "REP. IN GEN. ASSEMBLY").
NO_PARTY_OFFICE_RE = re.compile(
    r"^\s*("
    r"PRESIDENT OF THE UNITED STATES|UNITED STATES SENATOR|"
    r"GOVERNOR|LIEUTENANT GOVERNOR|LT\.?\s+GOVERNOR|"
    r"ATTORNEY GENERAL|AUDITOR GENERAL|"
    r"STATE TREASURER|STATE TREASUR|"
    r"REPRESENTATIVE IN CONGRESS|REP\.?\s+IN\s+CONGRESS|"
    r"SENATOR IN THE GENERAL ASSEMBLY|SEN\.?\s+IN\s+THE\s+GEN\.?\s+ASSEMBLY|"
    r"REPRESENTATIVE IN THE GENERAL ASSEMBLY|REP\.?\s+IN\s+(?:THE\s+)?GEN\.?\s+ASSEMBLY|"
    r"MEMBER OF (?:THE\s+)?(?:DEMOCRATIC|REPUBLICAN)\s+STATE\s+COMMITTEE|"
    r"(?:DEMOCRATIC|REPUBLICAN)\s+STATE\s+COMMITTEE|"
    r"STATE\s+COMMITTEE"
    r")\s*(?:.*)$",
    re.IGNORECASE,
)

# Candidate row: name + total + (optional percentage) + optional ED/mail/prov
# + optional extra "Spare" columns. Handles:
#   - 1 number (Schuylkill: TOTAL only)
#   - 4 numbers (most counties: TOTAL/ED/Mail/Prov)
#   - 7 numbers (McKean: TOTAL/ED/Mail/Prov/Spare1/Spare2/Spare3)
# Franklin inserts a "VOTE %" column between TOTAL and Election Day.
# Name may contain letters, spaces, periods, apostrophes, hyphens, commas.
CANDIDATE_ROW_RE = re.compile(
    r"^\s*([A-Z][A-Za-z\.\'\-,\s]+?)\s+"
    r"(\d{1,3}(?:,\d{3})*|\d+)"  # TOTAL
    r"(?:\s+\d+(?:\.\d+)?%)?"     # optional percentage (Franklin)
    r"(?:\s+(\d{1,3}(?:,\d{3})*|\d+))?"  # optional ED
    r"(?:\s+(\d{1,3}(?:,\d{3})*|\d+))?"  # optional Mail
    r"(?:\s+(\d{1,3}(?:,\d{3})*|\d+))?"  # optional Prov
    r"(?:\s+\d{1,3}(?:,\d{3})*)*"  # extra Spare columns (McKean)
    r"\s*$"
)

# Rows to skip within a contest block (not candidates).
SKIP_NAMES = {
    "TOTAL VOTES CAST", "CONTEST TOTALS", "OVERVOTES", "UNDERVOTES",
    "TIMES CAST", "NOT ASSIGNED", "PRECINCTS REPORTING", "PRECINCTS COMPLETE",
    "PRECINCTS PARTIALLY REPORTED", "REGISTERED VOTERS",
    "VOTER TURNOUT - TOTAL", "VOTER TURNOUT - DEMOCRATIC",
    "VOTER TURNOUT - REPUBLICAN", "VOTE FOR",
}

# Headers/banner lines to ignore between rows.
SKIP_LINE_PATTERNS = (
    re.compile(r"^\s*Election Summary\s*-", re.IGNORECASE),
    re.compile(r"^\s*Summary Results Report", re.IGNORECASE),
    re.compile(r"^\s*Report generated with Electionware", re.IGNORECASE),
    re.compile(r"^\s*Page\s+\d+\s+of\s+\d+", re.IGNORECASE),
    re.compile(r"^\s*OFFICIAL RESULTS", re.IGNORECASE),
    re.compile(r"^\s*UNOFFICIAL RESULTS", re.IGNORECASE),
    re.compile(r"^\s*2026\s+GUBERNATORIAL\s+PRIMARY", re.IGNORECASE),
    re.compile(r"^\s*2026\s+General\s+Primary", re.IGNORECASE),
    re.compile(r"^\s*May\s+19,\s+2026", re.IGNORECASE),
    re.compile(r"^\s*Tuesday\s+May\s+19,\s+2026", re.IGNORECASE),
    re.compile(r"^\s*County\s*$", re.IGNORECASE),
)

FIELDNAMES = [
    "county", "office", "district", "party", "candidate",
    "votes", "election_day", "mail", "provisional",
]

_ROMAN_RE = re.compile(r"^[IVX]+$")


def _finalize_candidate(raw: str) -> str:
    s = raw.replace(",", "").strip()
    if s.upper() == "WRITE-IN TOTALS" or s.upper() == "WRITE-IN":
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
        dm2 = DISTRICT_N_RE.search(upper)
        if dm2:
            district = str(int(dm2.group(1)))
            upper = DISTRICT_N_RE.sub("", upper).strip()
    for key, (norm, extract) in STATEWIDE_OFFICES.items():
        if upper.startswith(key):
            return (norm, district if extract else "")
    # Unknown office — keep title-cased raw, with district if present.
    return (raw.title(), district)


def _is_skip_line(line: str) -> bool:
    s = line.strip()
    if not s:
        return True
    if s.upper() in SKIP_NAMES:
        return True
    # "Number of Precincts" / "Precincts Reporting" sub-rows
    if re.match(r"^\s*(Number of Precincts|Precincts Reporting)\b", s, re.IGNORECASE):
        return True
    # Vote breakdown column headers (e.g. "TOTAL  ELECTION DAY  MAIL  PROVISIONAL")
    # often wrap across lines; treat any line that's only header words as a skip.
    for p in SKIP_LINE_PATTERNS:
        if p.match(s):
            return True
    # Lines that are only header words like "Election Day" / "Mail Votes" /
    # "Provisional Votes" / "TOTAL" / "ABSENTEE/MAIL-IN" — column header wraps.
    if re.match(r"^\s*(TOTAL|ELECTION\s+DAY|MAIL(\s+VOTES)?|PROVISIONAL(\s+VOTES)?|ABSENTEE(/MAIL-IN|/EARLY)?|VOTES|N DAY|NAL|E/MAIL-IN)\s*$", s, re.IGNORECASE):
        return True
    return False


def parse_summary(county: str, pdf_path: Path) -> list[dict]:
    proc = subprocess.run(
        ["pdftotext", "-layout", str(pdf_path), "-"],
        capture_output=True, text=True, check=True,
    )
    lines = proc.stdout.split("\n")

    rows: list[dict] = []
    i = 0
    n = len(lines)
    # Snyder groups contests under a "DEM GOVERNOR" / "REP GOVERNOR" banner;
    # subsequent contests without a party prefix inherit the most recent party.
    last_party = ""
    while i < n:
        line = lines[i]
        m = PRIMARY_OFFICE_PARTY_RE.match(line)
        if m:
            party_raw = m.group(1).upper()
            office_raw = m.group(2).strip()
            if not _looks_like_office_header(office_raw):
                i += 1
                continue
            last_party = party_raw
        else:
            nm = NO_PARTY_OFFICE_RE.match(line)
            if nm:
                office_raw = nm.group(1).strip()
                # Snyder: contests without party prefix inherit last_party.
                # For "STATE COMMITTEE" / "MEMBER OF ... STATE COMMITTEE", the
                # party is derived from last_party (set by "DEM GOVERNOR" etc.).
                party_raw = last_party
            else:
                i += 1
                continue
        # Expect "Vote For N" line soon after. Skip ahead up to ~6 lines.
        j = i + 1
        while j < n and not VOTE_FOR_RE.match(lines[j]):
            if PRIMARY_OFFICE_PARTY_RE.match(lines[j]) and _looks_like_office_header(
                PRIMARY_OFFICE_PARTY_RE.match(lines[j]).group(2).strip()
            ):
                break
            if NO_PARTY_OFFICE_RE.match(lines[j]):
                break
            j += 1
            if j - i > 8:
                break
        if j >= n or not VOTE_FOR_RE.match(lines[j]):
            i += 1
            continue
        office, district = normalize_office(office_raw)
        # Snyder's "STATE COMMITTEE" maps to a sentinel; resolve via party.
        if office == "__STATE_COMMITTEE__":
            office = ("Member of Republican State Committee" if party_raw == "REP"
                      else "Member of Democratic State Committee")
        # Walk rows from j+1 until we hit the next office header or
        # something clearly out of block.
        k = j + 1
        while k < n:
            s = lines[k]
            if PRIMARY_OFFICE_PARTY_RE.match(s) and _looks_like_office_header(
                PRIMARY_OFFICE_PARTY_RE.match(s).group(2).strip()
            ):
                break
            if NO_PARTY_OFFICE_RE.match(s):
                break
            if PER_PRECINCT_COMMITTEE_RE.match(s):
                break
            cm = CANDIDATE_ROW_RE.match(s)
            if cm:
                name_raw = cm.group(1).strip()
                if name_raw.upper() in SKIP_NAMES:
                    k += 1
                    continue
                name = _finalize_candidate(name_raw)
                if name.upper() in SKIP_NAMES:
                    k += 1
                    continue
                total = int(cm.group(2).replace(",", ""))
                ed = int(cm.group(3).replace(",", "")) if cm.group(3) else ""
                mail = int(cm.group(4).replace(",", "")) if cm.group(4) else ""
                prov = int(cm.group(5).replace(",", "")) if cm.group(5) else ""
                rows.append({
                    "county": county,
                    "office": office,
                    "district": district,
                    "party": party_raw,
                    "candidate": name,
                    "votes": total,
                    "election_day": ed,
                    "mail": mail,
                    "provisional": prov,
                })
            elif _is_skip_line(s):
                pass
            else:
                # A non-matching non-skip line: check if it's a continuation
                # of a candidate name (rare) — otherwise bail.
                # If it looks like a number-only fragment from a wrapped row
                # we ignore it; otherwise stop the block.
                if not re.match(r"^\s*[\d\s,]+%?\s*$", s):
                    # Could be end of block (e.g. next "Vote For" or page break)
                    if VOTE_FOR_RE.match(s):
                        break
                    # Don't break on column-header-looking lines.
                    pass
            k += 1
        i = k

    # Deduplicate: if the same (office, district, party, candidate) appears
    # multiple times (e.g. Cumberland splits Dem and Rep files, but we
    # parse each separately), keep the first.
    seen = set()
    deduped: list[dict] = []
    for r in rows:
        key = (r["office"], r["district"], r["party"], r["candidate"])
        if key in seen:
            continue
        seen.add(key)
        deduped.append(r)
    return deduped


def _looks_like_office_header(office_raw: str) -> bool:
    """Reject obvious false positives (e.g. "GENERAL PRIMARY ELECTION",
    "COUNTY, PA"). Accept lines starting with a known office keyword.

    Cumberland's PDF spreads office names across columns, producing lines
    like "LIEUTENANT              GOVERNOR" — collapse internal whitespace
    before matching so these still hit STATEWIDE_OFFICES keys.
    """
    upper = re.sub(r"\s+", " ", office_raw.upper()).strip()
    for key in STATEWIDE_OFFICES:
        if upper.startswith(key):
            return True
    # Common Electionware county/local offices
    if upper.startswith((
        "JUDGE OF", "DISTRICT ATTORNEY", "SHERIFF", "TREASUR", "CORONER",
        "PROTHONOTARY", "REGISTER AND RECORDER", "RECORDER OF DEEDS",
        "CLERK OF", "CONTROLLER", "COUNTY COMMISSIONER", "MAYOR",
        "COUNCIL", "SUPERVISOR", "CONSTABLE", "AUDITOR", "TAX COLLECTOR",
        "JUDGE OF ELECTION", "INSPECTOR OF ELECTION", "TOWNSHIP",
        "BOROUGH", "MEMBER OF THE", "DELEGATE", "ALTERNATE",
        "TOWNSHIP SUPERVISOR", "BOROUGH COUNCIL",
    )):
        return True
    return False


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
    # Deduplicate across multi-file inputs (Cumberland Dem + Rep shouldn't
    # collide since each contest appears in only one file, but guard anyway).
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