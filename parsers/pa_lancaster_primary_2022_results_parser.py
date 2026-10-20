#!/usr/bin/env python3
"""
Parse Lancaster County PA 2022 Primary (5/17/2022) election precinct results.

Source: "Lancaster PA 2022 Primary Certified Results {Democratic,Republican}.html"
(the county's own consolidated certified-results pages, one per ballot party).
Same Clarity consolidated-HTML family as the 2022 general parser
(pa_lancaster_general_2022_results_parser.py). Each contest has a repeated
title band followed by a "Candidate | ... | Write In | Total" table: one row
per precinct (243 for countywide contests), blank cells for zeros, and a
final "Totals" row. Generated 6/17/2022 (after the GOP U.S. Senate recount
was completed and certified).

Because the two files are one ballot party each, every contest row (including
Write Ins) is tagged with the file's party.

Contests present (by-precinct): U.S. Senate, Governor, Lieutenant Governor,
U.S. House 11, State Senate 36/48, State House 37/41/43/49/96/97/98/99/100,
Member of the Democratic State Committee, Member of the Republican State
Committee (Male), Member of the Republican State Committee (Female). The
(Remale)/(Female) qualifiers are kept in the office name because they are two
separate races with separate tabulations; the single Democratic race is named
exactly as the PA ENR anchor prints it. No vote-type breakdowns, no
Registered Voters / Ballots Cast rows exist in this source.

Usage:
    python parsers/pa_lancaster_primary_2022_results_parser.py \\
        "<input.html>" "<output.csv>" <DEM|REP>
"""

import csv
import re
import sys
import warnings
from pathlib import Path

import pandas as pd

warnings.filterwarnings("ignore")

HEADERS = ["county", "precinct", "office", "district", "party", "candidate", "votes"]


def cell_str(v):
    if pd.isna(v):
        return ""
    return str(v).strip()


def normalize_office(name):
    u = re.sub(r"\s+", " ", name.upper())
    if u.startswith("UNITED STATES SENATOR"):
        return "U.S. Senate", ""
    if u.startswith("GOVERNOR"):
        return "Governor", ""
    if u.startswith("LIEUTENANT GOVERNOR"):
        return "Lieutenant Governor", ""
    m = re.search(r"REPRESENTATIVE IN CONGRESS\s*(\d+)", u)
    if m:
        return "U.S. House", m.group(1)
    m = re.search(r"SENATOR IN THE GENERAL ASSEMBLY\s*(\d+)", u)
    if m:
        return "State Senate", m.group(1)
    m = re.search(r"REPRESENTATIVE IN THE GENERAL ASSEMBLY\s*(\d+)", u)
    if m:
        return "State House", m.group(1)
    if u.startswith("MEMBER OF THE DEMOCRATIC STATE COMMITTEE"):
        return "Member of Democratic State Committee", ""
    if u.startswith("MEMBER OF THE REPUBLICAN STATE COMMITTEE"):
        if "(MALE)" in u:
            return "Member of Republican State Committee (Male)", ""
        if "(FEMALE)" in u:
            return "Member of Republican State Committee (Female)", ""
        return "Member of Republican State Committee", ""
    return re.sub(r"\s+", " ", name).strip().title(), ""


def parse(html_path, county="Lancaster", party=""):
    raw = Path(html_path).read_text()
    tbls = pd.read_html(raw)
    rows = []
    n_precincts = set()
    contests = []
    bad = 0

    def toint(x):
        x = x.replace(",", "").strip()
        return int(x) if x.isdigit() else 0

    last_title = None
    for df in tbls:
        try:
            row0 = [cell_str(v) for v in df.iloc[0]]
        except Exception:
            continue
        row0_nz = [v for v in row0 if v]
        if not row0_nz:
            continue
        # repeated-title band: office name cell repeated across the row
        if len(set(row0_nz)) < len(row0_nz) and len(row0_nz) >= 2:
            last_title = row0_nz[0]
            continue
        if row0_nz[0] != "Candidate":
            continue
        if last_title is None:
            print(f"WARN: Candidate table without preceding section band: shape {df.shape}", file=sys.stderr)
            continue
        title = re.sub(r"\s+", " ", last_title)
        office, district = normalize_office(title)
        contests.append((office, district))
        cands = []
        for v in [cell_str(v) for v in df.iloc[0, 1:]]:
            if not v:
                continue
            pm = re.match(r"^(.*\S)\s+(DEM|REP|GRN|LIB|KEY|IND|CON)$", v)
            cands.append(pm.group(1).strip() if pm else v)
        n_cand = len(cands)
        labels = [cell_str(v) for v in df.iloc[1, :]]
        wi_col = next((ci for ci, l in enumerate(labels) if l.lower() == "write in"), None)
        total_col = next((ci for ci, l in enumerate(labels) if l.strip().lower() == "total"), None)
        if wi_col is None or total_col is None:
            print(f"WARN: {office} {district} table missing Write In/Total columns", file=sys.stderr)
            continue
        for _, r in df.iloc[2:].iterrows():
            name = cell_str(r[0])
            if (not name or name.startswith("Copyright") or name.strip() == "Totals"
                    or name.strip() == "Precincts"):
                continue
            wi = cell_str(r[wi_col]) if wi_col < len(r) else ""
            tot = cell_str(r[total_col]) if total_col < len(r) else ""
            counts = [cell_str(r[ci]) for ci in range(1, n_cand + 1)]
            if not any(x for x in counts) and not wi and not tot:
                continue
            cv = [toint(x) for x in counts]
            wv = toint(wi)
            tv = toint(tot)
            if sum(cv) + wv != tv:
                bad += 1
                print(f"  MISMATCH {name} | {office} {district}: summed {sum(cv) + wv} vs printed {tv}",
                      file=sys.stderr)
            n_precincts.add(name)
            for cand, v in zip(cands, cv):
                rows.append({"county": county, "precinct": name, "office": office,
                             "district": district, "party": party, "candidate": cand, "votes": v})
            rows.append({"county": county, "precinct": name, "office": office,
                         "district": district, "party": party, "candidate": "Write Ins", "votes": wv})
    print(f"party={party} precincts: {len(n_precincts)}, contests: {len(contests)}, "
          f"row-sum mismatches: {bad}", file=sys.stderr)
    print(f"contests: {sorted(set(f'{o} {d}'.strip() for o, d in contests))}", file=sys.stderr)
    return rows


def main():
    if len(sys.argv) not in (3, 4):
        sys.exit(f"Usage: {Path(sys.argv[0]).name} <input.html> <output.csv> <DEM|REP>")
    rows = parse(sys.argv[1], party=sys.argv[3].upper() if len(sys.argv) == 4 else "")
    with open(sys.argv[2], "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=HEADERS)
        w.writeheader()
        w.writerows(rows)
    print(f"Wrote {len(rows)} rows to {sys.argv[2]}")


if __name__ == "__main__":
    main()