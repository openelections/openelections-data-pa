"""Shared parser for SOE Software ("Clarity") SpreadsheetML (Excel 2003 XML)
precinct-detail exports (York, Luzerne, Westmoreland counties, 2022 general).

Workbook layout (per county, minor variations):
  * Sheet "Table of Contents": contest list (ignored).
  * Sheet "Registered Voters": precinct -> Registered Voters counts.
  * Sheets "2".."N": one contest per sheet:
      row 1: contest title (e.g. "UNITED STATES SENATOR ( 1)")
      row 2: candidate headers (one cell per contest candidate block, possibly
             with party prefix ("REP Mehmet Oz") or ticket separator ("//"))
      row 3: column labels: Precinct | Registered Voters | then, per
             candidate, a group of vote-type columns in a county-specific
             order (always ending in "Total Votes") | final all-candidates
             "Total" column.
      rows 4+: one row per precinct.

County config knobs (see SovcXmlConfig): none of which the engine cannot
infer, so configs normally only set ``county`` and drop this in. The engine
reads the label row to determine each county's vote-type column order, so
no per-county ordering table is needed.
"""

from __future__ import annotations

import csv
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

SS_NS = "urn:schemas-microsoft-com:office:spreadsheet"
NS = {"s": SS_NS}
SS = "{" + SS_NS + "}"

VOTE_GROUP_LABELS = {
    "election day": "election_day",
    "election day votes": "election_day",
    "mail-in": "mail",
    "mail in / absentee": "mail",
    "mail-in / absentee": "mail",
    "mail in / absentee votes": "mail",
    "mail-in  / absentee votes": "mail",
    "mail-in / absentee votes": "mail",
    "absentee/mail": "mail",
    "absentee / mail": "mail",
    "provisional": "provisional",
    "provisional votes": "provisional",
}


@dataclass
class SovcXmlConfig:
    county: str
    # contest title -> (office, district); raise ValueError to catch unmapped
    # (the engine falls back to regex office mapping below when None)
    office_map: Optional[dict] = None
    # strip a leading party token ("REP Mehmet Oz") into the party column
    party_prefix: bool = False
    # normalize "A // B" ticket separators to " / "
    slash_ticket: bool = True
    skip_contests: tuple = ()  # sheet-name prefixes to skip entirely
    # candidate-name prettifier applied after ticket/party normalization
    candidate_normalizer: Optional[callable] = None


def is_summary_row(name):
    """County/cumulative summary rows appearing after the precinct list."""
    n = str(name).strip()
    return (
        not n
        or n.lower().startswith("total")
        or re.match(r"^cumulative", n, re.I)
        or re.search(r"county - total", n, re.I)
        or n.lower() in ("precinct", "precinct portion")
    )


def _rows(sheet):
    """Yield each sheet row as a list of cell values, honoring s:Index
    (sparse cells) and s:MergeAcross (each merged cell pads with empties so
    column positions stay aligned with the label row)."""
    for row in sheet.iter(SS + "Row"):
        vals = []
        pos = 1  # 1-based SpreadsheetML column position
        for cell in row.findall(SS + "Cell"):
            idx = cell.get(SS + "Index")
            if idx:
                pos = int(idx)
            while len(vals) < pos - 1:
                vals.append("")
            data = cell.find("s:Data", NS)
            v = ""
            if data is not None and data.text:
                # Number cells are sometimes stored with trailing junk; keep
                # the raw text and let callers strip.
                v = data.text
            vals.append(v.strip() if isinstance(v, str) else v)
            merge = cell.get(SS + "MergeAcross")
            if merge:
                vals += [""] * int(merge)
            pos = len(vals) + 1
        yield vals


