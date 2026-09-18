#!/usr/bin/env python3
"""Parse Allegheny County 2023 primary election results (Clarity Elections).

Inputs (detected by extension, matching the real sources):
  * "Allegheny County Official Summary Results 2023 Primary.csv"
      -> county-level CSV (countywide totals, no ED/mail/provisional split)
  * "Allegheny Clarity Data.zip" / detail.xml
      -> precinct-level CSV from the Clarity detail report

Outputs (OpenElections format):
  county-level: county,office,district,party,candidate,votes,election_day,mail,provisional
  precinct:     county,precinct,office,district,party,candidate,votes,election_day,mail,provisional

Primary-specific handling:
  * Contest text carries the party as a prefix ("DEM Justice of the Supreme
    Court" / "REP ..."); the prefix is stripped into the party column (DEM/REP)
    on every row of the contest, including Write-ins rows (party-empty write-in
    rows would fail the repo's duplicate_entries test in primaries).
  * Candidates cross-filed in both parties appear as separate DEM and REP
    contests with the same office text; both are kept, distinguished by party.
    Duplicates are disambiguated only within the same party with a " (2)"/" (3)"
    suffix, mirroring the general parser.
  * "Write-in" choices aggregate to a single "Write-ins" row per contest.
  * The nonpartisan special election "Commissioner Mt. Lebanon Ward 3" has
    choice party NON -> party column empty.
  * The "REGISTERED VOTERS - Nonpartisan" (Vote For 0) contest is replaced by
    Registered Voters / Ballots Cast metadata rows.

Usage:
    python pa_allegheny_primary_2023_results_parser.py <input.csv|.zip|.xml> <output.csv>
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


class OfficeNamer:
    """Disambiguates repeated (party, office) contests with a " (2)" suffix,
    matching the general parser's convention. Cross-party duplicates keep the
    same office text (the party column distinguishes them)."""

    def __init__(self):
        self.used = {}

    def name(self, party, office):
        key = (party, office)
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
    rvi = idx["registered_voters"]
    bci = idx["ballots_cast"]
    ovi = idx.get("over_votes")
    uvi = idx.get("under_votes")

    rows_out = []
    namer = OfficeNamer()
    contests = []  # (contest_name, [rows]) preserving order; consecutive
    # contests may repeat a contest_name (e.g. three "Auditor Forward" seats),
    # so a run also breaks when: a choice name repeats within the run; or
    # percent_of_votes increases (choices inside a Clarity contest are listed
    # in non-increasing percent order); or a named candidate follows a
    # Write-in choice that had 100% (single-choice seats share that percent).
    # All 825 contest boundaries reproduce the detail XML exactly with these
    # rules (verified: 0 in-contest percent increases, 0 rule conflicts).
    problems = []

    pvi = idx.get("percent_of_votes")

    rv_row = None
    current_name = None
    current_rows = []
    seen_choices = set()
    prev_pct = -1.0
    prev_choice = None
    for r in rows:
        cname = clean(r[ci])
        if cname.startswith("REGISTERED VOTERS"):
            rv_row = r
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
                contests.append((current_name, current_rows))
            current_name = cname
            current_rows = []
            seen_choices = set()
        prev_pct = pct
        prev_choice = ch
        seen_choices.add(ch)
        current_rows.append(r)
    if current_name is not None:
        contests.append((current_name, current_rows))

    if rv_row is not None:
        rv = int(rv_row[rvi])
        bc = int(rv_row[bci])
        rows_out.append([COUNTY, "Registered Voters", "", "", "", rv, "", "", ""])
        rows_out.append([COUNTY, "Ballots Cast", "", "", "", bc, "", "", ""])

    for office_text, crows in contests:
        parties = set()
        over = under = 0
        for r in crows:
            parties.add(clean(r[pi]).upper())
            if ovi is not None:
                over += int(r[ovi] or 0)
            if uvi is not None:
                under += int(r[uvi] or 0)
        party, office_base = contest_party(strip_vote_for(office_text), parties)
        office = namer.name(party, office_base)
        for r in crows:
            cand = clean(r[chi])
            if cand.lower() == "write-in":
                candidate = "Write-ins"
            else:
                candidate = cand
            row_party = party
            if not party:
                row_party = ""
            rows_out.append([
                COUNTY, office, "", row_party, candidate, int(r[ti]), "", "", "",
            ])
        if over or under:
            sys.stderr.write(
                "NOTE %s: over_votes=%d under_votes=%d (not emitted; aggregate "
                "rows only reported when non-zero)\n" % (office_text, over, under))

    header_out = ["county", "office", "district", "party", "candidate", "votes",
                  "election_day", "mail", "provisional"]
    write_csv(output_path, header_out, rows_out)
    sys.stderr.write("county rows=%d contests=%d\n" % (len(rows_out), len(contests)))
    return len(rows_out)


# ---------------------------------------------------------------------------
# Precinct-level: Clarity detail.xml
# ---------------------------------------------------------------------------

def extract_zip(input_path):
    tmpdir = tempfile.mkdtemp(prefix="allegheny_clarity_")
    with zipfile.ZipFile(input_path) as zf:
        names = [n for n in zf.namelist() if n.lower().endswith(".xml")]
        if not names:
            raise SystemExit("No XML file found in %s" % input_path)
        zf.extract(names[0], tmpdir)
        xml_path = os.path.join(tmpdir, names[0])
    return xml_path, tmpdir


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
        office = namer.name(party, office_base)

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
            if vt_totals != ctot:
                problems.append("SUM MISMATCH %s / %s: votetypes=%d totalVotes=%d"
                                % (ctext, candidate, vt_totals, ctot))

            for pname, bd in per_precinct.items():
                votes = sum(bd.values())
                if votes == 0:
                    continue  # omit all-zero precinct rows to keep file size sane
                rows.append([
                    COUNTY, pname, office, "", party, candidate, votes,
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


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)
    parse(sys.argv[1], sys.argv[2])