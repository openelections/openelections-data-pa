#!/usr/bin/env python3
"""Parse Bucks County PA 2026 Primary precinct-level SOVC PDF.

Source: "Statement of Votes Cast by Geography" (Bucks County EMS report).
Certified input: "Bucks County 060826 - Second Signing EMS Report - Precinct
Level Only.pdf" (Gen Pri 5/26 Final, Second Signing, 2026-06-09).  Each
precinct starts with a "Precinct <name>" line; each contest is a header like:

    GOVERNOR - D (Dem) (Vote for 1)
    REPRESENTATIVE IN THE GENERAL ASSEMBLY 143RD - R (Rep) (Vote for 1)
    DEMOCRATIC STATE COMMITTEE (Dem) (Vote for 13)                 (column 0 or indented)
    BENSALEM TOWNSHIP LOWER EAST 1 COMMITTEEPERSON (Dem) (Vote for 2)
    BEDMINSTER TOWNSHIP EAST COMMITTEEMAN (Rep) (Vote for 1)
    YARDLEY BOROUGH COMMITTEEWOMAN (Rep) (Vote for 1)

Candidate rows are indented with five columns: "Name  votes  ED  MI  PR".
Office mapping:
- statewide/federal/legislative headers map through OFFICE_MAP, with the
  district number taken from the trailing ordinal ("... 143RD").
- DEMOCRATIC/REPUBLICAN STATE COMMITTEE → "Member of Democratic/Republican
  State Committee" (no district printed here; the DOS apportionment for all
  of Bucks is "Bucks County (District 9)").
- locality COMMITTEEPERSON/COMMITTEEMAN/COMMITTEEWOMAN contests (party
  county-committee races printed per precinct) → "County Committeeperson
  <locality>" etc., following the Berks 2026 primary precinct-file
  precedent. Without this (and State Committee) mapping these contests'
  candidate rows inherited the preceding contest's office/party under the
  original CONTEST_RE (which required the "- D (Dem)" form and could not
  digest ordinals like "143RD"), which produced 32 duplicate
  (precinct, office, party, candidate) keys with conflicting totals —
  e.g. Leann Hart under Governor/DEM for Bensalem Twp Lower East 1 both as
  her Democratic State Committee vote (246) and her Bensalem Twp Lower
  East 1 Committeeperson vote (221).
- The source prints REPRESENTATIVE IN CONGRESS without a district (Bucks
  lies entirely in CD-1); district "1" is filled in to match the DOS
  certified anchor (2026/20260519__pa__primary__county.csv, U.S. House
  district 1).

Multiple "Write-in" lines under one contest (vote-for-N contests have one
line per write-in slot) are SUMMED into a single "Write-In Totals" row so
no write-in votes are lost; "Total", "Overvotes"/"Undervotes" and page
headers are skipped. The source carries no per-precinct Registered
Voters/Ballots Cast table, so no RV/BC rows are emitted.

Usage:
    python parsers/pa_bucks_primary_2026_results_parser.py <input.pdf> <output.csv>
"""

from __future__ import annotations

import csv
import re
import subprocess
import sys
from collections import namedtuple
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
    "DEMOCRATIC STATE COMMITTEE": ("Member of Democratic State Committee", False),
    "REPUBLICAN STATE COMMITTEE": ("Member of Republican State Committee", False),
}

DISTRICT_RE = re.compile(r"\b(\d+)(?:ST|ND|RD|TH)\b", re.IGNORECASE)

# Contest header: optional "- D"/"- R" party code, then (Dem)/(Rep), then
# (Vote for N). Handles both "GOVERNOR - D (Dem)" and the committee header
# "BENSALEM TOWNSHIP ... COMMITTEEPERSON (Dem)" (also the misspelling
# "COMMITTEPERSON"), indented or at column 0.
CONTEST_RE = re.compile(
    r"^\s*(.+?)\s+(?:-\s*[DR]\s+)?\((Dem|Rep)\)\s*\(Vote for \d+\)\s*$",
    re.IGNORECASE,
)

COMMITTEE_ENDS = ("COMMITTEEPERSON", "COMMITTEPERSON",
                  "COMMITTEEMAN", "COMMITTEEWOMAN")

CANDIDATE_RE = re.compile(
    r"^\s+([A-Z][A-Za-z\.\'\- ]+?)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s*$"
)

PRECINCT_RE = re.compile(r"^Precinct\s+(.+?)\s*$")

FIELDNAMES = [
    "county", "precinct", "office", "district", "party",
    "candidate", "votes", "election_day", "mail", "provisional",
]

Row = namedtuple("Row", "office district party candidate votes election_day mail provisional")

# U.S. House district used when the source header omits it (Bucks = CD-1).
DEFAULT_US_HOUSE_DISTRICT = "1"


def _title_muni(raw: str) -> str:
    """Title-case a locality, keeping ordinal suffixes lowercase (1st)."""
    words = []
    for w in raw.split():
        m = re.match(r"^(\d+)(ST|ND|RD|TH)$", w.upper())
        if m:
            words.append(m.group(1) + m.group(2).lower())
        elif "-" in w and w.upper() != w:
            words.append("-".join(p.capitalize() for p in w.split("-")))
        elif len(w) > 2 and w[:2].upper() == "MC":
            words.append("Mc" + w[2:].capitalize())
        else:
            words.append(w.capitalize())
    return " ".join(words)


