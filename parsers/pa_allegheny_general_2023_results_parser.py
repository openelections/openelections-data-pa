#!/usr/bin/env python3
"""Parse Allegheny County 2023 general election results (Clarity Elections XML).

Input: the "Allegheny Clarity Data.zip" (containing detail.xml) or the
detail.xml file itself, as exported from the Clarity detail report.

Output: OpenElections precinct-level CSV:
    county,precinct,office,district,party,candidate,votes,election_day,mail,provisional

Notes on the source:
  * Registered Voters / Ballots Cast per precinct come from <VoterTurnout>.
  * Each <Contest> has <Choice> children with per-precinct <VoteType> blocks
    named "Election Day", "Absentee" and "Provisional".  Clarity's tabulation
    for this election combines absentee and mail ballots into "Absentee";
    those votes are written to the `mail` column.
  * "Write-in" choices are reported as candidate "Write-ins" (party empty).
  * Retention questions are contests whose text is "<Office> - <Judge name>";
    they become office "<Office> (Retention) <Judge name>" with Yes/No rows.
  * A few contest texts appear twice (two separate seats with identical
    titles); the second and later occurrences get a " (2)" suffix on office.

Usage:
    python pa_allegheny_general_2023_results_parser.py <input.zip|input.xml> <output.csv>
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

# Party codes we pass through unchanged; anything else maps to "".
PARTY_KEEP = {
    "DEM", "REP", "D/R", "LBR", "LIB", "GRN", "CON", "IND", "CGO", "DFC",
    "GOC", "MF", "MFT", "SFS", "UWS", "ASO", "FWD",
}


def norm_party(raw):
    p = (raw or "").strip().upper()
    if not p or p in ("NON", "Y", "N"):
        return ""
    return p


def retention_split(text):
    """Return (office, name) if the contest text looks like a retention
    question '<Office> - <Name>' for a judicial office, else None."""
    if " - " not in text:
        return None
    prefix, name = text.rsplit(" - ", 1)
    pl = prefix.lower()
    if ("judge of the" in pl or "justice of the" in pl) and "court" in pl:
        return prefix, name.strip()
    return None


def parse(input_path, output_path):
    xml_path = input_path
    tmpdir = None
    if input_path.lower().endswith(".zip"):
        tmpdir = tempfile.mkdtemp(prefix="allegheny_clarity_")
        with zipfile.ZipFile(input_path) as zf:
            zf.extractall(tmpdir)
        names = [n for n in zf.namelist() if n.lower().endswith(".xml")]
        if not names:
            raise SystemExit("No XML file found in %s" % input_path)
        xml_path = "%s/%s" % (tmpdir, names[0])

    tree = etree.parse(xml_path)

    # Registered voters / ballots cast per precinct.
    turnout = OrderedDict()  # precinct -> (rv, bc)
    for prec in tree.iter("Precinct"):
        # only VoterTurnout/Precincts/Precinct elements have totalVoters
        if prec.getparent().tag == "Precincts":
            turnout[prec.get("name")] = (
                int(prec.get("totalVoters", "0")),
                int(prec.get("ballotsCast", "0")),
            )

    rows = []
    validation = {"contest_total_mismatch": [], "sum_mismatch": []}
    office_used = {}
    contests = tree.iter("Contest")

    for contest in contests:
        ctext = contest.get("text").strip()
        is_question = contest.get("isQuestion", "false") == "true"
        ctotal = int(contest.get("totalVotes", contest.get("totalvotes", "0")) or 0)

        # Build office label (disambiguating repeated contest texts).
        if is_question:
            ret = retention_split(ctext)
            if ret:
                office = "%s (Retention) %s" % (ret[0], ret[1])
            else:
                office = ctext
        else:
            office = ctext
        if office in office_used:
            office_used[office] += 1
            office = "%s (%d)" % (office, office_used[office])
        else:
            office_used[office] = 1

        contest_vote_sum = 0
        for choice in contest.findall("Choice"):
            cname = re.sub(r"\s{2,}", " ", choice.get("text")).strip()
            if cname.lower() == "write-in":
                candidate, party = "Write-ins", ""
            else:
                candidate = cname
                party = norm_party(choice.get("party"))
            if is_question:
                party = ""

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
                    pname = prec.get("name")
                    v = int(prec.get("votes", "0"))
                    per_precinct.setdefault(pname, {})[col] = v

            vt_totals = sum(
                int(vt.get("votes", "0"))
                for vt in choice.findall("VoteType") if vt.get("name") != "regVotersCounty"
            )
            if vt_totals != int(choice.get("totalVotes", "0")):
                validation["sum_mismatch"].append(
                    (ctext, candidate, vt_totals, choice.get("totalVotes"))
                )
            contest_vote_sum += int(choice.get("totalVotes", "0"))

            for pname, bd in per_precinct.items():
                votes = sum(bd.values())
                if votes == 0:
                    continue  # omit all-zero precinct rows to keep file size sane
                rows.append([
                    COUNTY, pname, office, "", party, candidate, votes,
                    bd.get("election_day", ""), bd.get("mail", ""), bd.get("provisional", ""),
                ])

        if ctotal and contest_vote_sum != ctotal:
            validation["contest_total_mismatch"].append((ctext, contest_vote_sum, ctotal))

    # Metadata rows: Registered Voters + Ballots Cast per precinct.
    for pname, (rv, bc) in turnout.items():
        rows.append([COUNTY, pname, "Registered Voters", "", "", "", rv, "", "", ""])
        if bc:
            rows.append([COUNTY, pname, "Ballots Cast", "", "", "", bc, "", "", ""])

    header = ["county", "precinct", "office", "district", "party", "candidate",
              "votes", "election_day", "mail", "provisional"]
    with open(output_path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)

    if tmpdir:
        for root, _, files in os.walk(tmpdir, topdown=False):
            for fn in files:
                os.remove(os.path.join(root, fn))
            os.rmdir(root)

    # Report validation issues on stderr.
    for c, cand, s, t in validation["sum_mismatch"][:20]:
        sys.stderr.write("SUM MISMATCH %s / %s: votetypes=%s totalVotes=%s\n" % (c, cand, s, t))
    for c, s, t in validation["contest_total_mismatch"][:20]:
        sys.stderr.write("CONTEST TOTAL MISMATCH %s: choices=%s attr=%s\n" % (c, s, t))
    sys.stderr.write("rows=%d contests=%d precincts=%d\n" %
                     (len(rows), len(office_used), len(turnout)))


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)
    parse(sys.argv[1], sys.argv[2])