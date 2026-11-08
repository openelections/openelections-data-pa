#!/usr/bin/env python3
"""Parse Allegheny County 2026 primary election results (Clarity Elections).

2026-11-07 official refresh of the 2026-05-19 general primary. Sources:
  * "Allegheny County 2026 Primary Precinct Detail XML Official.xml"
      -> precinct-level CSV from the Clarity detail report
  * "Allegheny County 2026 Primary Summary Results Official.csv"
      -> countywide totals used for verification only (the county CSV is
         derived from the same detail.xml aggregates, so county and precinct
         totals always agree).

Usage:
    python pa_allegheny_primary_2026_results_parser.py <detail.xml> <out_precinct.csv> [<out_county.csv>]

Refresh notes (vs the earlier unofficial-vintage precinct file):
  * Breakdown column order follows the 2026 precinct-file family and the
    existing Allegheny file: election_day,provisional,absentee. The Clarity
    "Absentee" vote type is written to the `absentee` column (no separate
    mail column in this schema).
  * Rows are emitted exactly as the XML prints them, including all-zero
    precinct rows (matches the existing Allegheny file convention).
  * Write-in choices aggregate per contest to a single "Write-In Totals" row
    carrying the contest party (party-empty write-in rows fail the repo's
    duplicate_entries test in primaries).
  * State Committee contests ("DEM/REP Member of State Committee Nth
    District") use the office names used by the anchor countywide file and
    the earlier Allegheny file: "Member of Democratic State Committee" /
    "Member of Republican State Committee", with an empty district column.
  * "DEM/REP County Committee ... Member <precinct>" contests are omitted:
    per-precinct party committee positions, out of OpenElections scope (the
    anchor carries none; the earlier Allegheny file has none; several other
    2026 counties also omit them).
  * "Proposed Amendment Pittsburgh" is a nonpartisan ballot question: rows
    carry party "" with candidates Yes / No, as the source reports.
  * Registered Voters / Ballots Cast rows come from VoterTurnout/Precincts;
    the precinct file carries both with empty breakdowns (the county file
    carries neither, matching other 2026 county files).
"""

import csv
import re
import sys
from collections import OrderedDict

from lxml import etree

COUNTY = "Allegheny"

WS_RE = re.compile(r"\s+")

HDR_PRECINCT = ["county", "precinct", "office", "district", "party", "candidate",
                "votes", "election_day", "provisional", "absentee"]
HDR_COUNTY = ["county", "office", "district", "party", "candidate", "votes",
              "election_day", "mail", "provisional"]

VT_MAP = {"Election Day": "election_day", "Absentee": "absentee", "Provisional": "provisional"}


def clean(text):
    return WS_RE.sub(" ", (text or "")).strip()


def trailing_district(text):
    """Return (base_text, district) for '... Nth District' style titles."""
    m = re.match(r"^(.*?)\s+(\d+)(?:st|nd|rd|th)\s+District\s*$", text, re.I)
    if m:
        return m.group(1).strip(), m.group(2)
    return text.strip(), ""


def contest_party(text, choice_parties):
    """Party of a contest from its text prefix, falling back to choice party
    attributes. Nonpartisan contests get an empty party."""
    m = re.match(r"^(DEM|REP)\s+(.*)$", text)
    if m:
        return m.group(1), m.group(2).strip()
    for p in choice_parties:
        pu = (p or "").strip().upper()
        if pu in ("DEM", "REP"):
            return pu, text.strip()
    return "", text.strip()


def normalize_office(party, office_text):
    """Map the source office title to the OpenElections standard, extracting
    the district where the title carries one. Returns (office, district)."""
    base, dist = trailing_district(office_text)
    low = base.lower()

    if base == "Governor":
        return "Governor", ""
    if base == "Lieutenant Governor":
        return "Lieutenant Governor", ""
    if low == "representative in congress":
        return "U.S. House", dist
    if low == "senator in the general assembly":
        return "State Senate", dist
    if low == "representative in the general assembly":
        return "State House", dist
    if low == "member of state committee":
        # party-embedded office names, no district (matches the anchor
        # countywide file and the prior Allegheny precinct file)
        if party == "DEM":
            return "Member of Democratic State Committee", ""
        return "Member of Republican State Committee", ""
    return office_text, dist


