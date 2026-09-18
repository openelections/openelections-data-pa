#!/usr/bin/env python3
"""
Parser for Philadelphia County 2023 General Election precinct results.

Input: the "Major Office Results" workbook exported by the Philadelphia City
Commissioners ("Philadelphia County Major Office Results 2023 General.xlsx").
It contains the complete certified precinct-level results:

  - sheet "Totals":   one row per precinct, one column per candidate
                      (race-candidate totals, plus Registration - Total and
                      Public Count - Total).
  - sheet "By_Type":  the same candidate columns broken out by Vote Method
                      (Election Day / Mail Votes / Provisional), one row per
                      precinct x method, plus per-method Public Count - Total.

NOTE: the companion "Philadelphia County Precinct Results 2023 General.csv"
long-format export is ELECTION-DAY VOTES ONLY (its countywide sums equal the
By_Type "Election Day" rows exactly, e.g. McCaffery 167,979 vs certified
247,042), so it is NOT used as a vote source. The workbook is the only
complete precinct-level source.

Usage:
    python parsers/pa_philadelphia_general_2023_results_parser.py \
        <major_office_xlsx> <output_csv> [--county Philadelphia]

Race/candidate column mapping ('RACE - NAME PARTY' style):
    'JUSTICE OF THE SUPREME COURT - DANIEL MCCAFFERY DEM'  -> office 'Justice of the Supreme Court', party DEM
    'DISTRICT COUNCIL - 10TH DISTRICT - GARY MASINO DEM'   -> office 'District Council', district '10'
    'COURT OF COMMON PLEAS RETENTION - HOLLY J FORD - YES' -> office 'Court of Common Pleas Retention - Holly J Ford', candidate 'Yes'
    'QUESTION #1 - YES'                                    -> office 'Question #1', candidate 'Yes'
    '... - Write-in'                                       -> candidate 'Write-ins', empty party
PartyCode 'NON'-style (nonpartisan) rows get an empty party.
"""

import argparse
import csv
import os
import re
import sys

MINOR_WORDS = {"of", "the", "and", "for"}

FIELDNAMES = [
    "county",
    "precinct",
    "office",
    "district",
    "party",
    "candidate",
    "votes",
    "election_day",
    "mail",
    "provisional",
]

METHODS = {"Election Day": "election_day", "Mail Votes": "mail", "Provisional": "provisional"}


def smart_title(text):
    """Title-case, leaving minor words lowercase (except as first word)."""
    words = text.split()
    out = []
    for i, word in enumerate(words):
        if word.lower() in MINOR_WORDS and i > 0:
            out.append(word.lower())
        else:
            parts = word.lower().split("'")
            out.append("'".join(p[:1].upper() + p[1:] for p in parts))
    return " ".join(out)


def map_column(col_name):
    """Map a candidate column header to (office, district, candidate, party).

    Column names look like 'RACE - CANDIDATE PARTY', 'RACE - CANDIDATE -
    YES/NO' (retentions), 'RACE - Write-in', 'DISTRICT COUNCIL - NTH DISTRICT
    - NAME PARTY' and 'QUESTION #1 - YES/NO'.
    """
    parts = [p.strip() for p in col_name.split(" - ")]
    last = parts[-1]

    if last == "Write-in":
        candidate, party = "Write-ins", ""
        race_parts = parts[:-1]
    elif last in ("YES", "NO") and "RETENTION" in parts[0]:
        # ['COURT OF COMMON PLEAS RETENTION', 'HOLLY J FORD', 'YES']
        office = smart_title(
            re.sub(r"\s*RETENTION\s*$", "", parts[0])
            + " Retention - "
            + parts[-2]
        )
        return office, "", last.title(), ""
    elif re.match(r"^QUESTION\s*#\s*\d+$", parts[0], re.IGNORECASE):
        return "Question #{}".format(parts[0].split("#")[1].strip()), "", last.title(), ""
    else:
        race_parts = parts[:-1]
        party_m = re.search(r"\s(DEM|REP|WFP|WIB)$", last)
        if party_m:
            candidate, party = last[: party_m.start()].strip(), party_m.group(1)
        else:
            candidate, party = last, ""
        race = " - ".join(race_parts)

        m = re.match(r"^DISTRICT COUNCIL\s*-?\s*(\d+)", race, re.IGNORECASE)
        if m:
            return "District Council", m.group(1), candidate, party

        return smart_title(race), "", candidate, party

    # Write-in columns still need their race parsed
    race = " - ".join(race_parts)
    m = re.match(r"^DISTRICT COUNCIL\s*-?\s*(\d+)", race, re.IGNORECASE)
    if m:
        return "District Council", m.group(1), candidate, party
    m = re.match(r"^QUESTION\s*#\s*(\d+)$", race, re.IGNORECASE)
    if m:
        return "Question #{}".format(m.group(1)), "", candidate, party
    if "RETENTION" in race:
        return smart_title(race), "", candidate, party
    return smart_title(race), "", candidate, party


