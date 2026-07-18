#!/usr/bin/env python3
"""
Parse Sullivan County PA 2024 Primary precinct results.

Source: Sullivan PA SOVC2+-+OFFICIAL_PA_Sullivan_2024_Primary_bdfx.pdf
(Long format: each row is "CANDIDATE  PRECINCT  VOTES". Contest headers
like "PRESIDENT OF THE UNITED STATES - REP (REP) (Vote for 1)" separate
contests. The first contest (DEM President) has no header — it's hardcoded.)

Usage:
    python parsers/pa_sullivan_primary_2024_results_parser.py <input.pdf> <output.csv>
"""

import csv
import re
import subprocess
import sys
import tempfile
from pathlib import Path


OFFICE_MAP = {
    "PRESIDENT OF THE UNITED STATES": "President",
    "UNITED STATES SENATOR": "U.S. Senate",
    "ATTORNEY GENERAL": "Attorney General",
    "AUDITOR GENERAL": "Auditor General",
    "STATE TREASURER": "State Treasurer",
    "REPRESENTATIVE IN CONGRESS": "U.S. House",
    "SENATOR IN THE GENERAL ASSEMBLY": "State Senate",
    "REPRESENTATIVE IN THE GENERAL ASSEMBLY": "State House",
}

CONTEST_RE = re.compile(
    r"^(.+?)\s*-\s*(DEM|REP)\s*\(\s*(?:DEM|REP)\s*\)\s*\(Vote for\s+(\d+)\)\s*$"
)

SULLIVAN_PRECINCTS = [
    "Cherry Township", "Bernice Precinct", "Colley Township",
    "Lopez Precinct", "Davidson Township", "Dushore Borough",
    "Eagles Mere Borough", "Elkland Township", "Forks Township",
    "Forksville Borough", "Fox Township", "Hillsgrove Township",
    "Laporte Township", "Laporte Borough", "Shrewsbury Township",
]

PRECINCT_RE = re.compile(
    r"^(.+?)\s+(" + "|".join(re.escape(p) for p in SULLIVAN_PRECINCTS) + r")\s+(\d+)\s*$"
)

SKIP_PREFIXES = (
    "Sullivan County,",
    "Statement of Votes Cast",
    "Precinct: All",
    "All All",
    "Show Write-ins",
    "Choice Precinct Votes",
    "Choice ",
    "2024",
    "All ",
)

FIELDNAMES = [
    "county", "precinct", "office", "district", "party",
    "candidate", "votes", "election_day", "provisional", "absentee",
]

SKIP_OFFICE_PREFIXES = (
    "DELEGATE TO THE",
    "ALTERNATE DELEGATE",
    "COUNTY REPUBLICAN COMMITTEE",
    "COUNTY DEMOCRATIC COMMITTEE",
)


def normalize_office(raw: str) -> tuple[str, str]:
    upper = raw.upper().strip()
    if upper in OFFICE_MAP:
        return (OFFICE_MAP[upper], "")
    for key, norm in OFFICE_MAP.items():
        if upper == key or upper.startswith(key + " "):
            return (norm, "")
    words = [w.capitalize() for w in raw.split()]
    return (" ".join(words), "")


def finalize_candidate(name: str) -> str:
    name = name.replace(",", "").strip()
    if name.lower().startswith("[write-in]"):
        return "Write-In Totals"
    if name.lower() == "write-in":
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


def extract_text(pdf_path: Path) -> str:
    with tempfile.NamedTemporaryFile(mode="w+", suffix=".txt", delete=False) as tmp:
        tmp_path = tmp.name
    try:
        subprocess.run(
            ["pdftotext", "-layout", str(pdf_path), tmp_path],
            check=True, capture_output=True,
        )
        with open(tmp_path, encoding="utf-8") as f:
            return f.read()
    finally:
        Path(tmp_path).unlink(missing_ok=True)


def parse_sullivan(text: str) -> list[dict]:
    rows: list[dict] = []
    current_office = "President"
    current_district = ""
    current_party = "DEM"
    for line in text.split("\n"):
        line = line.strip()
        if not line:
            continue
        if any(line.startswith(p) for p in SKIP_PREFIXES):
            continue
        cm = CONTEST_RE.match(line)
        if cm:
            raw_office = cm.group(1).strip()
            if any(raw_office.upper().startswith(p) for p in SKIP_OFFICE_PREFIXES):
                current_office = ""
                continue
            current_party = cm.group(2)
            current_office, current_district = normalize_office(raw_office)
            continue
        if not current_office:
            continue
        dm = PRECINCT_RE.match(line)
        if not dm:
            continue
        name = dm.group(1).strip()
        precinct = dm.group(2).strip()
        votes = int(dm.group(3))
        if name.upper() in ("INVALID", "DUPLICATES"):
            continue
        rows.append({
            "county": "Sullivan", "precinct": precinct,
            "office": current_office, "district": current_district,
            "party": current_party, "candidate": finalize_candidate(name),
            "votes": votes, "election_day": "", "provisional": "", "absentee": "",
        })
    return rows


def main(argv: list[str]) -> None:
    if len(argv) != 3:
        sys.exit(f"Usage: {Path(argv[0]).name} <input.pdf> <output.csv>")
    pdf_path = Path(argv[1])
    out_path = Path(argv[2])
    if not pdf_path.exists():
        sys.exit(f"Missing PDF: {pdf_path}")
    text = extract_text(pdf_path)
    rows = parse_sullivan(text)
    with out_path.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDNAMES)
        writer.writeheader()
        for r in rows:
            writer.writerow({k: r.get(k, "") for k in FIELDNAMES})
    print(f"Wrote {len(rows)} rows to {out_path}")


if __name__ == "__main__":
    main(sys.argv)