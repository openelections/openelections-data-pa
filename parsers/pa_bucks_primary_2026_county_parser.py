#!/usr/bin/env python3
"""County-level parser for Bucks County PA 2026 primary "Statement of Votes
Cast by Geography - Grand Totals" PDF.

The Grand Totals report is the county-wide aggregation of the per-precinct
SOVC-by-geography file. Contest headers look like::

        GOVERNOR - D (Dem) (Vote for 1)

and candidate rows are indented underneath with five columns (Votes, ED,
MI, PR). This parser reads the PDF via ``pdftotext -layout`` and emits one
row per candidate per county.

Usage:
    python parsers/pa_bucks_primary_2026_county_parser.py <input.pdf> <output.csv>
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
    "PRESIDENTIAL ELECTORS": ("President", False),
    "DEMOCRATIC STATE COMMITTEE": ("Member of Democratic State Committee", False),
    "REPUBLICAN STATE COMMITTEE": ("Member of Republican State Committee", False),
}

# Contest header: "    GOVERNOR - D (Dem) (Vote for 1)" — office, optional
# ordinal district, single-letter party code, full party name, vote-for.
# Headers may be at column 0 (State House) or indented (Governor).
CONTEST_RE = re.compile(
    r"^\s*(.+?)\s+(?:-\s*[A-Z]\s+)?\((Dem|Rep|GP|LBR|Ind|Grn|Wep|Wfp|Con)\)\s*"
    r"\(Vote for\s+\d+\)\s*$",
    re.IGNORECASE,
)

DISTRICT_ORDINAL_RE = re.compile(
    r"\b(\d+)(?:ST|ND|RD|TH)\s+(?:LEGISLATIVE\s+|CONGRESSIONAL\s+|SENATORIAL\s+)?DISTRICT\b",
    re.IGNORECASE,
)
# Bare ordinal suffix: "18TH", "1ST", "2ND", "3RD", "140TH" — Bucks writes
# district numbers as bare ordinals on contest headers (no "DISTRICT" word).
DISTRICT_BARE_ORDINAL_RE = re.compile(
    r"\b(\d+)(?:ST|ND|RD|TH)\b", re.IGNORECASE,
)
DISTRICT_PLAIN_RE = re.compile(
    r"\b(\d+)\w*\s+DISTRICT\b", re.IGNORECASE
)

# Candidate row: indented (3+ spaces), name, then 4 integers (votes, ed, mi, pr).
CANDIDATE_RE = re.compile(
    r"^\s+([A-Z][A-Za-z\.\'\-,\s]+?)\s+"
    r"(\d{1,3}(?:,\d{3})*|\d+)\s+"
    r"(\d{1,3}(?:,\d{3})*|\d+)\s+"
    r"(\d{1,3}(?:,\d{3})*|\d+)\s+"
    r"(\d{1,3}(?:,\d{3})*|\d+)\s*$"
)

SKIP_NAMES = {"WRITE-IN", "WRITE IN", "TOTAL", "TIMES CAST", "OVERVOTES",
              "UNDERVOTES", "NOT ASSIGNED", "UNRESOLVED WRITE-IN"}

# Per-precinct committee race substrings — these are not county-level
# contests and should be skipped.
PER_PRECINCT_COMMITTEE_TOKENS = (
    "COUNTY COMMITTEE", "COUNTY COMMITTEEMAN", "COUNTY COMMITTEEWOMAN",
    "COMMITTEE PERSON", "COMMITTEEPERSON", "COMMITTEEMAN", "COMMITTEEWOMAN",
)

PARTY_NORMALIZE = {"DEM": "DEM", "REP": "REP", "GP": "GP", "LBR": "LBR",
                   "IND": "IND", "GRN": "GRN", "WEP": "WEP", "WFP": "WFP",
                   "CON": "CON"}

FIELDNAMES = [
    "county", "office", "district", "party", "candidate",
    "votes", "election_day", "mail", "provisional",
]

_ROMAN_RE = re.compile(r"^[IVX]+$")


def _finalize_candidate(raw: str) -> str:
    s = raw.replace(",", "").strip()
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
    # Strip trailing " - D" / " - R" party-code suffix before matching.
    upper = re.sub(r"\s*-\s*[A-Z]\s*$", "", upper)
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
        else:
            # Bare ordinal at end of office name (Bucks: "REPRESENTATIVE IN
            # THE GENERAL ASSEMBLY 18TH"). Only treat as district if the
            # office is one we'd extract a district for.
            for key, (norm, extract) in OFFICE_MAP.items():
                if upper.startswith(key) and extract:
                    bm = DISTRICT_BARE_ORDINAL_RE.search(upper)
                    if bm:
                        district = str(int(bm.group(1)))
                        upper = DISTRICT_BARE_ORDINAL_RE.sub("", upper).strip()
                    break
    for key, (norm, extract) in OFFICE_MAP.items():
        if upper.startswith(key):
            return (norm, district if extract else "")
    return (raw.title(), district)


def parse_bucks(pdf_path: Path) -> list[dict]:
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
        party_full = m.group(2)
        # Map "Dem"/"Rep" to DEM/REP
        party_map = {"DEM": "DEM", "REP": "REP", "GP": "GP", "LBR": "LBR",
                     "IND": "IND", "GRN": "GRN", "WEP": "WEP", "WFP": "WFP",
                     "CON": "CON"}
        party_raw = party_map.get(party_full.upper(), party_full.upper())
        # Skip per-precinct committee races (not county-level).
        office_up = re.sub(r"\s+", " ", office_raw.upper())
        if any(tok in office_up for tok in PER_PRECINCT_COMMITTEE_TOKENS):
            i += 1
            continue
        office, district = normalize_office(office_raw)
        # Walk candidate rows until next contest header or blank section.
        k = i + 1
        while k < n:
            s = lines[k]
            if CONTEST_RE.match(s):
                break
            cm = CANDIDATE_RE.match(s)
            if cm:
                name_raw = cm.group(1).strip()
                name_up = name_raw.upper()
                if name_up in SKIP_NAMES:
                    k += 1
                    continue
                total = int(cm.group(2).replace(",", ""))
                ed = int(cm.group(3).replace(",", ""))
                mi = int(cm.group(4).replace(",", ""))
                pr = int(cm.group(5).replace(",", ""))
                rows.append({
                    "county": "Bucks",
                    "office": office,
                    "district": district,
                    "party": party_raw,
                    "candidate": _finalize_candidate(name_raw),
                    "votes": total,
                    "election_day": ed,
                    "mail": mi,
                    "provisional": pr,
                })
            k += 1
        i = k

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
    if len(argv) != 3:
        sys.exit(f"Usage: {Path(argv[0]).name} <input.pdf> <output.csv>")
    pdf_path = Path(argv[1])
    out_path = Path(argv[2])
    if not pdf_path.exists():
        sys.exit(f"Missing PDF: {pdf_path}")
    rows = parse_bucks(pdf_path)
    with out_path.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDNAMES)
        w.writeheader()
        for r in rows:
            w.writerow(r)
    offices = len({(r["office"], r["district"]) for r in rows})
    print(f"Wrote {len(rows)} rows across {offices} contests to {out_path}")


if __name__ == "__main__":
    main(sys.argv)