def parse(input_path):
    """Return the standardized rows parsed from the Major Office workbook."""
    import pandas as pd

    def norm(cols):
        return [re.sub(r"\s+", " ", str(c)).strip() for c in cols]

    totals = pd.read_excel(input_path, sheet_name="Totals", header=0)
    totals.columns = norm(totals.columns)
    precinct_col = "PRECINCT NAME"
    cand_cols = list(totals.columns)[9:]

    by_type = pd.read_excel(input_path, sheet_name="By_Type", header=0)
    by_type.columns = norm(by_type.columns)
    methods = by_type[by_type["Vote Method"].isin(METHODS)]

    # per-precinct per-method ballot counts for Ballots Cast breakdowns
    bc = {}
    for _, row in methods.iterrows():
        bc.setdefault(row[precinct_col], {})[METHODS[row["Vote Method"]]] = int(
            row["Public Count - Total"]
        )

    # per-precinct per-candidate votes by method
    votes = {}
    for _, row in methods.iterrows():
        precinct = row[precinct_col]
        for col in cand_cols:
            v = int(row[col])
            if v:
                votes.setdefault(precinct, {}).setdefault(col, {})[
                    METHODS[row["Vote Method"]]
                ] = v

    rows = []
    for precinct in sorted(set(totals[precinct_col]) - {"COUNTY TOTALS"}):
        reg = totals.loc[totals[precinct_col] == precinct].iloc[0]
        rows.append(
            {
                "precinct": precinct,
                "office": "Registered Voters",
                "district": "",
                "party": "",
                "candidate": "",
                "votes": int(reg["Registration - Total"]),
                "election_day": "",
                "mail": "",
                "provisional": "",
            }
        )
        b = bc.get(precinct, {})
        rows.append(
            {
                "precinct": precinct,
                "office": "Ballots Cast",
                "district": "",
                "party": "",
                "candidate": "",
                "votes": int(reg["Public Count - Total"]),
                "election_day": b.get("election_day", ""),
                "mail": b.get("mail", ""),
                "provisional": b.get("provisional", ""),
            }
        )
        for col in cand_cols:
            m = votes.get(precinct, {}).get(col, {})
            total = sum(m.values())
            office, district, candidate, party = map_column(col)
            rows.append(
                {
                    "precinct": precinct,
                    "office": office,
                    "district": district,
                    "party": party,
                    "candidate": candidate,
                    "votes": total,
                    "election_day": m.get("election_day", 0),
                    "mail": m.get("mail", 0),
                    "provisional": m.get("provisional", 0),
                }
            )
    return rows


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("input_path", help="Philadelphia Major Office Results xlsx")
    ap.add_argument("output_path", help="output standardized CSV path")
    ap.add_argument("--county", default="Philadelphia")
    args = ap.parse_args(argv)

    rows = parse(args.input_path)
    for row in rows:
        row["county"] = args.county

    out_dir = os.path.dirname(os.path.abspath(args.output_path))
    os.makedirs(out_dir, exist_ok=True)
    with open(args.output_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)
    print("wrote {} rows to {}".format(len(rows), args.output_path))
    return 0


if __name__ == "__main__":
    sys.exit(main())