#!/usr/bin/env python3
"""Parse Allegheny County 2024 primary election results (Clarity Elections).

Inputs (detected by extension, matching the real sources):
  * "Allegheny PA 2024 Primary Summary Results.csv"
      -> county-level CSV (countywide totals, no ED/mail/provisional split)
  * "Allegheny PA 2024 Primary Precinct Results.zip" / detail.xml
      -> precinct-level CSV from the Clarity detail report

Outputs (OpenElections format):
  county-level: county,office,district,party,candidate,votes,election_day,mail,provisional
  precinct:     county,precinct,office,district,party,candidate,votes,election_day,mail,provisional

Primary-specific handling:
  * Contest text carries the party as a prefix ("DEM President of the United
    States" / "REP ..."); the prefix is stripped into the party column (DEM/REP)
    on every row of the contest, including Write-ins rows (party-empty write-in
    rows would fail the repo's duplicate_entries test in primaries).
  * Office names are normalized to the OpenElections standard and district
    numbers are parsed out of the contest text into the `district` column:
      "President of the United States"                        -> President
      "United States Senator"                                 -> U.S. Senate
      "Representative in Congress District N"                 -> U.S. House
      "Senator in the General Assembly District N"            -> State Senate
      "Representative in the General Assembly District N"     -> State House
      "Delegate to the National Conventions District N"       -> party-specific
        (DEM: "Delegate to the Democratic National Convention",
         REP: "Delegate to the Republican National Convention")
      "Alternate Delegate to the National Conventions District N"
        (REP only in 2024)                                    -> "Alternate Delegate
        to the Republican National Convention"
  * "Write-in" choices aggregate to a single "Write-ins" row per contest,
    carrying the contest party.
  * The "REGISTERED VOTERS - Nonpartisan" (Vote For 0) contest and the
    contest-level regVotersCounty VoteType are skipped; Registered Voters /
    Ballots Cast rows are taken from the VoterTurnout/Precincts section.

Usage:
    python pa_allegheny_primary_2024_results_parser.py <input.csv|.zip|.xml> <output.csv>
"""

import csv
import os
import re
import sys
import tempfile
import zipfile
from collections import OrderedDict

from lxml import etree

COUNTY = "Allegheny"

WS = re.compile(r"\s+")


def clean(text):
    return WS.sub(" ", (text or "")).strip()


def strip_vote_for(text):
    return re.sub(r"\s*\(Vote For \d+\)\s*$", "", text).strip()


def trailing_district(text):
    """Return (base_text, district) for '... District 12' style titles."""
    m = re.match(r"^(.*?)\s+District\s+(\d+)\s*$", text)
    if m:
        return m.group(1).strip(), m.group(2)
    return text.strip(), ""


def contest_party(text, choice_parties):
    """Party of a contest from its text prefix, falling back to choice party
    attributes. Nonpartisan (NON) contests get an empty party."""
    m = re.match(r"^(DEM|REP)\s+(.*)$", text)
    if m:
        return m.group(1), m.group(2).strip()
    for p in choice_parties:
        pu = (p or "").strip().upper()
        if pu and pu != "NON":
            return pu, text.strip()
    return "", text.strip()


def normalize_office(party, office_text):
    """Map the source office title to the OpenElections standard, extracting
    the district where the title carries one. Returns (office, district)."""
    base, dist = trailing_district(office_text)

    if base == "President of the United States":
        return "President", ""
    if base == "United States Senator":
        return "U.S. Senate", ""
    if base == "Representative in Congress":
        return "U.S. House", dist
    if base == "Senator in the General Assembly":
        return "State Senate", dist
    if base == "Representative in the General Assembly":
        return "State House", dist
    if base == "Delegate to the National Conventions":
        if party == "DEM":
            return "Delegate to the Democratic National Convention", dist
        return "Delegate to the Republican National Convention", dist
    if base == "Alternate Delegate to the National Conventions":
        return "Alternate Delegate to the Republican National Convention", dist
    return office_text, dist


