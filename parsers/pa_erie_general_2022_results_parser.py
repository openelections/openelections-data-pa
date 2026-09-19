#!/usr/bin/env python3
"""Parse Erie County 2022 general election results (Dominion SOVC Excel export).

Input: "Erie PA County-Official-Results-by-Precinct.xlsx" — 9 sheets:
  * Sheet1: per-precinct turnout table (Registered Voters, Cards Cast /
    Voters Cast by counting group) — source for Registered Voters / Ballots
    Cast rows and their election_day / mail / provisional breakdowns.
  * Sheets 2-9: one contest per sheet ("SOVC for: All Contests..." export).
    Contest title in column A row 2 ("NAME (Vote for N)"), column headers in
    row 4: [Precinct, Registered Voters, Precinct, <candidates with (PARTY)
    tags>, Total Votes, named "Qualified Write In" columns, "Unresolved
    Write-In"].  Vertically each precinct contributes 5 rows: precinct header,
    Election Day, Mail-In, Provisional, Total.  Trailing County / PA County /
    Cumulative / - Total rows are summary rows.

Output: OpenElections precinct CSV
  county,precinct,office,district,party,candidate,votes,election_day,early_voting,provisional
  (early_voting carries the source Mail-In votes, per the 2022 convention).

Notes:
  * Named write-in columns (and Unresolved Write-In) are aggregated into a
    single "Write Ins" candidate per contest/precinct (party empty), emitted
    only where the write-in total is nonzero.
  * The source contains only these contests: U.S. Senate, Governor, U.S. House
    16, and State House districts 1, 2, 3, 4, 6.  Statewide row offices and
    State Senate were not on this SOVC export (not up in 2022 for Erie) and
    are not invented.

Usage:
    uv run python pa_erie_general_2022_results_parser.py <input.xlsx> <output.csv>
"""

import csv
import re
import sys

import pandas as pd

COUNTY = "Erie"

GROUPS = {"Election Day": "election_day", "Mail-In": "early_voting", "Provisional": "provisional"}
SKIP_PRECINCT = {
    "County", "PA County", "Cumulative", "PA County - Total", "Cumulative - Total",
    "County - Total", "Precinct",
}


def office_for(title):
    """Map a source contest title to (office, district)."""
    t = re.sub(r"\(Vote for\s*\d+\)", "", title).strip()
    m = re.match(r"REPRESENTATIVE IN CONGRESS\s+(\d+)(ST|ND|RD|TH)\s+DISTRICT$", t, re.I)
    if m:
        return "U.S. House", m.group(1)
    m = re.match(r"REPRESENTATIVE IN THE GENERAL ASSEMBLY\s+DISTRICT\s+(\d+)$", t, re.I)
    if m:
        return "State House", m.group(1)
    if re.match(r"UNITED STATES SENATOR$", t, re.I):
        return "U.S. Senate", ""
    if re.match(r"GOVERNOR AND LIEUTENANT GOVERNOR$", t, re.I):
        return "Governor", ""
    raise ValueError("unmapped contest title: %r" % title)


def norm_party(tag):
    tag = tag.strip().strip("()").upper()
    return tag if tag else ""


