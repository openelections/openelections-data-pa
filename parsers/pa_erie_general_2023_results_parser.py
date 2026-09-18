#!/usr/bin/env python3
"""Parse Erie County 2023 general election results (Dominion SOVC Excel export).

Input: "Erie County Precinct Results 2023 General.xlsx" — 128 sheets:
  * Sheet1: per-precinct turnout table (Registered Voters, Cards Cast /
    Voters Cast by counting group) — source for Ballots Cast rows and their
    election_day / mail / provisional breakdowns.
  * Sheets 2-128: one contest per sheet. Contest title in column A row 2
    ("NAME (Vote for N)").  Row 4 holds the column headers; each contest is
    laid out in horizontal blocks: [Precinct, Registered Voters, Undervotes]
    then [Precinct, <candidates with (PARTY) tags>, Total Votes, named
    "Qualified Write In" columns, "Unresolved Write-In"].  Vertically each
    precinct contributes 5 rows: precinct header, Election Day, Mail-In,
    Provisional, Total.  Trailing PA County/Cumulative/County - Total rows
    are summary rows.

Output: OpenElections precinct CSV
  county,precinct,office,district,party,candidate,votes,election_day,mail,provisional

Notes:
  * Named write-in columns (and Unresolved Write-In) are aggregated into a
    single "Write-ins" candidate per contest/precinct (party empty).
  * Retention questions ("JUDICIAL RETENTION QUESTION - <NAME>") are emitted
    as office "Judge of the Superior Court - <NAME>" with Yes/No rows,
    following the county's 2025 file convention.
  * Undervotes columns are not part of the schema and are skipped.

Usage:
    python pa_erie_general_2023_results_parser.py <input.xlsx> <output.csv>
"""

import csv
import re
import sys

import pandas as pd

COUNTY = "Erie"

GROUPS = {"Election Day": "election_day", "Mail-In": "mail", "Provisional": "provisional"}
SKIP_PRECINCT = {
    "County", "PA County", "Cumulative", "PA County - Total", "Cumulative - Total",
    "County - Total", "Precinct",
}

# Contest title cleanups: (source title, office, district)
SPECIAL = {
    "COUNTY COUNCIL DISTRICT 1": ("County Council", "1"),
    "COUNTY COUNCIL DISTRICT 3": ("County Council", "3"),
    "COUNTY COUNCIL DISTRICT 5": ("County Council", "5"),
    "COUNTY COUNCIL DISTRICT 7": ("County Council", "7"),
    "JUDICIAL RETENTION QUESTION - JACK PANELLA":
        ("Superior Court Retention - Jack Panella", ""),
    "JUDICIAL RETENTION QUESTION - VICTOR P STABILE":
        ("Superior Court Retention - Victor P Stabile", ""),
}


# Candidate name fixes after .title() mangling (source is ALL CAPS)
NAME_FIX = {
    "Daniel Mccaffery": "Daniel McCaffery",
    "Harry F Smail Jr": "Harry F. Smail Jr.",
}


def fix_office(s):
    return s.replace(" Of The ", " of the ").replace(" Of ", " of ")


def titleize(s):
    s = re.sub(r"\s+", " ", s.strip())
    out = []
    for w in s.split(" "):
        if w.isupper() or w.islower():
            out.append(w.capitalize())
        else:
            out.append(w)  # preserves things like "At-Large" if mixed case
    return " ".join(out)


def clean_contest(title):
    t = re.sub(r"\(Vote for\s*\d+\)", "", title).strip()
    if t in SPECIAL:
        return SPECIAL[t]
    t = (t
         .replace("ERIE TREASURER", "ERIE CITY TREASURER")
         .replace("ERIE COUNCIL", "ERIE CITY COUNCIL")
         .replace("CORRY COUNCIL", "CORRY CITY COUNCIL"))
    return fix_office(titleize(t)), ""


def norm_party(tag):
    tag = tag.strip().strip("()").upper()
    if tag in ("DEM", "REP", "LIB", "IND", "GRN", "CON"):
        return tag
    if tag in ("DEM/REP", "REP/DEM"):
        return "D/R"
    return tag