def normalize_candidate(name, config, party=None):
    """Normalize a candidate header cell to (candidate, party)."""
    name = re.sub(r"\s+", " ", str(name)).strip()
    party = party or ""
    if config.party_prefix:
        m = re.match(r"^(DEM|REP|LIB|GRN|KEY|CON|IND|NON)\s+(.+)$", name, re.I)
        if m:
            party = m.group(1).upper()
            name = m.group(2).strip()
    if config.slash_ticket:
        # 'Josh SHAPIRO // Austin DAVIS' -> 'Josh Shapiro / Austin Davis'
        name = re.sub(r"\s*//+\s*", " / ", name)
    # Title-case any word containing an uppercase run (surname style:
    # 'Josh SHAPIRO', 'Mehmet OZ', 'Tim McMASTER' -> 'Mcmaster',
    # 'BAGDES-CANNING' -> 'Bagdes-Canning'); title() also fixes apostrophes.
    if re.search(r"[A-Z]{2,}", name):
        words = []
        for w in name.split():
            if re.search(r"[A-Z]{2,}", w):
                words.append(w.title())
            else:
                words.append(w)
        name = " ".join(words)
    name = re.sub(r"\s+", " ", name).strip(" .")
    return name.strip(), party.strip()


def normalize_office(title, config):
    """Contest title -> (office, district, vote_for)."""
    t = re.sub(r"\s+", " ", str(title)).strip()
    vote_for = 1
    m = re.search(r"\(\s*(?:Vote[ Ff]or\s*)?(\d+)\s*\)\s*$", t)
    if m:
        vote_for = int(m.group(1))
        t = re.sub(r"\(\s*Vote[ Ff]or\s*\d+\s*\)\s*$", "", t).strip()
        t = re.sub(r"\(\s*\d+\s*\)\s*$", "", t).strip()
    t = t.strip(" .")
    up = t.upper()
    if up.startswith("UNITED STATES SENATOR"):
        return "U.S. Senate", "", vote_for
    if up.startswith("GOVERNOR"):
        return "Governor", "", vote_for
    if up.startswith("ATTORNEY GENERAL"):
        return "Attorney General", "", vote_for
    if up.startswith("AUDITOR GENERAL"):
        return "Auditor General", "", vote_for
    if up.startswith("STATE TREASURER"):
        return "State Treasurer", "", vote_for
    m = re.match(r"^(REPRESENTATIVE IN CONGRESS|U\.S\. (?:REPRESENTATIVE|HOUSE))[ \-]*(.+)$", up)
    if m:
        dnum = re.search(r"(\d+)", m.group(2) or "")
        return "U.S. House", dnum.group(1) if dnum else "", vote_for
    m = re.match(r"^(SENATOR IN THE GENERAL ASSEMBLY|STATE SENATE)[ \-]*(.+)$", up)
    if m:
        dnum = re.search(r"(\d+)", m.group(2) or "")
        return "State Senate", dnum.group(1) if dnum else "", vote_for
    m = re.match(r"^REPRESENTATIVE IN THE GENERAL ASSEMBLY[ \-]*(\d+)?", up)
    if m:
        return "State House", m.group(1) or "", vote_for
    if config.office_map and t in config.office_map:
        return config.office_map[t], "", vote_for
    if any(q in up for q in ("QUESTION", "REFERENDUM")):
        return t.title(), "", vote_for
    return t.title(), "", vote_for


