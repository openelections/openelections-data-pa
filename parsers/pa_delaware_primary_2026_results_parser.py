#!/usr/bin/env python3
"""Parse the Delaware County 2026 Primary Official Precinct Results detail.xml.

Source: an ES&S-style "Election result snapshot imported from Tabulation
System" XML (same family the county used for the 2024 "Official Detailed
Results" fixed-width text report, but in XML form):

    <ElectionResult>
      <VoterTurnout totalVoters ballotsCast>
        <Precincts><Precinct name="ALDAN BOR ..." totalVoters="1689"
            ballotsCast="394" ...>   <- per-precinct RV/BC section
      <Contest key text voteFor precinctsReported>
        <VoteType name="Undervotes|Overvotes" votes>            (contest level)
        <Choice text="(nn) Name" party="DEM" totalVotes>
          <VoteType name="Election Day Votes|Mail Voting|Provisional
           Voting" votes>  <Precinct name votes/></VoteType>

Only the party-primary contests the source reports are emitted; the source
has no party-prefixed contest titles, so party comes from each Choice's
`party` attribute and every contest block is reported for both parties in
file order (DEM block first for the partisan offices).

Output (10-column precinct file):

    county,precinct,office,district,party,candidate,votes,
    election_day,mail,provisional

  * "Registered Voters" / "Ballots Cast" rows per precinct from the
    VoterTurnout section (party empty).
  * Every contest section, in source order, including the county's
    "Committee Member, <precinct place>" per-precinct party committee
    contests (office = contest title as printed; one precinct per contest)
    and the two "Member of Democratic/Republican State Committee"
    countywide contests (title "(Vote for ...)/4 Year Term" suffix stripped).
  * Contest titles are normalized to the OpenElections standard
    (Representative in Congress 5th District -> U.S. House, district "5";
    Representative in the General Assembly NNNth -> State House; etc.).
  * Write-ins: each contest's "Unassigned write-ins" choice is emitted per
    precinct as candidate "Write-In Totals" (repo 2026 convention, same as
    the Allegheny/Berks/Bucks/Chester 2026 precinct files), carrying the
    contest party.  The nonpartisan Special Election's write-in choice
    carries NO party attribute -> party stays empty, matching the county's
    existing 2026 file.
  * Undervotes / Overvotes contest-level VoteTypes are skipped (not
    representable in the OpenElections schema).
  * Precinct rows mirror the source exactly: only the precincts each
    contest's VoteTypes list, all of them (zero rows kept, as the county's
    existing 2026 file does).

Refresh note (2026-11-07): replaces the county's earlier (election-night)
delaware precinct CSV, which carried the same numbers but
(a) mislabeled the Mail Voting column as `absentee`
    (order election_day,provisional,absentee),
(b) named write-ins "Unassigned Write-ins" instead of "Write-In Totals",
(c) lacked the Registered Voters / Ballots Cast rows, and
(d) lacked the per-precinct "Committee Member" contests the source reports.

Internal validation (every run):
  * per row: ED + Mail + Prov == votes
  * per contest/party/candidate: summed precinct votes == Choice
    totalVotes attribute
Usage:
    python pa_delaware_primary_2026_results_parser.py detail.xml output.csv
"""

import argparse
import csv
import re
import sys
from collections import OrderedDict

import requests  # noqa: F401  (repo deps assume requests is importable)

try:
    from lxml import etree
except ImportError:  # pragma: no cover
    import xml.etree.ElementTree as etree

COUNTY = "Delaware"
COLS = ["county", "precinct", "office", "district", "party", "candidate",
        "votes", "election_day", "mail", "provisional"]

VT_COL = {
    "Election Day Votes": "election_day",
    "Mail Voting": "mail",
    "Provisional Voting": "provisional",
}

WRITE_IN_TITLE = re.compile(r"^Unassigned write-ins", re.I)
WRITE_IN_NAME = "Write-In Totals"
BALLOT_NUM_PREFIX = re.compile(r"^\(\d+\)\s*")

STATE_SENATE_RX = re.compile(r"^Senator in the(?:\s+the)? General Assembly\s+"
                             r"(\d+)\w*\s+District")
STATE_HOUSE_RX = re.compile(r"^Representative in the\s+General Assembly\s+"
                            r"(\d+)\w*\s+District")
