#!/usr/bin/env python3
"""Parse Allegheny County 2022 Primary Election precinct-detail export.

Source is a Dominion-tabulation "ElectionResult" XML snapshot (same vendor as
Allegheny's 2022 GENERAL SpreadsheetML export, but a different shape: plain
namespaced-element XML, not SpreadsheetML) generated 2022-06-07, i.e. after the
June GOP U.S. Senate recount:

  <ElectionResult>
    <VoterTurnout><Precincts><Precinct name ballotsCast totalVoters ...>
    <Contest text="REP United States Senator" ...>
      <VoteType name="regVotersCounty">           <- contest-level, zeros
      <Choice text="Dave McCormick" party="REP" totalVotes="36386">
        <VoteType name="Election Day|Absentee|Provisional" votes="...">
          <Precinct name="..." votes="..."/>

Each Contest text carries the ballot party prefix ("REP"/"DEM").  Committee
races (County Committeeman/Committeewoman for a single precinct, State Committee)
are included, matching the 2020 Allegheny primary precedent.

Vote types map to: Election Day -> election_day, Absentee -> mail,
Provisional -> provisional.

Usage:
  uv run python parsers/pa_allegheny_primary_2022_results_parser.py \
      "input.xml" "output.csv"
"""

import csv
import re
import sys
import xml.etree.ElementTree as ET

# (regex on party-stripped contest title, office name, group# for district)
OFFICE_MAP = [
    (re.compile(r"^United States Senator$", re.I), "U.S. Senate", None),
    (re.compile(r"^Governor$", re.I), "Governor", None),
    (re.compile(r"^Lieutenant Governor$", re.I), "Lieutenant Governor", None),
    (re.compile(r"^Representative in Congress District (\d+)$", re.I), "U.S. House", 1),
    (re.compile(r"^Senator in the General Assembly District (\d+)$", re.I), "State Senate", 1),
    (re.compile(r"^Representative in the General Assembly District (\d+)$", re.I), "State House", 1),
]

STATE_COMMITTEE_RE = re.compile(r"^Member of State Committee(?: At-?Large)?(?: District (\d+))?$", re.I)
STATE_COMMITTEE_ATLARGE_RE = re.compile(r"^State Committee At-?Large$", re.I)
# "County Committeeman/Female Member" (REP) and "County Committee Male/Female
# Member" (DEM).  DEM titles are TRUNCATED to 50 chars in the source
# (e.g. "County Committee Male Member Pittsburgh Ward 10 Di"), so these offices
# are rebuilt as "<role> <precinct>" using the single precinct each covers.
COMMITTEEPERSON_RE = re.compile(
    r"^(County Committee(?: Male| Female) Member|County Committeem[ae]n|County Committeewoman)\s+(.*)$",
    re.I)
WRITE_IN_RE = re.compile(r"^(write-?ins?|write[- ]ins)\b", re.I)


def normalize_office(title, party):
    """Return (office, district) from a party-stripped contest title."""
    for rx, name, dg in OFFICE_MAP:
        m = rx.match(title)
        if m:
            return name, m.group(dg) if dg else ""
    m = STATE_COMMITTEE_RE.match(title)
    if m or STATE_COMMITTEE_ATLARGE_RE.match(title):
        dnum = m.group(1) if m else ""
        return "Member of %s State Committee" % ("Republican" if party == "REP" else "Democratic"), dnum
    return title.strip(), ""


def collapse_ws(s):
    """Collapse consecutive whitespace (file_format test rejects 'Gary J.  Moeller')."""
    return re.sub(r"\s+", " ", s).strip()


def contest_meta(contest):
    """Return (party, office, district) for a Contest element."""
    ctext = contest.get("text", "")
    party, title = "", ctext
    m = re.match(r"^(DEM|REP)\s+(.+)$", ctext)
    if m:
        party, title = m.group(1), m.group(2).strip()
    title = collapse_ws(title)
    mcp = COMMITTEEPERSON_RE.match(title)
    if mcp:
        # rebuild "<role> <precinct>" from the (single) precinct the race covers
        precincts = sorted({collapse_ws(p.get("name")) for p in contest.iter("Precinct")})
        if len(precincts) == 1:
            return party, "%s %s" % (mcp.group(1), precincts[0]), ""
        # multiple precincts: fall back to printed title (may be truncated)
        return party, title.strip(), ""
    office, district = normalize_office(title, party)
    return party, office, district


