#!/usr/bin/env python3
"""
Parse Lehigh County PA 2022 General Election precinct results.

Source: "Lehigh County Official Precinct Results 2022 General.pdf"
("Summary Report Contest by Precinct", per-precinct section: bare precinct
name line, then per contest an office line + "Candidate Name / Party /
Total Vote" header and rows "DEM John Fetterman  Democrat  501".
No per-precinct registered-voters or ballots-cast totals in this export.)

Usage:
    python parsers/pa_lehigh_general_2022_results_parser.py \\
        "<input.pdf>" "<output.csv>"

Office titles in the source are printed truncated ("Representative in the
General Assembly 131st Representat"); district numbers are extracted from
the leading digits, so truncation is harmless.
"""

import csv
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

CAND_RE = re.compile(
    r"^(DEM|REP|LIB|GRN|KEY|CON|IND)\s+(.+?)\s{2,}"
    r"(Democrat|Republican|Libertarian|Green|Key|Constitution|Independent"
    r"|Nonpartisan|Non-Partisan)\s+([\d,]+)\s*$"
)
WI_RE = re.compile(r"^Write-in\s+([\d,]+)\s*$")
OFFICE_PATTERNS = [
    re.compile(r"^United States Senator\s*$", re.I),
    re.compile(r"^Governor and Lieutenant Governor\s*$", re.I),
    re.compile(r"^Representative in Congress in the \d+(?:st|nd|rd|th)?\s+Congressional\s+Dist.*$", re.I),
    re.compile(r"^Senator In the General Assembly \d+(?:st|nd|rd|th)?\s+Senatorial District\s*$", re.I),
    re.compile(r"^Representative in the General Assembly \d+(?:st|nd|rd|th)?.*$", re.I),
]
NAME_SKIP = re.compile(r"^(Page \d+ of|\d{2}/\d{2}/\d{4}|November 2022 General Election|Summary Report Contest by Precinct|Offical Election Results|LEHIGH|Candidate Name +Party)")


def normalize_office(line):
    u = line.strip()
    if re.match(r"^United States Senator$", u, re.I):
        return "U.S. Senate", ""
    m = re.match(r"^Representative in Congress in the (\d+)", u, re.I)
    if m:
        return "U.S. House", m.group(1)
    m = re.match(r"^Senator In the General Assembly (\d+)", u, re.I)
    if m:
        return "State Senate", m.group(1)
    m = re.match(r"^Representative in the General Assembly (\d+)", u, re.I)
    if m:
        return "State House", m.group(1)
    return u.title(), ""


def normalize_candidate(code, name):
    if name.lower().startswith("write-in"):
        return "Write Ins", ""
    party = code.upper()
    n = re.sub(r"\s+", " ", name).strip()
    if n.isupper():
        n = " ".join(w.title() if len(w) > 1 else w.capitalize() + "." for w in n.split())
    return n, party


def parse(text, county="Lehigh"):
    rows = []
    counts = Counter()
    precinct = None
    office = None
    for line in text.split("\n"):
        line = line.rstrip()
        if not line.strip():
            continue
        if NAME_SKIP.match(line.strip()):
            continue
        matched_office = False
        for op in OFFICE_PATTERNS:
            if op.match(line.strip()):
                if precinct is None:
                    # office header seen before any precinct is a bug we want to know about
                    print(f"WARN: office before precinct: {line[:50]}", file=sys.stderr)
                    break
                office, district = normalize_office(line)
                matched_office = True
                break
        if matched_office:
            continue
        mwi = WI_RE.match(line.strip())
        if mwi and office:
            rows.append({
                "county": county, "precinct": precinct,
                "office": office, "district": district,
                "party": "", "candidate": "Write Ins",
                "votes": int(mwi.group(1).replace(",", "")),
                "election_day": "", "mail": "", "provisional": "",
            })
            counts[(precinct, office)] += 0
            continue
        m = CAND_RE.match(line.strip())
        if m and office:
            cand, party = normalize_candidate(m.group(1), m.group(2))
            votes = int(m.group(4).replace(",", ""))
            rows.append({
                "county": county, "precinct": precinct,
                "office": office, "district": district,
                "party": party, "candidate": cand, "votes": votes,
                "election_day": "", "mail": "", "provisional": "",
            })
            counts[(precinct, office)] += votes
            continue
        # Fallback shape: "DEM John Fetterman / Davis Democrat 501" split on
        # runs of 2+ spaces into [code+name, party text, votes].
        parts = re.split(r"\s{2,}", line.strip())
        votes = None
        if len(parts) >= 3 and parts[-1].strip().replace(",", "").isdigit():
            votes = int(parts[-1].replace(",", ""))
            name_cell, party_cell = parts[0].strip(), parts[1].strip() if len(parts) > 2 else ""
        elif len(parts) == 2 and parts[-1].strip().replace(",", "").isdigit():
            votes = int(parts[-1].replace(",", ""))
            name_cell, party_cell = parts[0].strip(), ""
        if votes is not None and office:
            code, _, name_cell2 = name_cell.partition(" ")
            if code.upper() in ("DEM", "REP", "LIB", "GRN", "KEY", "CON", "IND"):
                candx, party = normalize_candidate(code, name_cell2)
            else:
                candx, party = normalize_candidate("", name_cell)
            rows.append({
                "county": county, "precinct": precinct,
                "office": office, "district": district,
                "party": party, "candidate": candx, "votes": votes,
                "election_day": "", "mail": "", "provisional": "",
            })
            counts[(precinct, office)] += votes
            continue
        # Anything else is a precinct name line.
        precinct = line.strip()
        office = None
    return rows, counts


def main():
    if len(sys.argv) != 3:
        sys.exit(f"Usage: {Path(sys.argv[0]).name} <input.pdf> <output.csv>")
    text = subprocess.run(
        ["pdftotext", "-layout", sys.argv[1], "-"], capture_output=True, text=True
    ).stdout
    rows, counts = parse(text)
    with open(sys.argv[2], "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["county", "precinct", "office", "district",
                                           "party", "candidate", "votes",
                                           "election_day", "mail", "provisional"])
        w.writeheader()
        w.writerows(rows)
    print(f"Wrote {len(rows)} rows to {sys.argv[2]}")


if __name__ == "__main__":
    main()