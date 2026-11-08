#!/usr/bin/env python3
"""Parse Westmoreland County 2026 primary election results (Clarity Elections).

2026-11-07 official refresh of the 2026-05-19 general primary. Sources:
  * "Westmoreland County 2026 Primary Precinct Detail XML Official.xml"
      -> precinct-level CSV from the Clarity detail report
  * "Westmoreland County Certification of The Democrat Ballot Results 2026
     General Primary.pdf" / "... Republican Ballot Results ... .pdf"
      -> countywide certification totals used for verification only (a
     pdftotext -layout extract is parsed by work2026p/westmoreland/cert_check.py;
     the county CSV is NOT emitted from them).  No `__county.csv` existed for
     Westmoreland in 2026 and none is produced: the statewide countywide anchor
     2026/20260519__pa__primary__county.csv already carries the county totals.

Unlike the Allegheny 2026 XML (title-cased names), the Westmoreland export
prints OFFICES and CANDIDATES in caps and prefixes every contest with its
ballot party ("DEM GOVERNOR", "REP REPRESENTATIVE IN CONGRESS 14TH
DISTRICT", "REP SENATOR IN THE GENERAL ASSEMBLY 32ND DISTRICT", ...).  The
two countywide turnout contests "BALLOTS CAST - DEMOCRATIC"/"REPUBLICAN"
replace the usual single Ballots Cast row; the earlier (July 2026 vintage)
Westmoreland precinct file carried exactly those party-tagged rows and no
Registered Voters rows.  This refresh keeps the party-tagged Ballots Cast
rows and adds the per-precinct Registered Voters rows from
VoterTurnout/Precincts (empty party, no breakdowns) so the official turnout
data the XML carries is not dropped.

Precinct names are emitted verbatim as the turnout section prints them --
UPPERCASE codes such as "ADAMSBURG BORO", "ARNOLD 1-1", "SCOTTDALE 1ST
WARD" -- matching the existing 20260519 precinct file and the repo's
20260511...general 2024 file naming (the older 2018-2024 primary files use
a different descriptive style; those are separate elections/vintages).

Usage:
    python pa_westmoreland_primary_2026_results_parser.py <detail.xml> <out_precinct.csv> [<out_county.csv>]

Refresh notes:
  * Omitted: the 609 per-precinct "… (DEM|REP)<name> COUNTY COMMITT[EE]"
    contests (one member-of-county-committee contest per voting district --
    out of OpenElections scope, matching the Allegheny 2026 precedent and
    the anchor, which carries none).  Note two source contests carry the
    typo "COUNTY COMMITTE"; the skip pattern covers both spellings.
  * "DEM DEMOCRATIC STATE COMMITTEE" / "REP REPUBLICAN STATE COMMITTEE" -->
    "Member of Democratic State Committee" / "Member of Republican State
    Committee" (anchor/office style, empty district).  Candidates carry
    standalone gender markers ("LISA GEPHART (F)", "JILL COOPER (F)
    (Murrysville)") that are ballot-listing artifacts: standalone "(F)"/"(M)"
    tokens are stripped; disambiguating location parentheticals are kept
    ("JILL COOPER (MURRYSVILLE)", "JILL COOPER (Unity Township)" -- the
    anchor prints "JILL COOPER (UNITY TOWNSHIP)").
  * Write-in choice "Write-in" --> "Write-In Totals" carrying the contest
    party (party-empty write-in rows fail duplicate_entries in primaries).
    The certification PDFs' named per-candidate write-in detail is available
    countywide only (in cert_check output), so no individual write-in names
    go into the precinct file (matches repo 2026 family convention).
  * VoteType mapping: "Election Day" -> election_day, "Provisional" ->
    provisional, "Absentee/Mail" -> absentee (no plain-mail column in the
    precinct schema).
  * Rows are emitted exactly as the XML prints them, including all-zero
    precinct rows.  Within a contest, choices follow XML order and the
    precinct rows under each choice are sorted by precinct name.
  * Office normalization: Governor, Lieutenant Governor, REPRESENTATIVE IN
    CONGRESS nTH DISTRICT -> U.S. House (district n), SENATOR IN THE GENERAL
    ASSEMBLY nTH -> State Senate (n), REPRESENTATIVE IN THE GENERAL ASSEMBLY
    nTH -> State House (n).
  * Every check the parser can make for free: choice totalVotes vs the sum
    of its VoteType/Precinct cells (reported to stderr as SUM MISMATCH), and
    per-precinct BALLOTS CAST DEM+REP vs the turnout ballotsCast (reported
    as BC MISMATCH).
"""