def parse(input_path, output_path):
    xl = pd.ExcelFile(input_path)

    # ---- Sheet1: registered voters / ballots cast per precinct ------------
    bc = {}  # precinct -> dict
    s1 = xl.parse("Sheet1", header=None)
    cur = None
    for _, r in s1.iterrows():
        c0 = str(r[0]).strip() if pd.notna(r[0]) else ""
        if c0.startswith("Page:"):
            continue
        if c0 in GROUPS:
            if cur:
                col = GROUPS[c0]
                bc[cur][col] = int(float(r[5])) if pd.notna(r[5]) else 0
        elif c0 == "Total":
            if cur:
                bc[cur]["votes"] = int(float(r[5])) if pd.notna(r[5]) else 0
                bc[cur]["rv"] = int(float(r[1])) if pd.notna(r[1]) else 0
        elif c0 in SKIP_PRECINCT or c0.startswith("Cumulative") or "Total" in c0:
            cur = None
        elif c0:
            cur = c0
            bc.setdefault(cur, {})

    rows = []
    header = ["county", "precinct", "office", "district", "party", "candidate",
              "votes", "election_day", "early_voting", "provisional"]
    problems = []

    # ---- Contest sheets (2..9, one contest each) --------------------------
    for s in xl.sheet_names[1:]:
        df = xl.parse(s, header=None)
        title = str(df.iloc[1, 0])
        office, district = office_for(title)

        # candidate columns from header row (row index 3)
        cands = []  # (col, candidate, party)
        for i, v in enumerate(df.iloc[3]):
            if pd.isna(v):
                continue
            t = str(v).strip()
            if t in ("Precinct",) or "Registered" in t or "Undervotes" in t:
                continue
            if t.startswith("Total Votes"):
                continue
            if "Qualified Write In" in t or ("Unresolved" in t and "Write" in t):
                cands.append((i, "Write Ins", ""))
                continue
            name, party = t, ""
            if "\n" in t:
                parts = [p.strip() for p in t.split("\n") if p.strip()]
                name = parts[0]
                if len(parts) > 1 and parts[-1].startswith("("):
                    party = norm_party(parts[-1])
            cname = re.sub(r"\s+", " ", name).title() if name.isupper() else re.sub(r"\s+", " ", name)
            cands.append((i, cname, party))

        # walk data rows
        cur = None
        accum = {}  # col -> dict of group values for current precinct

        def flush():
            if cur is None:
                return
            merged = {}  # (candidate, party) -> [votes, ed, ev, pv]
            for col, cand, party in cands:
                d = accum.get(col, {})
                votes = int(float(d.get("votes", 0) or 0))
                ed = int(float(d.get("election_day", 0) or 0))
                ev = int(float(d.get("early_voting", 0) or 0))
                pv = int(float(d.get("provisional", 0) or 0))
                agg = merged.setdefault((cand, party), [0, 0, 0, 0])
                agg[0] += votes
                agg[1] += ed
                agg[2] += ev
                agg[3] += pv
            for (cand, party), (votes, ed, ev, pv) in merged.items():
                if cand == "Write Ins" and votes == 0:
                    continue
                if ed + ev + pv != votes:
                    problems.append((office, cand, cur, ed, ev, pv, votes))
                rows.append([COUNTY, cur, office, district, party, cand, votes,
                             ed, ev, pv])

        for _, r in df.iterrows():
            c0 = str(r[0]).strip() if pd.notna(r[0]) else ""
            if c0.startswith("Page:") or "(Vote for" in c0:
                continue
            if c0 in GROUPS:
                if cur is not None:
                    g = GROUPS[c0]
                    for col, _, _ in cands:
                        accum.setdefault(col, {})[g] = r[col] if pd.notna(r[col]) else 0
            elif c0 == "Total":
                if cur is not None:
                    for col, _, _ in cands:
                        accum.setdefault(col, {})["votes"] = r[col] if pd.notna(r[col]) else 0
            elif c0 in SKIP_PRECINCT or c0.startswith("Cumulative") or "Total" in c0:
                if c0 == "Precinct" or not c0:
                    continue
                flush()
                cur = None
                accum = {}
            elif c0:
                flush()
                cur = c0
                accum = {}
        flush()

    # Registered Voters / Ballots Cast rows (from Sheet1)
    for prec, d in bc.items():
        rows.append([COUNTY, prec, "Registered Voters", "", "", "", d.get("rv", 0), "", "", ""])
        bt = d.get("votes", 0)
        ed, ev, pv = d.get("election_day", 0), d.get("early_voting", 0), d.get("provisional", 0)
        if ed + ev + pv != bt:
            problems.append(("Ballots Cast", prec, prec, ed, ev, pv, bt))
        rows.append([COUNTY, prec, "Ballots Cast", "", "", "", bt, ed, ev, pv])

    with open(output_path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)

    for p in problems[:30]:
        sys.stderr.write("BREAKDOWN MISMATCH office=%s cand=%s prec=%s: ed=%s ev=%s prov=%s total=%s\n" % p)
    sys.stderr.write("rows=%d precincts=%d problems=%d\n" % (len(rows), len(bc), len(problems)))


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)
    parse(sys.argv[1], sys.argv[2])