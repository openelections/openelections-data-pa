#!/usr/bin/env python3
"""Parse Cambria County PA 2022 General Primary (May 17, 2022) precinct results.

Source: "Cambria PA 2022 Primary Precinct Detail.xls" — an SOE Software
SpreadsheetML (Excel 2003 XML) export, same format family as the 2022
general York/Luzerne/Westmoreland precinct details, so this parser imports
the shared engine's helpers (parsers/soe_xml_precinct.py, read-only) and
implements a primary-specific contest loop:

- Contest titles carry the ballot party as a prefix ("DEM UNITED STATES
  SENATOR ( 1)").  The prefix is stripped into the party column and that
  party is attached to EVERY row of the contest, including the Write Ins
  row (repo-wide primary convention: a party-less write-in row would make
  the DEM and REP sections of the same office collide in the
  duplicate_entries data test).
- Vote-type column labels are "Election Day / Absentee/Mail-in /
  Provisional / Total Votes" per candidate block; combined
  absentee-in-person + mail-in goes to `mail`, per the brief.
- The three "BALLOTS CAST - DEM/REP/NON" sheets are skipped: per-party
  ballots cast are not recorded (repo convention, cf. Westmoreland 2023
  primary parser).  Overall per-precinct Registered Voters / Ballots Cast
  (with breakdown columns on the "Registered Voters" sheet) ARE recorded,
  as `Registered Voters` / `Ballots Cast` rows with empty party/candidate
  and votes only (2020/2022 general file convention).
- Per-precinct DEM COMMITTEEMAN/COMMITTEEWOMAN and REP COMMITTEE PERSON
  contests (write-in only, one contest per precinct, sheets 21+; the DEM
  committee races are separate COMMITTEEMAN and COMMITTEEWOMAN contests)
  are kept, office as printed after the party prefix (e.g. "Committeeman
  Adams Township No. 1", "Committee Person Adams Township - Dunlo",
  "Committeewoman ..."), with the contest party.
- Contest titles are truncated at 50 chars in the export ("... 71ST DISTRI"),
  which is long enough for district extraction.
- The source predates the June 2022 GOP U.S. Senate statewide recount
  (no Cambria recount/amended file exists), so the REP U.S. Senate rows
  are pre-recount certified vote counts; the ENR anchor is post-recount
  and the residual is documented at verification time.

Usage: python parsers/pa_cambria_primary_2022_results_parser.py <input.xls> <output.csv>
"""

import csv
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from soe_xml_precinct import FIELDNAMES, is_summary_row, normalize_candidate  # noqa: E402
from soe_xml_precinct import SovcXmlConfig, _rows, normalize_office  # noqa: E402

COUNTY = "Cambria"
SS = soe_ns = "urn:schemas-microsoft-com:office:spreadsheet"
NS = {"s": SS}

CONFIG = SovcXmlConfig(
    county=COUNTY,
    # keys are the contest title body (after party prefix + "( N)" strip)
    office_map={
        "MEMBER OF DEMOCRATIC STATE COMMITTEE": "Member of Democratic State Committee",
        "MEMBER OF REPUBLICAN STATE COMMITTEE": "Member of Republican State Committee",
    },
)

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

WARNINGS = []


def _key(s):
    """Comparison key: lowercase alphanumerics only."""
    return re.sub(r"[^A-Za-z0-9]", "", s).lower()


def sheet_title(sheet):
    for row in sheet.findall(".//{" + SS + "}Row"):
        for cell in row.findall(".//{" + SS + "}Cell"):
            data = cell.find(".//{" + SS + "}Data", NS)
            if data is not None and data.text and data.text.strip():
                return data.text.strip()
    return ""


def split_party(title):
    """('DEM', 'UNITED STATES SENATOR') -> party prefix (or '', title)."""
    t = re.sub(r"\s+", " ", title).strip()
    m = re.match(r"^(DEM|REP|LIB|GRN)\s+(.+)$", t)
    if m:
        return m.group(1).upper(), m.group(2).strip()
    return "", t


def strip_vote_for(body):
    m = re.search(r"\(\s*(?:Vote[ Ff]or\s*)?(\d+)\s*\)\s*$", body)
    if not m:
        return body, 1
    return body[:m.start()].strip(), int(m.group(1))


def map_contest(body):
    """(office, district, vote_for) from the party-stripped contest body."""
    body, vf = strip_vote_for(body)
    office, district, _vf2 = normalize_office(body, CONFIG)
    return office, district, vf


def parse_workbook(path):
    tree = ET.parse(str(path))
    sheets = tree.getroot().findall(".//s:Worksheet", NS)
    by_name = {s.get("{" + SS + "}Name"): s for s in sheets}

    rows = []

    # ---- Registered Voters sheet ------------------------------------------
    grid = list(_rows(by_name.get("Registered Voters")))
    # header row: locate the Registered Voters / Ballots Cast columns
    rv_col, bc_col = None, None
    for vals in grid:
        low = [str(v).strip().lower() for v in vals]
        if "registered voters" in low and "ballots cast" in low:
            rv_col = low.index("registered voters")
            bc_col = low.index("ballots cast")
            break
    if rv_col is None:
        sys.exit("Cambria 2022 primary: no Registered Voters/Ballots Cast header row")
    for vals in grid:
        name = str(vals[0]).strip() if vals and vals[0] else ""
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
        party, body = split_party(title)
        if "BALLOTS CAST" in body.upper():
            continue  # per-party ballots cast: not recorded (repo convention)
        office, district, vf = map_contest(body)

        # candidate blocks: names in row 2 (index 1), labels in row 3
        cand_cells = grid[1]
        labels = [str(v).strip().lower() if v is not None else ""
                  for v in grid[2]]
        blocks = []  # (start_col, candidate, is_writein)
        cur = None
        for ci, v in enumerate(cand_cells):
            if v and str(v).strip() not in ("", "None"):
                cand, _p = normalize_candidate(v, CONFIG)
                if not cand:
                    continue
                blocks.append((ci, cand, bool(re.search(r"write|scatter", v, re.I))))
        col_kind = {}   # column -> vote-type column
        col_cand = {}   # column -> (block_start, candidate, is_writein)
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
            name = str(row[0]).strip() if row[0] is not None else ""
            if is_summary_row(name) or not name or name == "None":
                continue
            row_office = office
            # the export truncates some contest titles ("... SEVENTEENTH
            # WARD NO."): the four REP Committee Person contests for
            # Johnstown 17th Ward lose their ward number in the title.
            # Recover it from the precinct row name, which is the same
            # string plus "No. <n>" (one precinct per contest).
            m_off = re.match(r"^(.+?)\s+No\.?$", office, re.I)
            m_pre = re.match(r"^(.+?)\s+No\.?\s*(\d+)$", name, re.I)
            if m_off and m_pre \
                    and _key(m_off.group(1)).endswith(_key(m_pre.group(1))):
                row_office = f"{m_off.group(1)} No. {m_pre.group(2)}"
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
                    "office": row_office, "district": district,
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