def parse(path, out_path):
    tree = ET.parse(path)
    root = tree.getroot()

    rows = []
    skipped_zero = 0
    contest_count = 0

    # --- Registered Voters / Ballots Cast from VoterTurnout ---
    turnout = {}  # precinct -> (totalVoters, ballotsCast)
    pt = root.find("VoterTurnout/Precincts")
    if pt is not None:
        for p in pt.findall("Precinct"):
            turnout[p.get("name")] = (int(p.get("totalVoters", 0)), int(p.get("ballotsCast", 0)))

    # --- contests ---
    for contest in root.findall("Contest"):
        ctext = contest.get("text", "")
        if ctext.upper().startswith("REGISTERED VOTERS"):
            continue  # all-zero shell; RV comes from VoterTurnout
        party, office, district = contest_meta(contest)
        contest_count += 1

        for choice in contest.findall("Choice"):
            cand = collapse_ws(choice.get("text", ""))
            if WRITE_IN_RE.match(cand.lower()):
                cand = "Write Ins"
            cparty = (choice.get("party") or party).upper()
            for vt in choice.findall("VoteType"):
                name = vt.get("name", "")
                if name == "Election Day":
                    col = "election_day"
                elif name == "Absentee":
                    col = "mail"
                elif name == "Provisional":
                    col = "provisional"
                else:
                    raise SystemExit("unexpected VoteType: %r" % name)
                for p in vt.findall("Precinct"):
                    precinct = collapse_ws(p.get("name"))
                    v = int(p.get("votes", 0))
                    if v == 0:
                        continue
                    rows.append({
                        "county": "Allegheny",
                        "precinct": precinct,
                        "office": office,
                        "district": district,
                        "party": cparty,
                        "candidate": cand,
                        "votes": v,
                        col: v,
                    })

    # --- Registered Voters / Ballots Cast rows ---
    for precinct in sorted(turnout):
        rv, bc = turnout[precinct]
        if rv:
            rows.append({
                "county": "Allegheny", "precinct": precinct,
                "office": "Registered Voters", "district": "", "party": "",
                "candidate": "", "votes": rv,
            })
        if bc:
            rows.append({
                "county": "Allegheny", "precinct": precinct,
                "office": "Ballots Cast", "district": "", "party": "",
                "candidate": "", "votes": bc,
            })

    # collapse the sparse dicts into fixed columns, preserving per-contest order
    header = ["county", "precinct", "office", "district", "party", "candidate",
              "votes", "election_day", "mail", "provisional"]
    # merge per-votetype rows for the same (contest, choice, precinct)
    merged = {}
    order = []
    for r in rows:
        if r["office"] in ("Registered Voters", "Ballots Cast"):
            order.append(r)
            continue
        k = (r["precinct"], r["office"], r["district"], r["party"], r["candidate"])
        if k not in merged:
            merged[k] = {"county": "Allegheny", "precinct": r["precinct"],
                         "office": r["office"], "district": r["district"],
                         "party": r["party"], "candidate": r["candidate"],
                         "votes": 0, "election_day": 0, "mail": 0, "provisional": 0}
            order.append(merged[k])
        m2 = merged[k]
        votes = r["votes"]
        m2["votes"] += votes
        for col in ("election_day", "mail", "provisional"):
            m2[col] += r.get(col, 0)

    n = 0
    with open(out_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=header)
        w.writeheader()
        for r in order:
            if r["office"] not in ("Registered Voters", "Ballots Cast"):
                if r["votes"] == 0 and r["election_day"] == 0 and r["mail"] == 0 and r["provisional"] == 0:
                    skipped_zero += 1
                    continue
            w.writerow({k: r.get(k, "") for k in header})
            n += 1
    print(f"wrote {n} rows to {out_path} (contests={contest_count}, zero-rows skipped={skipped_zero})")


if __name__ == "__main__":
    src = sys.argv[1] if len(sys.argv) > 1 else \
        "/Users/dwillis/code/openelections-sources-pa/2022/primary/Allegheny PA 2022 Primary Precinct Detail.xml"
    dst = sys.argv[2] if len(sys.argv) > 2 else \
        "2022/counties/20220517__pa__primary__allegheny__precinct.csv"
    parse(src, dst)