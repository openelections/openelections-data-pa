#!/usr/bin/env python3
"""
Parse Bucks County PA 2022 General Primary (May 17, 2022) precinct results.

Source: "Bucks PA 2022GeneralPrimaryCertifiedElectionResultsbyPrecinct with
Recount Totals.pdf" (Dominion "Certified Election Returns by Precinct"
report, 919 pages over 304 precincts; header "General Primary - May 17, 2022",
"Precinct <Name>" blocks, contest headers "<NAME> - D (Dem) (Vote for N)" /
"<NAME> (Rep) (Vote for N)", candidate rows "Name  VOTES  ED  MI  PR").
The report is the post-recount version (GOP U.S. Senate race included with
recount totals); every contest is present for both ballot parties.

Adapted from parsers/pa_bucks_general_2022_results_parser.py (same report
family). Differences from the general:
  - contest headers carry the ballot-party suffix "- D (Dem)" / "- R (Rep)"
    (and "(Dem)"/"(Rep)" alone for the State Committee and committeeperson
    contests), mapped to party DEM/REP;
  - State Committee contests are one countywide list per party:
    "MEMBER OF DEMOCRATIC/REPUBLICAN STATE COMMITTEE" (office names per the
    ENR anchor; no district in the source);
  - per-precinct committeeperson/committeeman contests are local offices,
    kept as printed (title-cased);
  - a contest that allows multiple write-ins prints one "Write-in" row per
    write-in slot: those rows are merged (summed over votes/ED/MI/PR) into a
    single "Write Ins" row per contest/party/precinct so no duplicate rows
    are emitted.

No per-precinct Registered Voters / Ballots Cast rows exist in the source
(only a countywide "Total Ballots Cast: 153488" page header), so none are
emitted. Headers are column 7 + election_day/mail/provisional.

Usage:
    python parsers/pa_bucks_primary_2022_results_parser.py \\
        "<input.pdf>" "<output.csv>"

Text is extracted with pdftotext -layout.
"""

import re
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

PRECRE = re.compile(r"^Precinct\s+(.+)$")
# "UNITED STATES SENATOR - D (Dem) (Vote for 1)" and
# "YARDLEY BOROUGH COMMITTEEPERSON (Dem) (Vote for 2)" and
# "REPUBLICAN STATE COMMITTEE (Rep) (Vote for 17)"
CONTEST_RE = re.compile(r"^(.*?)\s*\((Dem|Rep)\)\s+\(Vote for (\d+)\)\s*$", re.I)
CAND_RE = re.compile(r"^(.+?)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)$")

SENATE_DIST_RE = re.compile(
    r"^SENATOR IN THE GENERAL ASSEMBLY\s+(\d+)(?:st|nd|rd|th)?$", re.I
)
HOUSE_DIST_RE = re.compile(
    r"^REPRESENTATIVE IN THE GENERAL ASSEMBLY\s+(\d+)(?:st|nd|rd|th)?$", re.I
)

SKIP_RE = re.compile(
    r"^(Certified Election Returns by Precinct|General Primary - May 17, 2022"
    r"|All Precincts, |Total Ballots Cast: |Choice\s+Votes|\d{4}-\d{2}-\d{2} "
    r"|\d{2}:\d{2}:\d{2}$)"
)

OFFICE_MAP = {
    "UNITED STATES SENATOR": "U.S. Senate",
    "GOVERNOR": "Governor",
    "LIEUTENANT GOVERNOR": "Lieutenant Governor",
    "REPRESENTATIVE IN CONGRESS": "U.S. House",
    "REPUBLICAN STATE COMMITTEE": "Member of Republican State Committee",
    "DEMOCRATIC STATE COMMITTEE": "Member of Democratic State Committee",
}

PARTY_MAP = {"D": "DEM", "R": "REP", "Dem": "DEM", "Rep": "REP"}


def office_and_district(title):
    """Map a cleaned contest title to (office, district)."""
    title = title.strip()
    upper = re.sub(r"\s+-\s+[DR]$", "", title, flags=re.I).strip()
    m = SENATE_DIST_RE.match(upper)
    if m:
        return "State Senate", m.group(1)
    m = HOUSE_DIST_RE.match(upper)
    if m:
        return "State House", m.group(1)
    if upper in OFFICE_MAP:
        return OFFICE_MAP[upper], ""
    # strip a trailing "- D" / "- R" ballot-party suffix from the plain name
    return upper.title(), ""


def parse(text, county="Bucks"):
    rows = []
    total_rows = []  # for the printed-total internal check
    precinct = None
    office = None
    district = ""
    pending = {}  # (office, district, party, candidate) -> [votes, ed, mi, pr]

    def flush():
        for (o, d, p, c), (v, ed, mi, pr) in pending.items():
            rows.append(
                {
                    "county": county,
                    "precinct": precinct,
                    "office": o,
                    "district": d,
                    "party": p,
                    "candidate": c,
                    "votes": v,
                    "election_day": ed,
                    "mail": mi,
                    "provisional": pr,
                }
            )
        pending.clear()

    for line in text.split("\n"):
        line = line.strip()
        if not line:
            continue
        if SKIP_RE.match(line):
            continue
        m = PRECRE.match(line)
        if m:
            flush()
            precinct, office, district = m.group(1).strip(), None, ""
            continue
        mc = CONTEST_RE.match(line)
        if mc and precinct:
            flush()
            title = mc.group(1).strip()
            # a contest title line may be prefixed by the party letter, e.g.
            # "GOVERNOR - R (Rep) (Vote for 1)"
            office, district = office_and_district(title)
            party = PARTY_MAP[mc.group(2).capitalize()]  # (Dem)/(Rep)
            continue
        if office is None or precinct is None:
            continue
        mx = CAND_RE.match(line)
        if mx:
            cand = mx.group(1).strip()
            vals = [int(mx.group(i)) for i in (2, 3, 4, 5)]
            if cand == "Total":
                flush()
                total_rows.append(
                    (precinct, office, district, party, vals[0], vals[1:] + [0])
                )
                continue
            key = (
                office,
                district,
                party,
                "Write Ins"
                if cand.lower().startswith("write-in")
                else cand,
            )
            cur = pending.get(key)
            if cur is None:
                pending[key] = list(vals)
            else:  # repeated Write-in rows -> merge into one
                for i in range(4):
                    cur[i] += vals[i]
    flush()
    return rows, total_rows


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
    parties = defaultdict(int)
    for r in rows:
        parties[r["party"]] += 1
    print(
        f"Wrote {len(rows)} rows to {out} "
        f"({dict(parties)}, {len({r['precinct'] for r in rows})} precincts)"
    )

    # Internal check: per contest (party contests are distinct), the
    # candidate rows must equal the printed contest "Total".
    contest_agg = defaultdict(int)
    for r in rows:
        contest_agg[
            (r["precinct"], r["office"], r["district"], r["party"])
        ] += int(r["votes"])
    mism = 0
    bad = []
    for (precinct, office, district, party, votes, _edmi) in total_rows:
        if contest_agg.get((precinct, office, district, party), -1) != votes:
            mism += 1
            if len(bad) < 10:
                bad.append(
                    f"  MISMATCH {precinct} | {office} {district} {party}: "
                    f"printed {votes} vs summed {contest_agg.get((precinct, office, district, party))}"
                )
    for b in bad:
        print(b)
    print(f"printed-total check: {len(total_rows)} contest totals, {mism} mismatches")


if __name__ == "__main__":
    import csv

    main()