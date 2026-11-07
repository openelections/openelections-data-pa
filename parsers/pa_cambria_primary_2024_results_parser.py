#!/usr/bin/env python3
"""Parse Cambria County PA 2024 General Primary (Apr 23, 2024) precinct results.

Source: "Cambria PA 2024 Primary Precinct Results.xls" — an SOE Software
SpreadsheetML (Excel 2003 XML) export, same format family (and same layout)
as the Cambria 2022 primary precinct detail, so this parser adapts
parsers/pa_cambria_primary_2022_results_parser.py (same county/vendor) with
the shared engine helpers from parsers/soe_xml_precinct.py (read-only):

- Contest titles carry the ballot party as a prefix ("DEM PRESIDENT OF THE
  UNITED STATES ( 1)").  The prefix is stripped into the party column and
  that party is attached to EVERY row of the contest, including the Write
  Ins row (repo-wide primary convention: a party-less write-in row would
  make the DEM and REP sections of the same office collide in the
  duplicate_entries data test).
- Vote-type column labels per candidate block are "Election Day /
  Absentee/Mail-in / Provisional / Total Votes"; combined absentee/mail-in
  goes to `mail`, per the brief.  A final all-candidates "Total" column ends
  the last block and is not a candidate row.
- Unlike 2022, the export has no per-party "BALLOTS CAST - DEM/REP/NON"
  sheets and no precinct-committee contests.  One contest per sheet 2-24.
- Registered Voters sheet carries precinct Registered Voters, Ballots Cast
  and a turnout breakdown (Election Day / Absentee-Mail / Provisional).
  The breakdown columns are a turnout breakdown of Ballots Cast and are NOT
  carried as vote-breakdown columns on the Registered Voters / Ballots Cast
  rows (they would break the vote_breakdown_totals data test against the RV
  count); RV and Ballots Cast are recorded as votes-only rows, the 2022
  Cambria convention.
- Contest titles are truncated at 50 chars in the export ("... 71ST DISTRI"),
  which is long enough for district extraction (State House 71/72/73,
  delegate CD 13).  The State Senate contests ("SENATOR IN THE GENERAL
  ASSEMBLY ( 1)") carry no district number in the title; Cambria's 2024
  state-senator districts are single-valued here and SD 35 (Wayne
  Langerholc Jr.) is the district for the whole county, so district 35 is
  recorded (verification: summary file'sSenator-in-General-Assembly turnout
  RV/BC rows span all 125 precincts).

Usage: python parsers/pa_cambria_primary_2024_results_parser.py <input.xls> <output.csv>
"""

import csv
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from soe_xml_precinct import FIELDNAMES, SovcXmlConfig, _rows  # noqa: E402
from soe_xml_precinct import is_summary_row, normalize_candidate  # noqa: E402
from soe_xml_precinct import normalize_office  # noqa: E402

COUNTY = "Cambria"
SS = "urn:schemas-microsoft-com:office:spreadsheet"
NS = {"s": SS}

CONFIG = SovcXmlConfig(county=COUNTY)

WARNINGS = []

# label -> column meaning ("total" alone = all-candidates boundary)
LABEL_MAP = {
    "registered voters": None,
    "election day": "election_day",
    "election day votes": "election_day",
    "absentee/mail-in": "mail",
    "mail-in / absentee": "mail",
    "mail in / absentee": "mail",
    "absentee / mail": "mail",
    "provisional": "provisional",
    "provisional votes": "provisional",
}


def sheet_title(sheet):
    for row in sheet.findall(".//{" + SS + "}Row"):
        for cell in row.findall(".//{" + SS + "}Cell"):
            data = cell.find(".//{" + SS + "}Data", NS)
            if data is not None and data.text and data.text.strip():
                return data.text.strip()
    return ""


def map_contest(body):
    """(office, district, vote_for) from the party-stripped contest body."""
    t = re.sub(r"\s+", " ", body).strip()
    vote_for = 1
    m = re.search(r"\(\s*(?:Vote[ Ff]or\s*)?(\d+)\s*\)\s*$", t)
    if m:
        vote_for = int(m.group(1))
        t = t[: m.start()].strip()
    up = t.upper()
    # normalize_office() has no President mapping and needs trailing text
    # after "SENATOR IN THE GENERAL ASSEMBLY"; delegate titles are truncated
    # at 50 chars ("...NATIONAL CONVENTION 13T"), so map them explicitly.
    if up.startswith("PRESIDENT"):
        return "President", "", vote_for
    if up.startswith("ALTERNATE DELEGATE"):
        return "Alternate Delegate to the Republican National Convention", "13", vote_for
    if up.startswith("DELEGATE TO THE DEMOCRATIC"):
        return "Delegate to the Democratic National Convention", "13", vote_for
    if up.startswith("DELEGATE TO THE REPUBLICAN"):
        return "Delegate to the Republican National Convention", "13", vote_for
    if up.startswith("SENATOR IN THE GENERAL ASSEMBLY"):
        # no district number in the title; Cambria's whole county is SD 35
        return "State Senate", "35", vote_for
    office, district, _ = normalize_office(t, CONFIG)
    # State Senate contest title carries no district number; Cambria's whole
    # county is in SD 35.
    if office == "State Senate" and district == "":
        district = "35"
    return office, district, vote_for


