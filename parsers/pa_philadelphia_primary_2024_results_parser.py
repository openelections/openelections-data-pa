#!/usr/bin/env python3
"""
Parser for Philadelphia County, PA 2024 General Primary per-division results.

Source: "Philadelphia PA 2024 Primary Ward Division Results.xlsx" ("By Type"
sheet -- 3 rows (Election Day / Mail Votes / Provisional) per Ward/Division,
one contest column per candidate, exported from Philadelphia's results
system). Structure mirrors the 2025 General "By Type" export handled by
pa_philadelphia_general_2025_precinct_parser.py.

Usage:
    python parsers/pa_philadelphia_primary_2024_results_parser.py \\
        "<input.xlsx>" "<output.csv>"

Output: precinct-level CSV, raw ward-division codes ("01-01") as the
precinct label, Registered Voters / Ballots Cast metadata rows first per
division, then contest rows in source-column order (only non-zero rows are
emitted). The sheet's trailing "2ND/3RD/5TH CD" and "COUNTY TOTALS"
aggregate rows are not emitted as precinct rows (the CD rows hold
unassigned mail residual votes; their county-level effect is documented in
work2024p/philadelphia/).
"""

import csv
import re
import sys

import pandas as pd

OFFICE_MAP = {
    "PRESIDENT OF THE UNITED STATES": "President",
    "UNITED STATES SENATOR": "U.S. Senate",
    "REPRESENTATIVE IN CONGRESS": "U.S. House",
    "SENATOR IN THE GENERAL ASSEMBLY": "State Senate",
    "REPRESENTATIVE IN THE GENERAL ASSEMBLY": "State House",
    "ATTORNEY GENERAL": "Attorney General",
    "AUDITOR GENERAL": "Auditor General",
    "STATE TREASURER": "State Treasurer",
    "DELEGATE TO THE DEMOCRATIC NATIONAL CONVENTION":
        "Delegate to the Democratic National Convention",
    "DELEGATE TO THE REPUBLICAN NATIONAL CONVENTION":
        "Delegate to the Republican National Convention",
    "ALTERNATE DELEGATE TO THE REPUBLICAN NATIONAL CONVENTION":
        "Alternate Delegate to the Republican National Convention",
    "QUESTION #1": "Question #1",
}

METHODS = ("Election Day", "Mail Votes", "Provisional")
METHOD_TO_COL = {"Election Day": "election_day", "Mail Votes": "mail",
                 "Provisional": "provisional"}


def parse_contest_column(raw_header):
    """Parse one contest column header into (office, district, party, candidate).

    Header shapes (newlines/extra spaces collapsed):
      PRESIDENT OF THE UNITED STATES DEM - JOSEPH R BIDEN JR
        -> (President, "", "DEM", "JOSEPH R BIDEN JR")
      REPRESENTATIVE IN CONGRESS - 2ND DISTRICT DEM - BRENDAN F BOYLE
        -> (U.S. House, "2", "DEM", "BRENDAN F BOYLE")
      QUESTION #1 - YES                    -> (Question #1, "", "", "Yes")
      PRESIDENT OF THE UNITED STATES DEM - Write-in
        -> (President, "", "DEM", "Write-In")
    """
    header = " ".join(str(raw_header).split())

    m = re.match(r"^(.+?) - (YES|NO)$", header, re.IGNORECASE)
    if m:
        return (OFFICE_MAP.get(" ".join(m.group(1).split()).upper(),
                               m.group(1).title()), "", "", m.group(2).title())

    m = re.match(r"^(.+?) - Write-?in$", header, re.IGNORECASE)
    if m:
        office_part = m.group(1)
        office, district, party = _split_office(office_part)
        return (office, district, party, "Write-In")

    left, right = header.rsplit(" - ", 1)
    office, district, party = _split_office(left)
    return (office, district, party, right.strip())