import csv
import re
import sys
from collections import OrderedDict

from lxml import etree

COUNTY = "Westmoreland"

WS_RE = re.compile(r"\s+")

HDR_PRECINCT = ["county", "precinct", "office", "district", "party", "candidate",
                "votes", "election_day", "provisional", "absentee"]
HDR_COUNTY = ["county", "office", "district", "party", "candidate", "votes",
              "election_day", "mail", "provisional"]

VT_MAP = {"Election Day": "election_day", "Absentee/Mail": "absentee",
          "Provisional": "provisional"}

# "REP REPRESENTATIVE IN THE GENERAL ASSEMBLY 57TH DISTRICT" and one
# lowercase "57th DISTRICT" variant exist in the source.
DISTRICT_RE = re.compile(r"\b(\d+)\s*(?:ST|ND|RD|TH)\s+DISTRICT\s*$", re.I)


def clean(text):
    return WS_RE.sub(" ", (text or "")).strip()


def contest_party(text, choice_parties):
    """Party of a contest from its DEM/REP text prefix, falling back to the
    choices' party attributes."""
    m = re.match(r"^(DEM|REP)\s+(.*)$", text)
    if m:
        return m.group(1), m.group(2).strip()
    for p in choice_parties:
        pu = (p or "").strip().upper()
        if pu in ("DEM", "REP"):
            return pu, text.strip()
    return "", text.strip()


def normalize_office(party, office_text):
    """Map the (party-stripped) office title to the OpenElections standard,
    extracting the district where the title carries one.  Returns (office,
    district)."""
    low = office_text.lower()
    m = DISTRICT_RE.search(office_text)
    dist = m.group(1) if m else ""

    if low == "governor":
        return "Governor", ""
    if low == "lieutenant governor":
        return "Lieutenant Governor", ""
    if low.startswith("representative in congress"):
        return "U.S. House", dist
    if low.startswith("senator in the general assembly"):
        return "State Senate", dist
    if low.startswith("representative in the general assembly"):
        return "State House", dist
    if low.startswith("democratic state committee"):
        return "Member of Democratic State Committee", ""
    if low.startswith("republican state committee"):
        return "Member of Republican State Committee", ""
    return office_text, dist


def candidate_name(choice_text):
    t = clean(choice_text)
    if t.lower() == "write-in":
        return "Write-In Totals"
    # strip standalone gender markers: "(F)"/"(M)" tokens anywhere (the
    # export prints e.g. "LISA GEPHART (F)" and "JILL COOPER (F) (Unity)")
    t = re.sub(r"\s*\((?:f|m)\)\s+", " ", t, flags=re.I)
    t = re.sub(r"\s*\((?:f|m)\)$", "", t, flags=re.I)
    t = WS_RE.sub(" ", t).strip()
    # the export prints names in caps; the repo's Westmoreland primary files
    # (2020, 2022, 2024) carry title-cased candidate names
    t = t.title()
    # "(Unity)" is the township name in the anchor countywide file ("JILL
    # COOPER (UNITY TOWNSHIP)"); normalize so the two Jill Coopers compare 1:1
    t = re.sub(r"\(Unity\)$", "(Unity Township)", t)
    return t


def write_csv(output_path, header, rows):
    with open(output_path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)