def parse_workbook(path):
    tree = ET.parse(str(path))
    sheets = tree.getroot().findall(".//s:Worksheet", NS)
    by_name = {s.get("{" + SS + "}Name"): s for s in sheets}

    rows = []

    # ---- Registered Voters sheet ------------------------------------------
    grid = list(_rows(by_name.get("Registered Voters")))
    rv_col, bc_col = None, None
    for vals in grid:
        low = [str(v).strip().lower() for v in vals]
        if "registered voters" in low and "ballots cast" in low:
            rv_col = low.index("registered voters")
            bc_col = low.index("ballots cast")
            break
    if rv_col is None:
        sys.exit("Cambria 2024 primary: no Registered Voters/Ballots Cast header row")
    for vals in grid:
        name = re.sub(r"\s+", " ", str(vals[0]).strip()) \
            if vals and vals[0] else ""
        if not name or name == "None" or is_summary_row(name):
            continue
        rv_raw = vals[rv_col].strip() if rv_col < len(vals) else ""
        if rv_raw.isdigit():
            rows.append({"county": COUNTY, "precinct": name,
                         "office": "Registered Voters", "district": "",
                         "party": "", "candidate": "", "votes": int(rv_raw),
                         "election_day": "", "mail": "", "provisional": ""})
        bc_raw = bc_col if bc_col is not None and bc_col < len(vals) else None
        if bc_raw is not None and str(vals[bc_raw]).strip().isdigit():
            rows.append({"county": COUNTY, "precinct": name,
                         "office": "Ballots Cast", "district": "",
                         "party": "", "candidate": "",
                         "votes": int(str(vals[bc_raw]).strip()),
                         "election_day": "", "mail": "", "provisional": ""})

    # ---- Contest sheets (numeric names in source order) --------------------
    def sort_key(kv):
        n = kv[0]
        return (0, int(n)) if n.isdigit() else (1, 0)

    for sheet_name, sheet in sorted(
        ((n, s) for n, s in by_name.items()
         if n not in ("Table of Contents", "Registered Voters")),
        key=sort_key,
    ):
        grid = list(_rows(sheet))
        if len(grid) < 4:
            continue
        title = sheet_title(sheet)
        if not title:
            continue
        # party prefix ("DEM ", "REP ")
        party, body = "", title
        m = re.match(r"^(DEM|REP|LIB|GRN)\s+(.+)$", title, re.I)
        if m:
            party = m.group(1).upper()
            body = m.group(2).strip()
        office, district, vf = map_contest(body)

        # candidate blocks: names in row 2 (index 1), labels in row 3
        cand_cells = grid[1]
        labels = [str(v).strip().lower() if v is not None else ""
                  for v in grid[2]]
        blocks = []  # (start_col, candidate, is_writein)
        for ci, v in enumerate(cand_cells):
            if v and str(v).strip() not in ("", "None"):
                cand, _p = normalize_candidate(v, CONFIG)
                if not cand:
                    continue
                blocks.append((ci, cand, bool(re.search(r"write|scatter", v, re.I))))
        col_kind = {}   # column -> vote-type column
        col_cand = {}   # column -> (block_start, candidate, is_writein)
        cur = None
        for ci, lab in enumerate(labels):
            for bi, cand, w in blocks:
                if bi == ci:
                    cur = (bi, cand, w)
            if lab in ("", "precinct", "precinct name", "registered voters",
                       "registered voters - total"):
                continue
            if lab == "total":  # all-candidates column ends the block
                cur = None
                continue
            kind = LABEL_MAP.get(lab)
            if lab in ("total votes", "total votes cast"):
                kind = "votes"
            if kind is None:
                WARNINGS.append(f"{sheet_name} {title!r}: unmapped label {lab!r}")
                cur = None
                continue
            if cur:
                col_kind[ci] = kind
                col_cand[ci] = cur
        if not col_kind:
            WARNINGS.append(f"{title}: no vote columns recognized")
            continue
        writein_cols = {ci for ci, lab in enumerate(labels)
                        if "write" in lab or "scatter" in lab}

        # data rows
        for row in grid[3:]:
            if not row:
                continue
            name = re.sub(r"\s+", " ", str(row[0]).strip()) \
                if row[0] is not None else ""
            if is_summary_row(name) or not name or name == "None":
                continue
            agg = {}
            for ci, kind in col_kind.items():
                if ci >= len(row) or row[ci] is None:
                    continue
                s = str(row[ci]).strip().replace(",", "")
                if not s.isdigit():
                    continue
                bi, cand, w = col_cand[ci]
                if w or ci in writein_cols:
                    cand = "Write Ins"
                v = int(s)
                d = agg.setdefault(cand, {"votes": 0, "election_day": 0,
                                          "mail": 0, "provisional": 0})
                d[kind] += v
            for cand, d in agg.items():
                if cand == "Write Ins" and d["votes"] == 0:
                    continue
                rows.append({
                    "county": COUNTY, "precinct": name,
                    "office": office, "district": district,
                    "party": party, "candidate": cand,
                    "votes": d["votes"], "election_day": d["election_day"],
                    "mail": d["mail"], "provisional": d["provisional"],
                })
    return rows


def main(argv):
    if len(argv) != 3:
        sys.exit(f"Usage: {Path(argv[0]).name} <input.xls> <output.csv>")
    rows = parse_workbook(argv[1])
    with open(argv[2], "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDNAMES)
        w.writeheader()
        w.writerows(rows)
    print(f"Wrote {len(rows)} rows to {argv[2]}")
    for msg in WARNINGS:
        print("WARNING:", msg)


if __name__ == "__main__":
    main(sys.argv)
