#!/usr/bin/env python3
"""Parse Allegheny County 2022 General Election "Official Detail" export.

Source is a SpreadsheetML (.xls, Excel 2003 XML) workbook with one worksheet
per contest plus a "Registered Voters" summary sheet. Each contest sheet:
  row 0: merged contest title, e.g. "United States Senator (Vote For 1)"
  row 1: candidate labels, each merged across 4 cells ("DEM John Fetterman", "Write-in", ...)
  row 2: column labels (County, Registered Voters, then per candidate
         Election Day / Absentee / Provisional / Total Votes, then Total)
  rows 3..n: one row per precinct; final row is "Total:"

Usage:
  uv run python parsers/pa_allegheny_general_2022_results_parser.py \
      "input.xls" "output.csv"
"""

import csv
import re
import sys
import xml.etree.ElementTree as ET

NS = {"s": "urn:schemas-microsoft-com:office:spreadsheet"}
S = NS["s"]

OFFICE_MAP = [
    (re.compile(r"^United States Senator", re.I), "U.S. Senate", None),
    (re.compile(r"^President", re.I), "President", None),
    (re.compile(r"^Governor and Lieutenant Governor", re.I), "Governor", None),
    (re.compile(r"^Representative in Congress District (\d+)", re.I), "U.S. House", 1),
    (re.compile(r"^Senator in the General Assembly District (\d+)", re.I), "State Senate", 1),
    (re.compile(r"^Representative in the General Assembly District (\d+)", re.I), "State House", 1),
]

PARTY_RE = re.compile(r"^(DEM|REP|LIB|GRN|CON|IND|KEY|RFC|SSP|BST|FWD|PRO|NL|SOC|UST|APV|NON)\s+(.+)$", re.I)


def normalize_office(title):
    """Return (office, district) from a contest title."""
    t = re.sub(r"\s*\(Vote For \d+\)\s*$", "", title).strip()
    for rx, name, dg in OFFICE_MAP:
        m = rx.match(t)
        if m:
            district = m.group(dg) if dg else ""
            return name, district
    # any other title carrying "District N"
    m = re.search(r"District\s+(\d+)$", t, re.I)
    district = m.group(1) if m else ""
    return t, district


def cell_values(row):
    """Return a list of (col_index, text) honoring ss:Index and ss:MergeAcross."""
    out = []
    idx = 0
    for c in row.findall("s:Cell", NS):
        i = c.get("{%s}Index" % S)
        if i:
            idx = int(i)
        d = c.find("s:Data", NS)
        txt = "".join(d.itertext()) if d is not None else ""
        ma = c.get("{%s}MergeAcross" % S)
        span = int(ma) + 1 if ma else 1
        if txt.strip():
            out.append((idx, txt.strip()))
        idx += span
    return out


def parse_candidate_label(label):
    """Split 'DEM Josh Shapiro' into (party, candidate); Write-in handled."""
    if label.lower().replace("-", " ").strip() in ("write in", "write-in", "writeins", "write ins"):
        return "", "Write Ins"
    m = PARTY_RE.match(label)
    if m:
        return m.group(1).upper(), m.group(2).strip()
    return "", label.strip()


def parse(path, out_path):
    tree = ET.parse(path)
    sheets = {ws.get("{%s}Name" % S): ws for ws in tree.getroot().findall("s:Worksheet", NS)}

    # --- Registered Voters / Ballots Cast sheet ---
    rv_rows = {}  # precinct -> (rv, ed, abs, prov, ballots_cast)
    for row in sheets["Registered Voters"].find("s:Table", NS).findall("s:Row", NS):
        cells = cell_values(row)
        if not cells or cells[0][1] in ("County", "Total:"):
            continue
        d = {i: t for i, t in cells}
        precinct = d[0]
        rv_rows[precinct] = (
            int(d.get(1, 0)), int(d.get(2, 0)), int(d.get(3, 0)),
            int(d.get(4, 0)), int(d.get(5, 0)),
        )

    rows = []
    # --- contest sheets ---
    for name in sorted((k for k in sheets if k not in ("Table of Contents", "Registered Voters")),
                       key=lambda x: int(x)):
        tbl = sheets[name].find("s:Table", NS)
        all_rows = tbl.findall("s:Row", NS)
        title = cell_values(all_rows[0])[0][1]
        office, district = normalize_office(title)

        # candidate header row -> column offset for each candidate
        cand_cols = []  # (start_col, party, candidate)
        for col, txt in cell_values(all_rows[1]):
            party, cand = parse_candidate_label(txt)
            cand_cols.append((col, party, cand))

        for row in all_rows[3:]:
            cells = cell_values(row)
            if not cells:
                continue
            d = dict(cells)
            precinct = d.get(0)
            if not precinct or precinct == "Total:":
                continue
            for col, party, cand in cand_cols:
                total = int(d.get(col + 3, 0))
                if total == 0 and all(int(d.get(col + k, 0)) == 0 for k in range(4)):
                    continue
                rows.append({
                    "county": "Allegheny",
                    "precinct": precinct,
                    "office": office,
                    "district": district,
                    "party": party,
                    "candidate": cand,
                    "votes": total,
                    "election_day": d.get(col + 0, "0"),
                    "early_voting": d.get(col + 1, "0"),
                    "provisional": d.get(col + 2, "0"),
                })

    # --- Registered Voters / Ballots Cast rows ---
    for precinct, (rv, ed, ab, pv, bc) in rv_rows.items():
        rows.append({
            "county": "Allegheny", "precinct": precinct, "office": "Registered Voters",
            "district": "", "party": "", "candidate": "", "votes": rv,
            "election_day": ed, "early_voting": ab, "provisional": pv,
        })
        rows.append({
            "county": "Allegheny", "precinct": precinct, "office": "Ballots Cast",
            "district": "", "party": "", "candidate": "", "votes": bc,
            "election_day": ed, "early_voting": ab, "provisional": pv,
        })

    header = ["county", "precinct", "office", "district", "party", "candidate",
              "votes", "election_day", "early_voting", "provisional"]
    with open(out_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=header)
        w.writeheader()
        for r in rows:
            w.writerow(r)
    print(f"wrote {len(rows)} rows to {out_path}")


if __name__ == "__main__":
    src = sys.argv[1] if len(sys.argv) > 1 else \
        "/Users/dwillis/code/openelections-sources-pa/2022/general/Allegheny PA 2022 General Official Detail.xls"
    dst = sys.argv[2] if len(sys.argv) > 2 else \
        "2022/counties/20221108__pa__general__allegheny__precinct.csv"
    parse(src, dst)