def candidate_name(choice_text):
    return "Write-In Totals" if choice_text.lower() == "write-in" else choice_text


def write_csv(output_path, header, rows):
    with open(output_path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)


def parse_detail_xml(xml_path, precinct_out, county_out):
    turnout = OrderedDict()  # precinct -> (rv, bc)
    rows = []
    county_rows = []
    problems = []
    contests_parsed = 0

    for event, prec in etree.iterparse(xml_path, events=("end",), tag="Precinct"):
        # only VoterTurnout/Precincts/Precinct elements carry totalVoters;
        # clear only those (contest-internal Precinct elements must keep their
        # attributes until their Contest is processed)
        if prec.getparent().tag == "Precincts":
            turnout[clean(prec.get("name"))] = (
                int(prec.get("totalVoters", "0")),
                int(prec.get("ballotsCast", "0")),
            )
            prec.clear()
            while prec.getprevious() is not None:
                del prec.getparent()[0]

    for event, contest in etree.iterparse(xml_path, events=("end",), tag="Contest"):
        ctext = clean(contest.get("text"))

        if ctext.startswith("REGISTERED VOTERS") or re.match(r"^(DEM|REP) County Committee", ctext):
            contest.clear()
            while contest.getprevious() is not None:
                del contest.getparent()[0]
            continue
        contests_parsed += 1

        choices = contest.findall("Choice")
        q = (contest.get("isQuestion") or "").strip().lower() == "true"
        party, office_base = contest_party(ctext, [c.get("party") for c in choices])
        office, dist = normalize_office(party, office_base)
        choice_party = "" if q else party

        for choice in choices:
            candidate = candidate_name(clean(choice.get("text")))
            per_precinct = {}
            vt_totals = {}
            for vt in choice.findall("VoteType"):
                col = VT_MAP.get(vt.get("name"))
                if col is None:
                    continue  # regVotersCounty pseudo totals
                vt_totals[col] = vt_totals.get(col, 0) + int(vt.get("votes", "0"))
                for prec in vt.findall("Precinct"):
                    pname = clean(prec.get("name"))
                    per_precinct.setdefault(pname, {})[col] = int(prec.get("votes", "0"))

            total = int(choice.get("totalVotes", "0"))
            vt_sum = sum(vt_totals.values())
            if total != vt_sum:
                problems.append("SUM MISMATCH %s / %s: votetypes=%d totalVotes=%d"
                                % (ctext, candidate, vt_sum, total))

            if county_out:
                county_rows.append([COUNTY, office, dist, choice_party, candidate,
                                    total,
                                    vt_totals.get("election_day", ""),
                                    vt_totals.get("absentee", ""),
                                    vt_totals.get("provisional", "")])

            for pname in sorted(per_precinct):
                bd = per_precinct[pname]
                rows.append([COUNTY, pname, office, dist, choice_party, candidate,
                             sum(bd.values()),
                             bd.get("election_day", ""), bd.get("provisional", ""),
                             bd.get("absentee", "")])

        contest.clear()
        while contest.getprevious() is not None:
            del contest.getparent()[0]

    for pname, (rv, bc) in turnout.items():
        rows.append([COUNTY, pname, "Registered Voters", "", "", "", rv, "", "", ""])
        rows.append([COUNTY, pname, "Ballots Cast", "", "", "", bc, "", "", ""])

    write_csv(precinct_out, HDR_PRECINCT, rows)
    if county_out:
        write_csv(county_out, HDR_COUNTY, county_rows)

    for p in problems[:20]:
        sys.stderr.write(p + "\n")
    sys.stderr.write("precinct rows=%d contests=%d precincts=%d problems=%d county_rows=%d\n"
                     % (len(rows), contests_parsed, len(turnout), len(problems),
                        len(county_rows) if county_out else 0))
    return len(rows)


def main(argv):
    if len(argv) < 3:
        print(__doc__)
        return 1
    src, out = argv[1], argv[2]
    county_out = argv[3] if len(argv) > 3 else None
    if not src.lower().endswith(".xml"):
        raise SystemExit("2026 refresh expects the Clarity detail XML. For the summary CSV use it for "
                         "verification only (no precinct data).")
    parse_detail_xml(src, out, county_out)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:] if False else sys.argv))