def parse_workbook(path, config: SovcXmlConfig):
    tree = ET.parse(str(path))
    sheets = tree.getroot().findall(".//s:Worksheet", NS)
    by_name = {s.get(SS + "Name"): s for s in sheets}

    # ---- Registered Voters sheet ------------------------------------------
    rv = {}
    for vals in _rows(by_name.get("Registered Voters", ET.Element("x"))):
        if len(vals) >= 2 and vals[0] and is_summary_row(vals[0]):
            continue
        if len(vals) >= 2 and vals[0] and str(vals[1]).strip().isdigit():
            rv[str(vals[0]).strip()] = int(str(vals[1]))
    if not rv:
        for vals in _rows(by_name.get("Registered Voters", ET.Element("x"))):
            if len(vals) >= 2 and vals[0]:
                pass
    # fallback: find the column holding numbers in the Registered Voters sheet
    if not rv:
        grid = list(_rows(by_name["Registered Voters"]))
        for ci in range(1, 6):
            col = [row[ci] if ci < len(row) else "" for row in grid]
            nums = [v for v in col if str(v).strip().isdigit()]
            if len(nums) > len(grid) // 2:
                for row in grid:
                    if row[0] and ci < len(row) and str(row[ci]).strip().isdigit():
                        rv[str(row[0]).strip()] = int(str(row[ci]))
                break

    rows = []
    for precinct, count in sorted(rv.items()):
        rows.append({"county": config.county, "precinct": precinct,
                     "office": "Registered Voters", "district": "",
                     "party": "", "candidate": "", "votes": count,
                     "election_day": "", "mail": "", "provisional": ""})

    # optional "Ballots Cast" column on the Registered Voters sheet
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
        if bc_col and bc_col < len(vals) and str(vals[bc_col]).strip().isdigit():
            ballots_cast[name] = int(str(vals[bc_col]).strip())
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
        if not grid:
            continue
        title = str(grid[0][0]).strip() if grid[0] else ""
        if not title or title == "None":
            continue
        if config.skip_contests and any(k in title for k in config.skip_contests):
            continue
        office, district, vote_for = normalize_office(title, config)

        # candidate columns: row 2 has names where a contest block starts;
        # row 3 has per-column labels.
        if len(grid) < 3:
            continue
        cand_cells = grid[2 - 1 if False else 1]  # row 2 (index 1)
        labels = grid[2]
        # map each candidate block's start column -> candidate name
        blocks = []  # (start_col, candidate, party, is_writein)
        for ci, v in enumerate(cand_cells):
            if v and str(v).strip() not in ("", "None"):
                cand, party = normalize_candidate(v, config)
                if not cand:
                    continue
                w = bool(re.search(r"write|scatter", v, re.I))
                blocks.append((ci, cand, party, w))
        # find group boundaries from labels: each label cell that says
        # Election-day-ish or 'Total Votes' belongs to the nearest prior
        # candidate block (blocks are laid out in column order).
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
                # the sheet's final all-candidates "Total" column (and any
                # unrecognized label) ends the current candidate block
                cur = None
        block_writein = {bi: w for bi, c, p, w in blocks}
        if not col_group:
            continue
        first_data_col = min(bi for bi, c, p, w in blocks if any(col_cand.get(ci) == (bi, c, p) for ci in col_group)) if blocks else 1

        # which candidate columns are write-in columns?
        writein_cols = set()
        for ci, lab in enumerate(labels):
            if ci < len(labels) and lab is not None:
                l = str(lab).strip().lower()
                if "write" in l or "scatter" in l:
                    writein_cols.add(ci)

        # walk data rows
        for row in grid[3:]:
            if not row:
                continue
            name = row[0] if row[0] is not None else ""
            name = str(name).strip()
            if is_summary_row(name) or not name or name == "None":
                continue
            vals = {}
            for ci in col_group:
                if ci < len(row) and row[ci] is not None:
                    v = str(row[ci]).strip().replace(",", "")
                    if v.isdigit():
                        vals[col_group[ci]] = int(v)
            votes = vals.get("votes", 0)
            # candidate columns grouped: aggregate write-in columns into one
            agg = {}  # (candidate, party) -> totals
            for ci, g in col_group.items():
                bi, cand, party = col_cand[ci]
                if ci in writein_cols or block_writein.get(bi):
                    cand, party = "Write Ins", ""
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


FIELDNAMES = ["county", "precinct", "office", "district", "party",
              "candidate", "votes", "election_day", "mail", "provisional"]


def run_cli(config, argv=None):
    import sys

    argv = list(sys.argv) if argv is None else argv
    if len(argv) != 3:
        sys.exit(f"Usage: {Path(argv[0]).name} <input.xls> <output.csv>")
    rows = parse_workbook(argv[1], config)
    with open(argv[2], "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDNAMES)
        w.writeheader()
        w.writerows(rows)
    print(f"Wrote {len(rows)} rows to {argv[2]}")