def parse(input_path, output_path):
    xl = pd.ExcelFile(input_path)

    # ---- Sheet1: ballots cast per precinct -------------------------------
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
                bc[cur][col] = int(r[5]) if pd.notna(r[5]) else 0
        elif c0 == "Total":
            if cur:
                bc[cur]["votes"] = int(r[5]) if pd.notna(r[5]) else 0
                bc[cur]["rv"] = int(r[1]) if pd.notna(r[1]) else 0
        elif c0 in SKIP_PRECINCT or c0.startswith("Cumulative") or "Total" in c0:
            cur = None
        elif c0:
            cur = c0
            bc.setdefault(cur, {})

    rows = []
    header = ["county", "precinct", "office", "district", "party", "candidate",
              "votes", "election_day", "mail", "provisional"]
    problems = []

    # ---- Contest sheets ---------------------------------------------------
    for s in xl.sheet_names[1:]:
        df = xl.parse(s, header=None)
        title = str(df.iloc[1, 0])
        office, district = clean_contest(title)

        # candidate columns from header row 3
        cands = []  # (col, candidate, party)
        total_cols = []
        for i, v in enumerate(df.iloc[3]):
            if pd.isna(v):
                continue
            t = str(v).strip()
            if t in ("Precinct",) or "Registered" in t or "Undervotes" in t:
                continue
            if t.startswith("Total Votes"):
                total_cols.append(i)
                continue
            if "Qualified Write In" in t or "Unresolved" in t and "Write" in t:
                cands.append((i, "Write-ins", ""))
                continue
            if t in ("YES", "NO"):
                cands.append((i, t.capitalize(), ""))
                continue
            name = t
            party = ""
            if "\n" in t:
                parts = [p.strip() for p in t.split("\n") if p.strip()]
                name = parts[0]
                if len(parts) > 1 and parts[-1].startswith("("):
                    party = norm_party(parts[-1])
            if "WRITE-IN" in t.upper():
                cands.append((i, "Write-ins", ""))
            else:
                cname = re.sub(r"\s+", " ", name).title() if name.isupper() else re.sub(r"\s+", " ", name)
                cands.append((i, NAME_FIX.get(cname, cname), party))

        # walk data rows
        cur = None
        accum = {}  # col -> dict of group values for current precinct
        def flush():
            if cur is None:
                return
            merged = {}  # (candidate, party) -> [votes, ed, ml, pv]
            for col, cand, party in cands:
                d = accum.get(col, {})
                votes = int(d.get("votes", 0) or 0)
                ed = int(d.get("election_day", 0) or 0)
                ml = int(d.get("mail", 0) or 0)
                pv = int(d.get("provisional", 0) or 0)
                agg = merged.setdefault((cand, party), [0, 0, 0, 0])
                agg[0] += votes; agg[1] += ed; agg[2] += ml; agg[3] += pv
            for (cand, party), (votes, ed, ml, pv) in merged.items():
                if ed + ml + pv != votes:
                    problems.append((office, cand, cur, ed, ml, pv, votes))
                rows.append([COUNTY, cur, office, district, party, cand, votes,
                             ed, ml, pv])

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

        # Registered Voters per precinct (from column-0 block, Total row col 1)
        pass  # taken from Sheet1 below

    # Registered Voters rows (from Sheet1 col1 on Total rows)
    for prec, d in bc.items():
        rows.append([COUNTY, prec, "Registered Voters", "", "", "", d.get("rv", 0), "", "", ""])
        bt = d.get("votes", 0)
        ed, ml, pv = d.get("election_day", 0), d.get("mail", 0), d.get("provisional", 0)
        if ed + ml + pv != bt:
            problems.append(("Ballots Cast", prec, prec, ed, ml, pv, bt))
        rows.append([COUNTY, prec, "Ballots Cast", "", "", "", bt, ed, ml, pv])

    with open(output_path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)

    for p in problems[:30]:
        sys.stderr.write("BREAKDOWN MISMATCH %s / %s / %s: ed=%s mail=%s prov=%s total=%s\n" % p)
    sys.stderr.write("rows=%d precincts=%d problems=%d\n" % (len(rows), len(bc), len(problems)))


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)
    parse(sys.argv[1], sys.argv[2])