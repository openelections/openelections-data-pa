#!/usr/bin/env python3
"""
Parse Bucks County PA 2022 General Election precinct results.

Source: "Bucks PA 2022 General Certified Election Results by Precinct.pdf"
(Dominion "Certified Election Results by Precinct" report, 190 pages, 304
precincts; page header "All Precincts, ..." then "Precinct <Name>" blocks
with "(Vote for N)" office headers and candidate rows
"Name  TOTAL  ED  MI  PR"; 8 State Senate / 10 State House districts; two
local questions).

Usage:
    python parsers/pa_bucks_general_2022_results_parser.py \\
        "<input.pdf>" "<output.csv>"

Text is extracted with pdftotext -layout (plain whitespace rows, no
percentage column).
"""

import re
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

SKIP_EXACT = {
    "Choice Votes ED MI PR",
    "Choice    Votes    ED    MI    PR",
}

OFFICE_RE = re.compile(r"^([A-Z][A-Z0-9 &'/-]+(?:\s+\d+(?:st|nd|rd|th))?)?\s*\(Vote for (\d+)\)\s*$")
SENATOR_RE = re.compile(r"^SENATOR IN THE GENERAL ASSEMBLY\s+(\d+)(?:st|nd|rd|th)?\s*\(Vote for (\d+)\)$", re.I)
HOUSE_RE = re.compile(
    r"^REPRESENTATIVE IN THE GENERAL ASSEMBLY\s+(\d+)(?:st|nd|rd|th)?\s*\(Vote for (\d+)\)$",
    re.I,
)
GENERIC_RE = re.compile(r"^([A-Z][A-Z0-9 &'/.-]+)\s*\(Vote for (\d+)\)$")
CAND_RE = re.compile(r"^(.+?)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)$")
PRECRE = re.compile(r"^Precinct\s+(.+)$")
DISTRICT_FIX = {"Referendem": "Referendum"}


def parse(text, county="Bucks"):
    rows = []
    total_rows = []
    precinct = None
    office = None
    district = ""
    vote_for = 1
    for line in text.split("\n"):
        line = line.strip()
        if not line:
            continue
        if line in SKIP_EXACT or re.match(
            r"^(Certified Election Results by Precinct|General Election, November 8, 2022"
            r"|All Precincts, All Districts|Total Ballots Cast: |\d{4}-\d{2}-\d{2} |\d{2}:)",
            line,
        ):
            continue
        m = PRECRE.match(line)
        if m:
            precinct, office, district = m.group(1).strip(), None, ""
            continue
        mo = SENATOR_RE.match(line)
        if mo:
            office, district, vote_for = "State Senate", mo.group(1), int(mo.group(2))
            continue
        mh = HOUSE_RE.match(line)
        if mh:
            office, district, vote_for = "State House", mh.group(1), int(mh.group(2))
            continue
        mg = GENERIC_RE.match(line)
        if mg:
            name = DISTRICT_FIX.get(mg.group(1).title().strip(), mg.group(1).title().strip())
            if name == "United States Senator":
                office = "U.S. Senate"
            elif name == "Governor And Lieutenant Governor":
                office = "Governor"
            elif name == "Representative In Congress":
                office = "U.S. House"
            else:
                office = name
            district, vote_for = "", int(mg.group(2))
            continue
        mc = CAND_RE.match(line)
        if mc and precinct and office:
            cand = mc.group(1).strip()
            votes, ed, mi, pr = (int(mc.group(i)) for i in (2, 3, 4, 5))
            rec = {
                "county": county,
                "precinct": precinct,
                "office": office,
                "district": district,
                "party": "",
                "candidate": "Write Ins" if cand.lower().startswith("write-in") else cand,
                "votes": votes,
                "election_day": ed,
                "mail": mi,
                "provisional": pr,
            }
            if cand == "Total":
                total_rows.append((precinct, office, district, votes, rec))
                continue
            rows.append(rec)
    return rows, total_rows


def normalize_candidate(c):
    # Ticket names "Josh Shapiro/Austin Davis" -> "Josh Shapiro / Austin Davis" stays
    c = re.sub(r"\bDiGiulio\b", "DiGiulio", c)
    c = re.sub(r"\b(\w+)\b", lambda m: m.group(1), c)
    return c


def main():
    if len(sys.argv) != 3:
        sys.exit(f"Usage: {Path(sys.argv[0]).name} <input.pdf> <output.csv>")
    pdf = Path(sys.argv[1])
    out = Path(sys.argv[2])
    text = subprocess.run(
        ["pdftotext", "-layout", str(pdf), "-"], capture_output=True, text=True
    ).stdout
    rows, total_rows = parse(text)
    with out.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    print(f"Wrote {len(rows)} rows to {out}")

    # Printed-total verification: contest Total rows should equal the sum of
    # the candidate rows above them.
    agg = defaultdict(int)
    for r in rows:
        agg[(r["precinct"], r["office"], r["district"])] += r["votes"]
    bad = 0
    for (precinct, office, district, votes, rec) in total_rows:
        got = agg.get((precinct, office, district))
        if got is None:
            agg[(precinct, office, district)] = 0
            got = agg[(precinct, office, district)]
        agg[(precinct, office, district)] = got  # keep original
    # separate check: rebuild from candidate rows only
    cand_agg = defaultdict(int)
    for r in rows:
        cand_agg[(r["precinct"], r["office"], r["district"])] += int(r["votes"])
    for (precinct, office, district, votes, rec) in total_rows:
        if cand_agg.get((precinct, office, district), -1) != votes:
            bad += 1
            print(f"  MISMATCH {precinct} | {office} {district}: printed {votes} vs summed {cand_agg.get((precinct, office, district))}")
    print(f"printed-total check: {len(total_rows)} contests, {bad} mismatches")


if __name__ == "__main__":
    import csv

    main()