def _split_office(office_part):
    """Strip the ballot-party token and district ordinal from an office string."""
    party = ""
    m = re.search(r"\b(DEM|REP)\s*$", office_part)
    if m:
        party = m.group(1)
        office_part = office_part[:m.start()].rstrip(" -")

    district = ""
    m = re.search(r"(\d{1,3})(?:ST|ND|RD|TH)\s+DISTRICT\b", office_part,
                  re.IGNORECASE)
    if m:
        district = m.group(1)
        office_part = (office_part[:m.start()] + office_part[m.end():])
        office_part = " ".join(office_part.split()).strip(" -")

    office = OFFICE_MAP.get(office_part.strip().upper(), office_part.title())
    return (office, district, party)


def main(input_path, output_path):
    df = pd.read_excel(input_path, sheet_name="By Type", header=None)
    headers = df.iloc[0]

    # Contest columns start after PRECINCT NAME / Vote Method / 4 x
    # (Public Count, Registration, Turnout) x (Total, Dem, Rep, Non) and the
    # 5 per-division district-code columns (Ward, Cong, St Sen, St Rep,
    # Council).
    method_col = first_col = None
    for idx in range(len(headers)):
        if pd.isna(headers[idx]):
            continue
        if method_col is None and "Vote Method" in str(headers[idx]):
            method_col = first_col = idx
            continue
    if method_col is None:
        raise SystemExit("could not locate Vote Method column")

    skipped = 12 + 5  # turnout/registration columns + district-code columns
    specs = {}
    for idx in range(first_col + 1 + skipped, len(headers)):
        if pd.isna(headers[idx]):
            continue
        specs[idx] = parse_contest_column(headers[idx])
    contest_cols = sorted(specs)

    agg = {}          # (division, col) -> {method: votes}
    divisions = {}    # division -> {method: {"total", "registered"}}
    for _, row in df.iloc[1:].iterrows():
        division = str(row[0]).strip()
        if not re.match(r"^\d+-\d+$", division):
            continue
        method = str(row[method_col]).strip()
        # col first_col+1 = "Public Count - Total", +2 = "Registration - Total"
        total = int(row[first_col + 1]) if pd.notna(row[first_col + 1]) else 0
        registered = (int(row[first_col + 2])
                      if pd.notna(row[first_col + 2]) else 0)
        info = divisions.setdefault(division, {}).setdefault(method, {})
        info["total"] = info.get("total", 0) + total
        info["registered"] = registered
        for idx in contest_cols:
            val = row[idx]
            if pd.isna(val):
                continue
            votes = int(val)
            if votes == 0:
                continue
            agg.setdefault((division, idx), {}).setdefault(method, 0)
            agg[(division, idx)][method] += votes

    out_rows = []

    def add(division, office, district, party, candidate, votes,
            ed=None, mail=None, prov=None):
        out_rows.append({
            "county": "Philadelphia", "precinct": division, "office": office,
            "district": district, "party": party, "candidate": candidate,
            "votes": votes, "election_day": ed, "mail": mail,
            "provisional": prov,
        })

    for division, methods in divisions.items():
        registered = next(iter(methods.values()))["registered"]
        breakdown = {}
        for method in METHODS:
            breakdown[METHOD_TO_COL[method]] = methods.get(
                method, {}).get("total", 0)
        add(division, "Registered Voters", "", "", "", registered)
        add(division, "Ballots Cast", "", "", "", sum(breakdown.values()),
            breakdown.get("election_day", 0), breakdown.get("mail", 0),
            breakdown.get("provisional", 0))
        for idx in contest_cols:
            mv = agg.get((division, idx), {})
            if not mv:
                continue
            ed = mv.get("Election Day", 0)
            mail = mv.get("Mail Votes", 0)
            prov = mv.get("Provisional", 0)
            votes = ed + mail + prov
            if votes == 0:
                continue
            office, district, party, candidate = specs[idx]
            add(division, office, district, party, candidate, votes,
                ed, mail, prov)

    fieldnames = ["county", "precinct", "office", "district", "party",
                  "candidate", "votes", "election_day", "mail", "provisional"]
    with open(output_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(out_rows)
    print(f"Wrote {len(out_rows)} rows ({len(divisions)} divisions) "
          f"to {output_path}")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage: python pa_philadelphia_primary_2024_results_parser.py "
              "<input.xlsx> <output.csv>")
        sys.exit(1)
    main(sys.argv[1], sys.argv[2])