def parse_detail_xml(xml_path, precinct_out, county_out=None):
    turnout = OrderedDict()  # precinct -> (rv, bc)
    rows = []
    county_rows = []
    problems = []
    contests_parsed = 0

    for event, prec in etree.iterparse(xml_path, events=("end",), tag="Precinct"):
        # only VoterTurnout/Precincts/Precinct elements carry totalVoters;
        # clear only those (contest-internal Precinct elements must keep
        # their attributes until their Contest is processed)
        if prec.getparent().tag == "Precincts":
            turnout[clean(prec.get("name"))] = (
                int(prec.get("totalVoters", "0")),
                int(prec.get("ballotsCast", "0")),
            )
            prec.clear()
            while prec.getprevious() is not None:
                del prec.getparent()[0]

    # Registered Voters rows lead the file (the VoterTurnout section precedes
    # the contests in the export; the earlier vintage file carried no RV rows)
    for pname in turnout:
        rows.append([COUNTY, pname, "Registered Voters", "", "", "",
                     turnout[pname][0], "", "", ""])

    ballots_cast = {}  # precinct -> {(party, col): votes}

    for event, contest in etree.iterparse(xml_path, events=("end",), tag="Contest"):
        ctext = clean(contest.get("text"))

        if re.search(r"COUNTY COMMITT", ctext) or ctext.startswith("REGISTERED VOTERS"):
            contest.clear()
            while contest.getprevious() is not None:
                del contest.getparent()[0]
            continue
        contests_parsed += 1

        if ctext.startswith("BALLOTS CAST - "):
            party = ctext[len("BALLOTS CAST - "):].split()[0]
            party = {"DEMOCRATIC": "DEM", "REPUBLICAN": "REP"}.get(party, party)
            choice = contest.find("Choice")
            for vt in choice.findall("VoteType"):
                col = VT_MAP.get(vt.get("name"))
                if col is None:
                    continue  # regVotersCounty pseudo totals
                for prec in vt.findall("Precinct"):
                    pname = clean(prec.get("name"))
                    ballots_cast.setdefault(pname, {})[(party, col)] = \
                        int(prec.get("votes", "0"))
            # emit in place; the source order puts the two BALLOTS CAST
            # contests first (as in the earlier vintage file), precincts
            # alphabetical
            for pname in sorted(ballots_cast):
                bd = ballots_cast[pname]
                votes = sum(bd.get((party, c), 0)
                            for c in ("election_day", "provisional", "absentee"))
                rows.append([COUNTY, pname, "Ballots Cast", "", party, "", votes,
                             bd.get((party, "election_day"), ""),
                             bd.get((party, "provisional"), ""),
                             bd.get((party, "absentee"), "")])
                if county_out:
                    county_rows.append(
                        [COUNTY, "Ballots Cast", "", party, "", votes,
                         bd.get((party, "election_day"), ""),
                         bd.get((party, "absentee"), ""),
                         bd.get((party, "provisional"), "")])
            contest.clear()
            while contest.getprevious() is not None:
                del contest.getparent()[0]
            continue

        choices = contest.findall("Choice")
        party, office_base = contest_party(ctext, [c.get("party") for c in choices])
        office, dist = normalize_office(party, office_base)

        for choice in choices:
            candidate = candidate_name(choice.get("text"))
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
                county_rows.append([COUNTY, office, dist, party, candidate, total,
                                    vt_totals.get("election_day", ""),
                                    vt_totals.get("absentee", ""),
                                    vt_totals.get("provisional", "")])

            for pname in sorted(per_precinct):
                bd = per_precinct[pname]
                rows.append([COUNTY, pname, office, dist, party, candidate,
                             sum(bd.values()),
                             bd.get("election_day", ""), bd.get("provisional", ""),
                             bd.get("absentee", "")])

        contest.clear()
        while contest.getprevious() is not None:
            del contest.getparent()[0]

    # per-precinct cross-check: BALLOTS CAST DEM + REP per precinct must equal
    # the turnout section's ballotsCast for every precinct (no nonpartisan
    # ballots in the 2026 primary here, so there is nothing else to attribute).
    dem_bc = {p: sum(v.get(("DEM", c), 0)
                     for c in ("election_day", "provisional", "absentee"))
              for p, v in ballots_cast.items()}
    rep_bc = {p: sum(v.get(("REP", c), 0)
                     for c in ("election_day", "provisional", "absentee"))
              for p, v in ballots_cast.items()}
    for pname, (_rv, bc) in turnout.items():
        tot = dem_bc.get(pname, 0) + rep_bc.get(pname, 0)
        if tot != bc:
            problems.append("BC MISMATCH %s: dem+rep=%d turnout ballotsCast=%d"
                            % (pname, tot, bc))

    write_csv(precinct_out, HDR_PRECINCT, rows)
    if county_out:
        write_csv(county_out, HDR_COUNTY, county_rows)

    for p in problems[:20]:
        sys.stderr.write(p + "\n")
    sys.stderr.write("precinct rows=%d contests=%d precincts=%d problems=%d\n"
                     % (len(rows), contests_parsed, len(turnout), len(problems)))
    return len(rows)


def main(argv):
    if len(argv) < 3:
        print(__doc__)
        return 1
    src, out = argv[1], argv[2]
    county_out = argv[3] if len(argv) > 3 else None
    if not src.lower().endswith(".xml"):
        raise SystemExit("2026 refresh expects the Clarity detail XML; the "
                         "certification PDFs are verification-only sources.")
    parse_detail_xml(src, out, county_out)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:] if False else sys.argv))