def normalize_office(raw: str) -> tuple[str, str]:
    """Map a contest header to (office, district)."""
    upper = re.sub(r"\s+", " ", re.sub(r"\s*-\s*[DR]$", "", raw.upper())).strip()
    district = ""
    dm = DISTRICT_RE.search(upper)
    if dm:
        district = str(int(dm.group(1)))
        upper = DISTRICT_RE.sub("", upper).strip()
    for key, (norm, extract) in OFFICE_MAP.items():
        if upper == key or upper.startswith(key + " ") or upper.startswith(key):
            return (norm, district if extract else "")
    return (raw.title(), district)


def classify_contest(header: str) -> tuple[str, str]:
    """Contest header -> (office, district)."""
    upper = re.sub(r"\s+", " ",
                   re.sub(r"\s*-\s*[DR]$", "", header.upper())).strip()
    m = re.search(r"\b(COMMITTEEPERSON|COMMITTEPERSON|COMMITTEEMAN|COMMITTEEWOMAN)\b", upper)
    if m:
        kind = m.group(1)
        locality = _title_muni(upper[:m.start(1)].strip())
        label = ("County Committeeperson" if kind in
                 ("COMMITTEEPERSON", "COMMITTEPERSON")
                 else ("County Committeeman" if kind == "COMMITTEEMAN"
                       else "County Committeewoman"))
        return (f"{label} {locality}".strip(), "")
    office, district = normalize_office(header)
    if office == "U.S. House" and not district:
        district = DEFAULT_US_HOUSE_DISTRICT
    return (office, district)


def finalize_candidate(name: str) -> str:
    name = name.strip()
    if name.lower() == "write-in":
        return "Write-In Totals"
    parts = name.split()
    out = []
    for w in parts:
        m = re.match(r"^(\d+)(ST|ND|RD|TH)$", w.upper())
        if w.upper() in ("JR", "SR", "II", "III", "IV"):
            out.append(w.upper().replace("JR", "Jr.").replace("SR", "Sr."))
        elif m:
            out.append(m.group(1) + m.group(2).lower())
        elif "-" in w and w.upper() != w:
            out.append("-".join(p.capitalize() for p in w.split("-")))
        elif "’" in w or "'" in w:
            out.append(w[0].upper() + w[1:].lower())
        elif len(w) > 2 and w[:2].upper() == "MC":
            out.append("Mc" + w[2:].capitalize())
        else:
            out.append(w.capitalize())
    return " ".join(out)


def extract_text(pdf_path: str) -> str:
    res = subprocess.run(
        ["pdftotext", "-layout", pdf_path, "-"],
        capture_output=True, text=True, check=True,
    )
    return res.stdout


def parse(text: str) -> list[dict]:
    results: list[dict] = []
    current_precinct = ""
    current_office = ""
    current_district = ""
    current_party = ""
    write_in_sum = None  # [votes, ed, mi, pr] accumulator, None = no write-ins yet

    def flush_write_in():
        nonlocal write_in_sum
        if write_in_sum is not None and (current_precinct and current_office):
            results.append({
                "county": "Bucks",
                "precinct": current_precinct,
                "office": current_office,
                "district": current_district,
                "party": current_party,
                "candidate": "Write-In Totals",
                "votes": write_in_sum[0],
                "election_day": write_in_sum[1],
                "mail": write_in_sum[2],
                "provisional": write_in_sum[3],
            })
        write_in_sum = None

    for line in text.split("\n"):
        s = line.strip()
        if not s:
            continue
        if s.startswith("Statement of Votes Cast") or "Total Ballots Cast:" in s \
                or s.startswith("Choice") or s.startswith("Jurisdiction Wide") \
                or s.startswith("All Precincts, All") or re.match(r"^Page: \d+", s) \
                or re.match(r"^2026-\d\d-\d\d", s) or re.match(r"^\d\d:\d\d:\d\d", s):
            continue
        pm = PRECINCT_RE.match(line)
        if pm:
            flush_write_in()
            current_precinct = pm.group(1).strip()
            current_office = ""
            continue
        cm = CONTEST_RE.match(line)
        if cm:
            flush_write_in()
            current_office, current_district = classify_contest(cm.group(1))
            current_party = cm.group(2).upper()
            continue
        if not current_precinct or not current_office:
            continue
        # Per-contest "Total"/"Overvotes"/"Undervotes" summary rows: skip.
        if re.match(r"^(Total|Overvotes|Undervotes)\b", s):
            continue
        cand = CANDIDATE_RE.match(line)
        if not cand:
            continue
        name, votes, ed, mi, pr = cand.groups()
        vals = [int(x) for x in (votes, ed, mi, pr)]
        if name.strip().lower() == "write-in":
            if write_in_sum is None:
                write_in_sum = vals[:]
            else:
                for i in range(4):
                    write_in_sum[i] += vals[i]
            continue
        flush_write_in()
        results.append({
            "county": "Bucks",
            "precinct": current_precinct,
            "office": current_office,
            "district": current_district,
            "party": current_party,
            "candidate": finalize_candidate(name),
            "votes": vals[0],
            "election_day": vals[1],
            "mail": vals[2],
            "provisional": vals[3],
        })
    flush_write_in()
    return results


def main():
    if len(sys.argv) != 3:
        sys.exit(f"Usage: {Path(sys.argv[0]).name} <input.pdf> <output.csv>")
    pdf_path, out_path = sys.argv[1], sys.argv[2]
    text = extract_text(pdf_path)
    rows = parse(text)
    with open(out_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDNAMES)
        w.writeheader()
        w.writerows(rows)
    precincts = len({r["precinct"] for r in rows})
    print(f"Wrote {len(rows)} rows across {precincts} precincts to {out_path}")


if __name__ == "__main__":
    main()
