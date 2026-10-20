#!/usr/bin/env python3
"""
Parse Westmoreland County PA 2022 General Primary precinct results.

Source: "Westmoreland County Precinct Detail 2022 Primary.xls" (SOE Software
SpreadsheetML export).

Same workbook family as the 2022 general export (see
parsers/pa_westmoreland_general_2022_results_parser.py / soe_xml_precinct.py),
with the primary-specific twists handled HERE rather than in the shared
engine (which is untouched):

  * Every contest title carries the ballot party prefix
    ("DEM UNITED STATES SENATOR ( 1)", "REP GOVERNOR ( 1)") -- stripped into
    the party column.
  * Party-affiliated committee races:
      "MEMBER OF THE DEMOCRATIC STATE COMMITTEE"   -> "Member of Democratic State Committee"
      "MEMBER OF THE REPUBLICAN STATE COMMITTEE"   -> "Member of Republican State Committee"
      "<PARTY> <PARTY-NAMED> COUNTY COMMITTEE <muni>" -> office kept as printed
        ("Democratic County Committee Adamsburg Borough"), district "".
  * Write-in candidate blocks are aggregated into candidate "Write Ins" and
    CARRY THE CONTEST'S PARTY (party-empty write-ins fail duplicate_entries
    in primaries).
  * The committee races report write-ins only; zero-write-in precincts are
    dropped (no invented rows).

The Registered Voters sheet also carries Ballots Cast, Election Day,
Absentee/Mail and Provisional per precinct; Registered Voters and Ballots
Cast are emitted as rows (party "").

Usage: python pa_westmoreland_primary_2022_results_parser.py <input.xls> <output.csv>
"""

import csv
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from soe_xml_precinct import (  # noqa: E402
    FIELDNAMES,
    SovcXmlConfig,
    VOTE_GROUP_LABELS,
    _rows,
    is_summary_row,
    normalize_candidate,
    normalize_office,
)

NS = {"s": "urn:schemas-microsoft-com:office:spreadsheet"}
SS = "{urn:schemas-microsoft-com:office:spreadsheet}"

CONFIG = SovcXmlConfig(county="Westmoreland", party_prefix=True)


def normalize_contest(title, config):
    """Contest title -> (office, district, vote_for, party)."""
    t = re.sub(r"\s+", " ", str(title)).strip()
    vote_for = 1
    m = re.search(r"\(\s*(?:Vote[ Ff]or\s*)?(\d+)\s*\)\s*$", t)
    if m:
        vote_for = int(m.group(1))
        t = re.sub(r"\(\s*(?:Vote[ Ff]or\s*)?\d+\s*\)\s*$", "", t).strip()
    t = t.strip(" .")
    party = ""
    m = re.match(r"^(DEM|REP)\s+(.+)$", t, re.I)
    if m:
        party = m.group(1).upper()
        t = m.group(2).strip()
    up = t.upper()
    if up.startswith("MEMBER OF THE DEMOCRATIC STATE COMMITTEE"):
        return "Member of Democratic State Committee", "", vote_for, party
    if up.startswith("MEMBER OF THE REPUBLICAN STATE COMMITTEE"):
        return "Member of Republican State Committee", "", vote_for, party
    # Democratic/Republican County Committee <municipality> (as printed)
    m = re.match(r"^(DEMOCRATIC|REPUBLICAN)\s+COUNTY COMMITTEE\s+(.+)$", up)
    if m:
        office = "%s County Committee %s" % (
            m.group(1).capitalize(), m.group(2).title())
        return re.sub(r"\s+", " ", office).strip(), "", vote_for, party
    m = re.match(r"^STATE SENATOR\s*([0-9]+)(?:ST|ND|RD|TH)?", up)
    if m:
        return "State Senate", m.group(1), vote_for, party
    m = re.match(r"^REPRESENTATIVE IN THE GENERAL ASSEMBLY\s*([0-9]+)?", up)
    if m:
        return "State House", m.group(1) or "", vote_for, party
    office, district, vote_for2 = normalize_office(t, config)
    return office, district, vote_for, party