US_HOUSE_RX = re.compile(r"^Representative in Congress\s+(\d+)\w*\s+District")
STATE_COMMITTEE_RX = re.compile(
    r"^Member of (Democratic|Republican) State Committee")


def clean(text):
    return re.sub(r"\s+", " ", (text or "")).strip()


def map_office(title):
    """Source contest title -> (office, district)."""
    m = STATE_COMMITTEE_RX.match(title)
    if m:
        return "Member of %s State Committee" % m.group(1), ""
    m = US_HOUSE_RX.match(title)
    if m:
        return "U.S. House", m.group(1)
    m = STATE_SENATE_RX.match(title)
    if m:
        return "State Senate", m.group(1)
    m = STATE_HOUSE_RX.match(title)
    if m:
        return "State House", m.group(1)
    fixed = {
        "Governor": ("Governor", ""),
        "Lieutenant Governor": ("Lieutenant Governor", ""),
    }
    if title in fixed:
        return fixed[title]
    # "Committee Member, <place>" and the Special Election Middletown
    # Township Council - District 3: office as printed.
    return title, ""


def parse(input_path, county=COUNTY):
    rows = []
    problems = []

    turnout = OrderedDict()  # precinct -> (rv, bc)
    for event, prec in etree.iterparse(input_path, events=("end",),
                                       tag="Precinct"):
        if prec.getparent() is not None and \
                prec.getparent().tag == "Precincts":
            pname = clean(prec.get("name"))
            turnout[pname] = (int(prec.get("totalVoters", "0")),
                              int(prec.get("ballotsCast", "0")))
        prec.clear()

    for event, contest in etree.iterparse(input_path, events=("end",),
                                          tag="Contest"):
        title = clean(contest.get("text"))
        office, district = map_office(title)

        for choice in contest.findall("Choice"):
            ctext = clean(choice.get("text"))
            party = (choice.get("party") or "").strip().upper()
            if WRITE_IN_TITLE.match(ctext):
                candidate = WRITE_IN_NAME
            else:
                candidate = BALLOT_NUM_PREFIX.sub("", ctext).strip()

            per = {}
            for vt in choice.findall("VoteType"):
                col = VT_COL.get(vt.get("name"))
                if col is None:
                    continue
                for prec_el in vt.findall("Precinct"):
                    pname = clean(prec_el.get("name"))
                    if col in per.get(pname, {}):
                        problems.append(
                            "contest %r choice %r: repeat %r in %s"
                            % (title, ctext, pname, col))
                    per.setdefault(pname, {})[col] = int(
                        prec_el.get("votes", "0"))

            # internal cross-check: summed precinct votes == totalVotes attr
            want = int(choice.get("totalVotes", "0"))
            sums = {}
            for pname, bd in per.items():
                for col2, v in bd.items():
                    sums[col2] = sums.get(col2, 0) + v
            if sum(sums.values()) != want:
                problems.append(
                    "contest %r choice %r (%s): precinct sum %d != "
                    "totalVotes %d" % (title, ctext, party, sum(sums.values()),
                                       want))

            for pname, bd in per.items():
                ed = bd.get("election_day", 0)
                mail = bd.get("mail", 0)
                prov = bd.get("provisional", 0)
                rows.append([county, pname, office, district, party,
                             candidate, ed + mail + prov, ed, mail, prov])

        contest.clear()

    for pname, (rv, bc) in turnout.items():
        rows.append([county, pname, "Registered Voters", "", "", "", rv,
                     "", "", ""])
        rows.append([county, pname, "Ballots Cast", "", "", "", bc,
                     "", "", ""])

    return rows, problems


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("input")
    ap.add_argument("output")
    ap.add_argument("--county", default=COUNTY)
    args = ap.parse_args(argv)

    rows, problems = parse(args.input, args.county)
    with open(args.output, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(COLS)
        w.writerows(rows)

    n_prec = len({r[1] for r in rows})
    print("wrote %d rows (%d precincts) -> %s"
          % (len(rows), n_prec, args.output))
    if problems:
        print("VALIDATION PROBLEMS (%d):" % len(problems), file=sys.stderr)
        for p in problems[:40]:
            print("  " + p, file=sys.stderr)
        return 1
    print("all internal checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