class OfficeNamer:
    """Disambiguates repeated contests for the same office with a " (2)" suffix
    (matching the general parser's convention), keyed on (party, office,
    district). Districted contests with the same office but different district
    numbers are distinct contests and keep the same office text."""

    def __init__(self):
        self.used = {}

    def name(self, party, office, district=""):
        key = (party, office, district)
        if key in self.used:
            self.used[key] += 1
            return "%s (%d)" % (office, self.used[key])
        self.used[key] = 1
        return office


def write_csv(output_path, header, rows):
    with open(output_path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)


# ---------------------------------------------------------------------------
# County-level: Official Summary CSV
# ---------------------------------------------------------------------------

def parse_summary_csv(input_path, output_path):
    import csv as _csv

    with open(input_path, newline="", encoding="utf-8-sig") as f:
        reader = _csv.reader(f)
        header = next(reader)
        idx = {name: i for i, name in enumerate(header)}
        rows = list(reader)

    ci = idx["contest_name"]
    chi = idx["choice_name"]
    pi = idx["party_name"]
    ti = idx["total_votes"]
    rvi = idx.get("registered_voters")
    bci = idx.get("ballots_cast")
    pvi = idx.get("percent_of_votes")

    # Contests repeat contest_name across rows; consecutive rows of one contest
    # are in non-increasing percent order. A run breaks when the name changes,
    # a choice name repeats within the run, percent increases, or a named
    # candidate follows a Write-in choice that had 100%.
    contests = []  # (contest_name, [row choice names], [row total votes])
    problems = []

    current_name = None
    current_choices = []
    current_votes = []
    seen_choices = set()
    prev_pct = -1.0
    prev_choice = None
    for r in rows:
        cname = clean(r[ci])
        if cname.startswith("REGISTERED VOTERS"):
            continue
        ch = clean(r[chi])
        try:
            pct = float(r[pvi]) if pvi is not None else 0.0
        except (TypeError, ValueError):
            pct = 0.0
        if (cname != current_name
                or ch in seen_choices
                or pct > prev_pct
                or (prev_choice is not None and prev_choice.lower() == "write-in"
                    and prev_pct == 100.0 and ch.lower() != "write-in")):
            if current_name is not None:
                contests.append((current_name, current_choices, current_votes))
            current_name = cname
            current_choices = []
            current_votes = []
            seen_choices = set()
        prev_pct = pct
        prev_choice = ch
        seen_choices.add(ch)
        current_choices.append(ch)
        try:
            current_votes.append(int(float(r[ti])))
        except (TypeError, ValueError):
            current_votes.append(0)
    if current_name is not None:
        contests.append((current_name, current_choices, current_votes))

    rows_out = []
    namer = OfficeNamer()
    contests_parsed = 0
    for office_text, cchoices, cvotes in contests:
        party, office_base = contest_party(strip_vote_for(office_text), [])
        office, dist = normalize_office(party, office_base)
        office = namer.name(party, office, dist)
        contests_parsed += 1
        for ch, tv in zip(cchoices, cvotes):
            candidate = "Write-ins" if ch.lower() == "write-in" else ch
            rows_out.append([COUNTY, office, dist, party, candidate, tv, "", "", ""])

    # Global registered voters / ballots cast: the summary's registered_voters
    # column is unreliable on most rows, but the county-wide RV/BC pair
    # (905,308 / 245,198 in 2024) appears on the rows of the first contest.
    # Take the largest registered_voters value seen as the county RV, and the
    # largest ballots_cast as county BC.
    rv = bc = None
    for r in rows:
        try:
            t_rv = int(r[rvi])
            t_bc = int(r[bci])
        except (TypeError, ValueError, IndexError):
            continue
        if rv is None or t_rv > rv:
            rv = t_rv
        if bc is None or t_bc > bc:
            bc = t_bc
    if rv:
        rows_out.insert(0, [COUNTY, "Ballots Cast", "", "", "", bc, "", "", ""])
        rows_out.insert(0, [COUNTY, "Registered Voters", "", "", "", rv, "", "", ""])

    header_out = ["county", "office", "district", "party", "candidate", "votes",
                  "election_day", "mail", "provisional"]
    write_csv(output_path, header_out, rows_out)
    sys.stderr.write("county rows=%d contests=%d\n" % (len(rows_out), contests_parsed))
    return len(rows_out)