def parse_workbook(path, config):
    tree = ET.parse(str(path))
    sheets = tree.getroot().findall(".//s:Worksheet", NS)
    by_name = {s.get(SS + "Name"): s for s in sheets}

    # ---- Registered Voters sheet (carves RV and Ballots Cast rows) --------
    rv = {}
    ballots_cast = {}
    bc_col = None
    for vals in _rows(by_name.get("Registered Voters", ET.Element("x"))):
        name = str(vals[0]).strip() if vals and vals[0] else ""
        if bc_col is None:
            for ci, v in enumerate(vals):
                if str(v).strip().lower() == "ballots cast":
                    bc_col = ci
            continue
        if not name or name == "None" or is_summary_row(name):
            continue
        if len(vals) >= 2 and str(vals[1]).strip().isdigit():
            rv[name] = int(str(vals[1]).strip())
        if bc_col and bc_col < len(vals) and str(vals[bc_col]).strip().isdigit():
            ballots_cast[name] = int(str(vals[bc_col]).strip())
    rows = []
    for precinct, count in sorted(rv.items()):
        rows.append({"county": config.county, "precinct": precinct,
                     "office": "Registered Voters", "district": "",
                     "party": "", "candidate": "", "votes": count,
                     "election_day": "", "mail": "", "provisional": ""})
    for precinct, count in sorted(ballots_cast.items()):
        rows.append({"county": config.county, "precinct": precinct,
                     "office": "Ballots Cast", "district": "",
                     "party": "", "candidate": "", "votes": count,
                     "election_day": "", "mail": "", "provisional": ""})

    # ---- Contest sheets ----------------------------------------------------
    for sheet_name, sheet in sorted(
        ((n, s) for n, s in by_name.items()
         if n not in ("Table of Contents", "Registered Voters")),
        key=lambda kv: int(kv[0]) if kv[0].isdigit() else 10 ** 9,
    ):
        grid = list(_rows(sheet))
        if len(grid) < 3:
            continue
        title = str(grid[0][0]).strip() if grid[0] else ""
        if not title or title == "None":
            continue
        office, district, vote_for, contest_party = normalize_contest(title, config)

        cand_cells = grid[1]
        labels = grid[2]
        blocks = []  # (start_col, candidate, party, is_writein)
        for ci, v in enumerate(cand_cells):
            if v and str(v).strip() not in ("", "None"):
                cand, party = normalize_candidate(v, config)
                if not cand:
                    continue
                w = bool(re.search(r"write|scatter", v, re.I))
                blocks.append((ci, cand, party, w))
        col_group = {}
        col_cand = {}
        cur = None
        for ci in range(len(labels)):
            lab = str(labels[ci]).strip().lower() if ci < len(labels) and labels[ci] is not None else ""
            for bi, cand, party, w in blocks:
                if bi == ci:
                    cur = (bi, cand, party)
            if lab in VOTE_GROUP_LABELS and cur:
                col_group[ci] = VOTE_GROUP_LABELS[lab]
                col_cand[ci] = cur
            elif lab in ("total votes", "total votes cast", "scatter", "scattered",
                         "write-in (total)", "write-in total"):
                if cur:
                    col_group[ci] = "votes"
                    col_cand[ci] = cur
            elif lab and lab not in ("precinct", "registered voters",
                                     "registered voters - total", "precinct name"):
                cur = None
        block_writein = {bi: w for bi, c, p, w in blocks}
        if not col_group:
            continue

        for row in grid[3:]:
            if not row:
                continue
            name = str(row[0] if row[0] is not None else "").strip()
            if is_summary_row(name) or not name or name == "None":
                continue
            agg = {}  # (candidate, party) -> [votes, election_day, mail, provisional]
            for ci, g in col_group.items():
                bi, cand, _ = col_cand[ci]
                # Primary: every row of the sheet carries the contest's ballot
                # party (candidate headers here are prefix-free).
                party = contest_party
                if ci in _writein_cols(labels) or block_writein.get(bi):
                    cand = "Write Ins"
                v = 0
                if ci < len(row) and row[ci] is not None:
                    s = str(row[ci]).strip().replace(",", "")
                    if s.isdigit():
                        v = int(s)
                d = agg.setdefault((cand, party), [0, 0, 0, 0])
                if g == "votes":
                    d[0] = v
                elif g == "election_day":
                    d[1] += v
                elif g == "mail":
                    d[2] += v
                elif g == "provisional":
                    d[3] += v
            for (cand, party), (v, ed, mi, pr) in agg.items():
                if cand == "Write Ins" and v == 0:
                    continue
                if config.candidate_normalizer:
                    cand = config.candidate_normalizer(cand)
                rows.append({
                    "county": config.county, "precinct": name,
                    "office": office, "district": district,
                    "party": party, "candidate": cand,
                    "votes": v, "election_day": ed,
                    "mail": mi, "provisional": pr,
                })
    return rows


def _writein_cols(labels):
    cols = set()
    for ci, lab in enumerate(labels):
        if lab is not None:
            l = str(lab).strip().lower()
            if "write" in l or "scatter" in l:
                cols.add(ci)
    return cols


def main(argv=None):
    argv = list(sys.argv) if argv is None else argv
    if len(argv) != 3:
        sys.exit(f"Usage: {Path(argv[0]).name} <input.xls> <output.csv>")
    rows = parse_workbook(argv[1], CONFIG)
    with open(argv[2], "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDNAMES)
        w.writeheader()
        w.writerows(rows)
    print(f"Wrote {len(rows)} rows to {argv[2]}")


if __name__ == "__main__":
    main()