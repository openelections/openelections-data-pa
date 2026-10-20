#!/usr/bin/env python3
"""
Parse Lancaster County PA 2022 General Election precinct results.

Source: "Lancaster PA 2022 General Certified Results.html" (county's own
consolidated certified results page). Each contest has a section header
band (repeated title cells) followed by a "Candidate | ... | Write In |
Total" table: one row per precinct (243 data rows), blank cells for zero
vote counts. This consolidation includes by-precinct detail for U.S.
Senate, Governor, U.S. House 11, State Senate 36/48, State House
37/41/43/49/96/97/98/99/100, and the Conestoga Supervisor Referendum;
other local contests (remaining House districts, row offices, township
offices) appear only countywide in this source and are not parsed.

Usage:
    python parsers/pa_lancaster_general_2022_results_parser.py \\
        "<input.html>" "<output.csv>"
"""

import csv
import re
import sys
import warnings
from pathlib import Path

import pandas as pd

warnings.filterwarnings("ignore")


def normalize_office(name):
    u = name.upper()
    if u.startswith("UNITED STATES SENATOR"):
        return "U.S. Senate", ""
    if u.startswith("GOVERNOR"):
        return "Governor", ""
    m = re.search(r"REPRESENTATIVE IN CONGRESS\s*(\d+)", u)
    if m:
        return "U.S. House", m.group(1)
    m = re.search(r"SENATOR IN THE GENERAL ASSEMBLY\s*(\d+)", u)
    if m:
        return "State Senate", m.group(1)
    m = re.search(r"REPRESENTATIVE IN THE GENERAL ASSEMBLY\s*(\d+)", u)
    if m:
        return "State House", m.group(1)
    if "REFERENDUM" in u:
        return name.title(), ""
    return name.title(), ""


def parse(html_path, county="Lancaster"):
    raw = Path(html_path).read_text()
    tbls = pd.read_html(raw)
    rows = []
    n_precincts = set()
    bad = 0

    def cell_str(v):
        import pandas as _pd
        if _pd.isna(v):
            return ""
        return str(v).strip()

    for df in tbls:
        if df.shape[0] < 30 or df.shape[1] < 3:
            continue
        # by-precinct tables: row0 starts with 'Candidate'
        if not (df.shape[0] and cell_str(df.iloc[0, 0]) == "Candidate"):
            continue
        # office comes from a nearby section band; recover from the table
        # itself is impossible, so keep the last seen repeated-title band
        pass
    # Because pandas tables lose adjacency, re-scan: for each table index,
    # remember the section band title from the table 1-6 BEFORE it.
    last_title = None
    for i, df in enumerate(tbls):
        try:
            row0 = [cell_str(v) for v in df.iloc[0] if cell_str(v)]
        except Exception:
            continue
        if not row0:
            continue
        if len(set(row0)) < len(row0) and len(row0) >= 2:
            # repeated title band
            last_title = "/".join(row0[:1])
            continue
        u = " ".join(row0).upper()
        if row0 and row0[0] == "Candidate" and u.count("CANDIDATES") or (
            len(df) > 30 and row0[-1] not in ("Total",)
        ):
            pass
        if row0[0] == "Candidate" and len(df) > 30:
            if last_title is None:
                print(f"WARN: Candidate table without preceding section: table {i}", file=sys.stderr)
                continue
            office, district = normalize_office(re.sub(r"\s+", " ", last_title))
            cands = []
            cparties = []
            for v in [cell_str(v) for v in df.iloc[0, 1:]]:
                if not v:
                    continue
                pm = re.match(r"^(.*\S)\s+(DEM|REP|GRN|LIB|KEY|IND|CON)$", v)
                if pm:
                    cands.append(pm.group(1).strip())
                    cparties.append(pm.group(2))
                else:
                    cands.append(v)
                    cparties.append("")
            labels = [cell_str(v) for v in df.iloc[1, :]]
            wi_col = next((ci for ci, l in enumerate(labels) if l.lower().lstrip() == "write in"), None)
            total_col = next((ci for ci, l in enumerate(labels) if l.strip().lower() == "total"), None)
            n_cand = len(cands)
            # count column positions: candidate columns are 1..n_cand
            data_start = 2
            for _, r in df.iloc[data_start:].iterrows():
                name = cell_str(r[0])
                if not name or name.startswith("Copyright") or name.strip() == "Totals":
                    continue
                counts = [cell_str(r[ci]) for ci in range(1, n_cand + 1)]
                wi = cell_str(r[wi_col]) if wi_col is not None and wi_col < len(r) else ""
                tot = cell_str(r[total_col]) if total_col is not None and total_col < len(r) else ""
                if not any(x.lstrip().lstrip().strip() for x in counts) and not wi and not tot:
                    continue
                def toint(x):
                    x = x.replace(",", "").strip()
                    return int(x) if x.isdigit() else 0
                cv = [toint(x) for x in counts]
                wv = toint(wi)
                tv = toint(tot)
                if sum(cv) + wv != tv:
                    bad += 1
                    print(f"  MISMATCH {name} | {office} {district}: summed {sum(cv)+wv} vs printed {tv}", file=sys.stderr)
                n_precincts.add(name)
                for cand, pv, v in zip(cands, cparties, cv):
                    rows.append({
                        "county": county, "precinct": name,
                        "office": office, "district": district,
                        "party": pv, "candidate": cand, "votes": v,
                        "election_day": "", "mail": "", "provisional": "",
                    })
                rows.append({
                    "county": county, "precinct": name,
                    "office": office, "district": district,
                    "party": "", "candidate": "Write Ins", "votes": wv,
                    "election_day": "", "mail": "", "provisional": "",
                })
    print(f"precincts: {len(n_precincts)}, check mismatches: {bad}", file=sys.stderr)
    return rows


def main():
    if len(sys.argv) != 3:
        sys.exit(f"Usage: {Path(sys.argv[0]).name} <input.html> <output.csv>")
    rows = parse(sys.argv[1])
    with open(sys.argv[2], "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["county", "precinct", "office", "district",
                                           "party", "candidate", "votes",
                                           "election_day", "mail", "provisional"])
        w.writeheader()
        w.writerows(rows)
    print(f"Wrote {len(rows)} rows to {sys.argv[2]}")


if __name__ == "__main__":
    main()