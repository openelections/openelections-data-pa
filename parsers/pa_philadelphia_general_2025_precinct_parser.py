#!/usr/bin/env python3
"""
Parser for Philadelphia County, PA 2025 General Election per-division results.

Source: Philadelphia PA 2025 General Division Totals.xlsx ("By Type" sheet --
per Ward/Division x vote method (Election Day / Mail Votes / Provisional)
matrix of contest columns, exported from Philadelphia's results system).

Usage:
    python parsers/pa_philadelphia_general_2025_precinct_parser.py \\
        "<input.xlsx>" "<output.csv>"

Output: precinct-level CSV in the same shape as prior Philadelphia files
(2023/2024): raw ward-division codes ("01-01") as the precinct label,
Registered Voters / Ballots Cast metadata rows first per division, then
contest rows in source-column order. Contest columns not present in this
file (ward-level Judge/Inspector of Election, which have no per-division
breakdown from this export) are intentionally not emitted.
"""

import csv
import re
import sys

import pandas as pd

OFFICE_META_ROWS = ("Registered Voters", "Ballots Cast")


def parse_contest_column(raw_header):
    """Parse one contest column header into (office_display, candidate, party).

    Header shapes (newlines/extra spaces collapsed):
      JUDGE OF THE SUPERIOR COURT - BRANDON NEUMAN DEM   -> (office, name, DEM)
      DISTRICT ATTORNEY - Write-in                       -> (office, "Write-In", "")
      SUPREME COURT RETENTION - CHRISTINE DONOHUE - YES  -> (retention office + judge, Yes, "")
    """
    header = " ".join(str(raw_header).split())
    m = re.match(r"^(.*RETENTION.*) - (.*) - (YES|NO)$", header)
    if m:
        office = f"{m.group(1).title()} - {m.group(2).title()}"
        return (office, m.group(3).title(), "")

    m = re.match(r"^(.+?) - Write-?in$", header, re.IGNORECASE)
    if m:
        return (m.group(1).title(), "Write-In", "")

    m = re.match(r"^(.+?) - (.+?) ([A-Z]{3})$", header)
    if m:
        return (m.group(1).title(), m.group(2), m.group(3))

    m = re.match(r"^(.+?) - (.+)$", header)
    if m:
        return (m.group(1).title(), m.group(2), "")

    return (header.title(), "", "")


def main(input_path, output_path):
    df = pd.read_excel(input_path, sheet_name="By Type", header=None)
    headers = df.iloc[0]
    data = df.iloc[1:].reset_index(drop=True)

    # One spec per contest column index
    specs = {}
    first_col = None
    for idx in range(0, len(headers)):
        raw = headers[idx]
        if pd.isna(raw):
            continue
        label = str(raw)
        if first_col is None and "Vote Method" in label:
            first_col = idx
            continue
        if first_col is None:
            continue
        if idx < first_col + 9:  # 8 non-contest metadata columns after Vote Method
            continue
        specs[idx] = parse_contest_column(raw)

    contest_cols = sorted(specs)

    agg = {}          # (division, col) -> {method: votes}
    divisions = {}    # division -> {method: {"total": int, "registered": int}}
    for _, row in data.iterrows():
        division = str(row[0]).strip()
        if not re.match(r"^\d+-\d+$", division):
            continue
        method = str(row[first_col]).strip()
        total = int(row[2]) if pd.notna(row[2]) else 0
        registered = int(row[3]) if pd.notna(row[3]) else 0
        info = divisions.setdefault(division, {}).setdefault(method, {})
        info["total"] = info.get("total", 0) + total
        info["registered"] = registered
        for idx in contest_cols:
            val = row[idx]
            if pd.isna(val):
                continue
            try:
                votes = int(val)
            except (ValueError, TypeError):
                continue
            agg.setdefault((division, idx), {}).setdefault(method, 0)
            agg[(division, idx)][method] += votes

    out_rows = []

    def meta(division, office, votes, ed=None, mail=None, prov=None):
        out_rows.append({
            "county": "Philadelphia", "precinct": division, "office": office,
            "district": "", "party": "", "candidate": "", "votes": votes,
            "election_day": ed, "mail": mail, "provisional": prov,
        })

    def result(division, spec_order, office, candidate, party, votes, ed, mail, prov):
        out_rows.append({
            "county": "Philadelphia", "precinct": division, "office": office,
            "district": "", "party": party, "candidate": candidate,
            "votes": votes, "election_day": ed, "mail": mail,
            "provisional": prov,
        })

    for division, methods in divisions.items():
        meta(division, "Registered Voters",
             methods.get("Election Day", {}).get("registered",
             methods.get("Mail Votes", {}).get("registered",
             methods.get("Provisional", {}).get("registered", 0))))
        ed = methods.get("Election Day", {}).get("total", 0)
        mail = methods.get("Mail Votes", {}).get("total", 0)
        prov = methods.get("Provisional", {}).get("total", 0)
        meta(division, "Ballots Cast", ed + mail + prov, ed, mail, prov)
        for pos, idx in enumerate(contest_cols):
            mv = agg.get((division, idx), {})
            if not mv:
                continue
            ed_v = mv.get("Election Day", 0)
            mail_v = mv.get("Mail Votes", 0)
            prov_v = mv.get("Provisional", 0)
            votes = ed_v + mail_v + prov_v
            if votes == 0:
                continue
            office, candidate, party = specs[idx]
            result(division, pos, office, candidate, party, votes, ed_v, mail_v, prov_v)

    fieldnames = ["county", "precinct", "office", "district", "party",
                  "candidate", "votes", "election_day", "mail", "provisional"]
    with open(output_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(out_rows)
    print(f"Wrote {len(out_rows)} rows ({len(divisions)} divisions) to {output_path}")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage: python pa_philadelphia_general_2025_precinct_parser.py <input.xlsx> <output.csv>")
        sys.exit(1)
    main(sys.argv[1], sys.argv[2])
