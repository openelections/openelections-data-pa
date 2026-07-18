#!/usr/bin/env python3
"""Parse Bucks County PA 2026 Primary precinct-level SOVC PDF.

Source format: "Statement of Votes Cast by Geography" (Bucks County EMS
report). Each precinct starts with a "Precinct <name>" line; each contest
is header "  OFFICE - D (Dem) (Vote for N)" or "  OFFICE - R (Rep) (Vote
for N)"; candidate rows are indented with "Name  votes  ED  MI  PR".
"Write-in" and "Total" rows are skipped (totals are recomputed from
candidates; write-ins are emitted as a single Write-In Totals row).

Usage:
    python parsers/pa_bucks_primary_2026_results_parser.py <input.pdf> <output.csv>
"""

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
}

DISTRICT_RE = re.compile(r"\b(\d+)(?:ST|ND|RD|TH)\b", re.IGNORECASE)

CONTEST_RE = re.compile(
    r"^\s+([A-Z][A-Z\s,#\-\d]+?)\s+-\s+[DR]\s+\((Dem|Rep)\)\s*\(Vote for \d+\)\s*$"
)

CANDIDATE_RE = re.compile(
    r"^\s+([A-Z][A-Za-z\.\'\- ]+?)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s*$"
)

PRECINCT_RE = re.compile(r"^Precinct\s+(.+?)\s*$")

FIELDNAMES = [
    "county", "precinct", "office", "district", "party",
    "candidate", "votes", "election_day", "mail", "provisional",
]


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


def finalize_candidate(name: str) -> str:
    name = name.strip()
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
    seen_write_in = False

    for line in text.split("\n"):
        s = line.strip()
        if not s:
            continue
        pm = PRECINCT_RE.match(line)
        if pm:
            current_precinct = pm.group(1).strip()
            current_office = ""
            seen_write_in = False
            continue
        cm = CONTEST_RE.match(line)
        if cm:
            office_raw, party = cm.group(1), cm.group(2)
            current_office, current_district = normalize_office(office_raw)
            current_party = party.upper()
            seen_write_in = False
            continue
        if not current_precinct or not current_office:
            continue
        if s.startswith("Total") or s.startswith("Overvotes") or s.startswith("Undervotes"):
            continue
        cand = CANDIDATE_RE.match(line)
        if not cand:
            continue
        name, votes, ed, mi, pr = cand.groups()
        final = finalize_candidate(name)
        if final == "Write-In Totals":
            if seen_write_in:
                continue
            seen_write_in = True
        results.append({
            "county": "Bucks",
            "precinct": current_precinct,
            "office": current_office,
            "district": current_district,
            "party": current_party,
            "candidate": final,
            "votes": int(votes.replace(",", "")),
            "election_day": int(ed.replace(",", "")),
            "mail": int(mi.replace(",", "")),
            "provisional": int(pr.replace(",", "")),
        })
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