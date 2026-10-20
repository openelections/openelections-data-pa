#!/usr/bin/env python3
"""
Parse Perry County PA 2022 General Election precinct results.

Source: "Perry PA 2022 General Election Results w Precinct Detail.pdf"
(Dominion "Statement of Votes Cast by Geography" report, 18 pages, 31
precincts; "Precinct <NAME>" blocks with "(Vote for N)" office headers and
candidate rows "NAME  TOTAL  PCT%  ED  MI  PR" (no per-precinct
registered-voters/turnout lines in this report).

Usage:
    python parsers/pa_perry_general_2022_results_parser.py \\
        "<input.pdf>" "<output.csv>"

Validation: each contest's printed "Total" row is compared against the sum
of that contest's candidate rows; mismatches are reported on stderr.
"""

import csv
import re
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

PRECRE = re.compile(r"^Precinct\s+(.+)$")
CONTEST_RE = re.compile(r"^([A-Z][A-Z0-9&/. -]+)\s*(?:-?\s*(\d+)(?:ST|ND|RD|TH)\s*DISTRICT)?\s*\(Vote for (\d+)\)\s*$")
CAND_RE = re.compile(r"^(.+?)\s+(\d[\d,]*)\s+[\d.]+%\s+(\d[\d,]*)\s+(\d[\d,]*)\s+(\d[\d,]*)\s*$")


def normalize_office(raw):
    name = raw.strip()
    up = name.upper()
    if up.startswith("UNITED STATES SENATOR"):
        return "U.S. Senate", ""
    m = re.match(r"^REPRESENTATIVE IN CONGRESS(?:-)?\s*(\d+)", up)
    if m:
        return "U.S. House", m.group(1)
    m = re.match(r"^SENATOR IN THE GENERAL ASSEMBLY\s*(\d+)", up)
    if m:
        return "State Senate", m.group(1)
    m = re.match(r"^REPRESENTATIVE IN THE GENERAL ASSEMBLY\s*(\d+)", up)
    if m:
        return "State House", m.group(1)
    if up.startswith("GOVERNOR"):
        return "Governor", ""
    # Fallback: title case
    return name.title(), ""


def title_cand(c):
    if c.lower().startswith("write-in"):
        return "Write Ins"
    words = []
    for w in c.split():
        if w.lower() in ("&", "/"):
            words.append(w)
        elif len(w) == 1:
            words.append(w.capitalize() + ".")
        elif w.isupper():
            words.append(w.capitalize())
        else:
            words.append(w)
    return " ".join(words).replace("-canning", "-Canning")


def parse(text, county="Perry"):
    rows = []
    printed = []
    precinct = office = district = None
    vote_for = 1
    agg = defaultdict(int)
    for line in text.split("\n"):
        line = line.strip()
        if not line:
            continue
        if line.startswith(("Statement of Votes Cast by Geography",
                            "Perry County, PA, PA_Perry_2022G",
                            "All Precincts, All Districts",
                            "Total Ballots Cast:",
                            "Choice    Votes    Vote %")):
            continue
        if re.match(r"^Page: \d+ of \d+", line):
            continue
        if re.match(r"^\d{4}-\d{2}-\d{2}|^\d{2}:\d{2}:\d{2}", line):
            continue
        mp = PRECRE.match(line)
        if mp:
            precinct = mp.group(1).strip()
            office = district = None
            continue
        if line.startswith("Choice") and "Votes" in line and "%" in line:
            continue
        m = CAND_RE.match(line)
        if m and precinct:
            cand = title_cand(m.group(1).strip())
            votes = int(m.group(2).replace(",", ""))
            ed, mi, pr = (int(m.group(i).replace(",", "")) for i in (3, 4, 5))
            if cand == "Total":
                printed.append((precinct, office, district, votes, agg))
                continue
            if office is None:
                print(f"WARN: candidate row before contest header: {line[:60]}", file=sys.stderr)
                continue
            rows.append({
                "county": county, "precinct": precinct, "office": office,
                "district": district, "party": "", "candidate": cand,
                "votes": votes, "election_day": ed, "mail": mi,
                "provisional": pr,
            })
            agg[(precinct, office, district)] += votes
            continue
        mo = CONTEST_RE.match(line)
        if mo:
            office, district = normalize_office(mo.group(1))
            continue
    # Validation against printed totals
    bad = 0
    for (precinct, o, d, votes, _agg) in printed:
        got = agg.get((precinct, o, d), -1)
        if got != votes:
            bad += 1
            print(f"  MISMATCH {precinct} | {o} {d}: printed {votes} vs summed {got}", file=sys.stderr)
    print(f"printed-total check: {len(printed)} contests, {bad} mismatches", file=sys.stderr)
    return rows, bad


def main():
    if len(sys.argv) != 3:
        sys.exit(f"Usage: {Path(sys.argv[0]).name} <input.pdf> <output.csv>")
    text = subprocess.run(
        ["pdftotext", "-layout", sys.argv[1], "-"], capture_output=True, text=True
    ).stdout
    rows, bad = parse(text)
    with open(sys.argv[2], "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["county", "precinct", "office", "district",
                                           "party", "candidate", "votes", "election_day",
                                           "mail", "provisional"])
        w.writeheader()
        w.writerows(rows)
    print(f"Wrote {len(rows)} rows to {sys.argv[2]}")


if __name__ == "__main__":
    main()