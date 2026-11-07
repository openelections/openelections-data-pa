#!/usr/bin/env python3
"""Parse Lehigh County PA 2024 General Primary precinct results CSV.

Source: "Lehigh PA 2024 Primary Precinct Results.csv" — Lehigh's official
Electionware "Precinct Results" CSV export (same vendor family as
Northampton's 2024 primary export and the Lehigh 2026 primary CSVs).
A header preamble ("Official Election Results" / "LEHIGH") precedes the
column header:

    Precinct,Contest Name,Candidate Name,Votes,Voter Turnout

Only a single Votes column exists (no election-day/mail/provisional
breakdown, no Registered Voters / Ballots Cast rows — Voter Turnout is a
percentage only), so output uses the 7-column schema and no RV/BC rows
are emitted (none printed; none invented).

Contest names are truncated at 50 characters in this export, so
normalization matches on truncation-safe prefixes and regexes:
  * "DEM "/ "REP " prefix on party-primary contests; ballot questions have
    no prefix and no party (Yes/No candidates carry party "").
  * "Representative in Congress in the Nth Congressional[Dist?]..." →
    "U.S. House" with the district number.
  * "Representative in the General Assembly NNNth Represe..." →
    "State House" with the district number.
  * "Delegate to the (Democratic|Republican) National Convention ... and
    "Alternate Delegate to the Republican National Convention ..." —
    Lehigh sits entirely in the 7th Congressional District (the delegate
    slates match Northampton's CD-7 lists), so district "7".
Ballot questions: office as printed (e.g. "Heidelberg Township
Referendum Question"), candidate Yes/No.

Write-ins: the export prints one aggregate "Write-in" row per precinct/
contest (a totals-style single row), so the candidate is rendered
"Write-In Totals", matching Northampton's 2024 primary handling.

Usage:
    python parsers/pa_lehigh_primary_2024_results_parser.py \
        "<input.csv>" "<output.csv>"
"""

import csv
import re
import sys
import warnings
from collections import Counter, defaultdict

FIELDNAMES = [
    "county", "precinct", "office", "district", "party", "candidate",
    "votes",
]

OFFICE_EXACT = {
    "President of the United States": "President",
    "United States Senator": "U.S. Senate",
    "Attorney General": "Attorney General",
    "Auditor General": "Auditor General",
    "State Treasurer": "State Treasurer",
}

# Truncation-safe regexes for districted offices (names clipped at 50
# chars, so suffixes like "District"/"Repre" are unreliable).
DELEGATE_RE = re.compile(
    r"^Delegate to the (Democratic|Republican) National", re.I)
ALT_DELEGATE_RE = re.compile(
    # Truncated to 50 chars ("... National Conven") in this export.
    r"^Alternate Delegate to the Republican National", re.I)
CONGRESS_RE = re.compile(
    r"^Representative in Congress in the (\d+)th Congressional", re.I)
STATE_HOUSE_RE = re.compile(
    r"^Representative in the General Assembly (\d+)", re.I)


def normalize_contest(contest):
    """Map a (possibly truncated) contest name to (office, district, party)."""
    party = ""
    rest = contest
    pm = re.match(r"^(DEM|REP)\s+(.*)$", rest)
    if pm:
        party = pm.group(1)
        rest = pm.group(2)
    rest = " ".join(rest.split())

    for key, office in OFFICE_EXACT.items():
        if rest == key:
            return office, "", party
    m = CONGRESS_RE.match(rest)
    if m:
        return "U.S. House", m.group(1), party
    m = STATE_HOUSE_RE.match(rest)
    if m:
        return "State House", m.group(1), party
    m = ALT_DELEGATE_RE.match(rest)
    if m:
        return ("Alternate Delegate to the Republican National"
                " Convention"), "7", party
    m = DELEGATE_RE.match(rest)
    if m:
        return (
            "Delegate to the %s National Convention" % m.group(1).title(),
            "7", party,
        )
    # Ballot questions: no party prefix, no party on rows.
    if "REFERENDUM" in rest.upper() or "QUESTION" in rest.upper():
        return rest, "", ""
    return ("", "", "")


def tidy_name(name):
    return " ".join(name.split())


def parse(path, county="Lehigh"):
    with open(path, newline="", encoding="utf-8-sig") as fh:
        reader = csv.reader(fh)
        lines = [r for r in reader if r]
    # Strip the preamble: everything up to and including the header row.
    start = None
    for i, row in enumerate(lines):
        if row and row[0].strip() == "Precinct":
            start = i + 1
            break
    if start is None:
        raise ValueError("Precinct,Contest Name,... header row not found")

    rows = []
    totals = defaultdict(int)          # (office, district, party, candidate)
    contest_rows = defaultdict(set)    # contest key -> precincts seen
    skipped = Counter()
    for r in lines[start:]:
        if len(r) != 5:
            skipped["columns!=5"] += 1
            continue
        precinct, contest, cand, votes, _turnout = (
            s.strip() for s in r[:5])
        if not votes.isdigit():
            skipped["votes not numeric"] += 1
            continue
        office, district, party = normalize_contest(contest)
        if not office:
            warnings.warn("unmapped contest: " + contest)
            continue
        candidate = tidy_name(cand)
        if candidate.lower().startswith("write-in"):
            candidate = "Write-In Totals"
        votes = int(votes)
        rows.append({
            "county": county,
            "precinct": precinct,
            "office": office,
            "district": district,
            "party": party,
            "candidate": candidate,
            "votes": votes,
        })
        totals[(office, district, party, candidate)] += votes
        contest_rows[(office, district)].add(precinct)
    if skipped:
        print("Skipped rows: " + repr(dict(skipped)), file=sys.stderr)
    return rows, dict(totals), contest_rows


def main():
    if len(sys.argv) != 3:
        sys.exit(
            "Usage: %s <input.csv> <output.csv>" % sys.argv[0])
    rows, totals, contest_rows = parse(sys.argv[1])
    with open(sys.argv[2], "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDNAMES)
        w.writeheader()
        w.writerows(rows)
    precincts = {r["precinct"] for r in rows}
    print("Wrote %d rows to %s" % (len(rows), sys.argv[2]))
    print("Distinct precincts: %d; contests: %d"
          % (len(precincts), len(contest_rows)))
    print("Per-contest totals:")
    for key in sorted(totals):
        office, district, party, cand = key
        print("  %-55s dist=%-3s %s %-20s %6d"
              % (office, district, party, cand, totals[key]))
    # Contests that appear in only some precincts (districted / questions).
    for key, ps in sorted(contest_rows.items()):
        if len(ps) != len(precincts):
            print("  partial contest %s dist %s: %d precincts"
                  % (key[0], key[1], len(ps)))


if __name__ == "__main__":
    main()