# ---------------------------------------------------------------------------
# Precinct-level: Clarity detail.xml
# ---------------------------------------------------------------------------

def parse_detail_xml(xml_path, output_path):
    turnout = OrderedDict()  # precinct -> (rv, bc)
    rows = []
    problems = []
    namer = OfficeNamer()
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

        if ctext.startswith("REGISTERED VOTERS"):
            contest.clear()
            while contest.getprevious() is not None:
                del contest.getparent()[0]
            continue

        choices = contest.findall("Choice")
        contests_parsed += 1
        party, office_base = contest_party(
            ctext, [c.get("party") for c in choices])
        office_base, dist = normalize_office(party, office_base)
        office = namer.name(party, office_base, dist)

        for choice in choices:
            cname = clean(choice.get("text"))
            if cname.lower() == "write-in":
                candidate = "Write-ins"
            else:
                candidate = cname

            per_precinct = {}
            for vt in choice.findall("VoteType"):
                name = vt.get("name")
                if name == "Election Day":
                    col = "election_day"
                elif name == "Absentee":
                    col = "mail"  # Clarity combines absentee + mail ballots
                elif name == "Provisional":
                    col = "provisional"
                else:
                    continue
                for prec in vt.findall("Precinct"):
                    pname = clean(prec.get("name"))
                    per_precinct.setdefault(pname, {})[col] = int(prec.get("votes", "0"))

            vt_totals = sum(
                int(vt.get("votes", "0"))
                for vt in choice.findall("VoteType")
                if vt.get("name") != "regVotersCounty"
            )
            ctot = int(choice.get("totalVotes", "0"))
            if ctot and vt_totals != ctot:
                problems.append("SUM MISMATCH %s / %s: votetypes=%d totalVotes=%d"
                                % (ctext, candidate, vt_totals, ctot))

            for pname, bd in per_precinct.items():
                votes = sum(bd.values())
                if votes == 0:
                    continue  # omit all-zero precinct rows to keep file size sane
                rows.append([
                    COUNTY, pname, office, dist, party, candidate, votes,
                    bd.get("election_day", ""), bd.get("mail", ""), bd.get("provisional", ""),
                ])

        contest.clear()
        while contest.getprevious() is not None:
            del contest.getparent()[0]

    for pname, (rv, bc) in turnout.items():
        rows.append([COUNTY, pname, "Registered Voters", "", "", "", rv, "", "", ""])
        if bc:
            rows.append([COUNTY, pname, "Ballots Cast", "", "", "", bc, "", "", ""])

    header = ["county", "precinct", "office", "district", "party", "candidate",
              "votes", "election_day", "mail", "provisional"]
    write_csv(output_path, header, rows)

    for p in problems[:20]:
        sys.stderr.write(p + "\n")
    sys.stderr.write("precinct rows=%d contests=%d precincts=%d problems=%d\n"
                     % (len(rows), contests_parsed, len(turnout), len(problems)))
    return len(rows)


def parse(input_path, output_path):
    lower = input_path.lower()
    if lower.endswith(".csv"):
        return parse_summary_csv(input_path, output_path)
    tmpdir = None
    if lower.endswith(".zip"):
        xml_path, tmpdir = extract_zip(input_path)
    else:
        xml_path = input_path
    try:
        return parse_detail_xml(xml_path, output_path)
    finally:
        if tmpdir:
            import shutil
            shutil.rmtree(tmpdir, ignore_errors=True)


def extract_zip(input_path):
    tmpdir = tempfile.mkdtemp(prefix="allegheny_clarity_")
    with zipfile.ZipFile(input_path) as zf:
        names = [n for n in zf.namelist() if n.lower().endswith(".xml")]
        if not names:
            raise SystemExit("No XML file found in %s" % input_path)
        zf.extract(names[0], tmpdir)
        xml_path = os.path.join(tmpdir, names[0])
    return xml_path, tmpdir


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)
    parse(sys.argv[1], sys.argv[2])
