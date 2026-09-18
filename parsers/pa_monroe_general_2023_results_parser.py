#!/usr/bin/env python3
"""Parse Monroe County 2023 general election HTML results.

The county site publishes:
  * one "Precinct Results <Municipality>" HTML per municipality (20 files),
    each containing that municipality's LOCAL contests with municipality-wide
    totals (the finest granularity Monroe publishes).  Each municipality
    becomes one `precinct` value in the output.
  * one "Official Results" summary HTML with countywide totals for the
    statewide judicial, county row office, magisterial district judge and
    school director contests.

Usage:
  # Municipality files -> precinct-schema CSV (one or more files, or a dir)
  python pa_monroe_general_2023_results_parser.py <files-or-dir> <out.csv>

  # Official summary -> countywide rows (county-level schema, no precinct col)
  python pa_monroe_general_2023_results_parser.py --summary <summary.html> <out.csv>

Contest panels look like:
  <h4 class="panel-title"><a>Title</a><small>...</small>
      <small>(6 Year Term)Vote For Not More Than 2 Candidates</small></h4>
  <table><tbody><tr><td>NAME (PARTY)</td><td class="votetotal">n</td>...
"""
from __future__ import annotations

import csv
import glob
import os
import re
import sys

from bs4 import BeautifulSoup

FIELDNAMES = ["county", "precinct", "office", "district", "party",
              "candidate", "votes", "election_day", "mail", "provisional"]
COUNTY_FIELDNAMES = ["county", "office", "district", "party",
                     "candidate", "votes", "election_day", "mail", "provisional"]

PARTY_MAP = {
    "DEMOCRATIC": "DEM",
    "REPUBLICAN": "REP",
    "DEMOCRATIC/REPUBLICAN": "D/R",
    "REPUBLICAN/DEMOCRATIC": "D/R",
    "LIBERTARIAN": "LIB",
    "GREEN": "GRN",
    "CONSTITUTION": "CON",
    "LIBERTY": "LBR",
}

# office title (from panel <a>) -> standardized office
OFFICE_MAP = {
    "supervisor": "Township Supervisor",
    "auditor": "Township Auditor",
    "commissioner": "Township Commissioner",
    "councilmember": "Council Member",
    "council member": "Council Member",
    "mayor": "Mayor",
    "constable": "Constable",
    "high constable": "High Constable",
    "tax collector": "Tax Collector",
}

COUNTY_OFFICE_MAP = {
    "Commissioner": "County Commissioner",
    "Controller": "County Controller",
}


def clean_name(raw: str) -> str:
    """'PATTI  OKEEFE' -> 'Patti Okeefe'; 'MCCORMACK' -> 'McCormack'."""
    s = re.sub(r"\s+", " ", raw).strip()
    out = []
    for w in s.split(" "):
        if not w:
            continue
        lw = w.lower()
        if lw.startswith("mc") and len(w) > 2 and w[2:3].isalpha():
            out.append("Mc" + lw[2].upper() + lw[3:])
        elif lw.startswith("o'") and len(lw) > 2:
            out.append("O'" + lw[2].upper() + lw[3:])
        elif w.upper() in ("II", "III", "IV"):
            out.append(w.upper())
        else:
            out.append(w[0].upper() + lw[1:])
    return " ".join(out)


def parse_party(cell_text: str) -> tuple[str, str]:
    """Return (clean_name, party_code) from 'NAME (PARTY)' candidate text."""
    m = re.match(r"^(.*?)\s*\(([^()]*)\)\s*$", cell_text.strip())
    if not m:
        return clean_name(cell_text), ""
    name_raw, party_raw = m.group(1), m.group(2).strip().upper()
    party = PARTY_MAP.get(party_raw, "")
    return clean_name(name_raw), party


def term_suffix(subtext: str) -> str:
    """'(6 Year Term)Vote For ...' -> ' (6 Year)'."""
    m = re.search(r"\((\d+)\s*Year\s*Term\)", subtext or "")
    return f" ({m.group(1)} Year)" if m else ""


def parse_panel(pan) -> tuple[str, str, list[tuple[str, str]]]:
    """Return (title, term_subtext, [(candidate_cell, votes), ...])."""
    a = pan.select_one("h4.panel-title a")
    title = " ".join(a.stripped_strings).replace(' Eval("Key.OfficeTitle")', "").strip()
    smalls = pan.select("h4.panel-title small")
    subtext = " ".join(" ".join(s.stripped_strings) for s in smalls)
    rows = []
    for tb in pan.select("table"):
        for tr in tb.select("tbody tr"):
            tds = tr.select("td")
            if len(tds) < 2:
                continue
            cand = tds[0].get_text(" ", strip=True)
            votes = tds[1].get_text(strip=True)
            if cand and votes.isdigit():
                rows.append((cand, votes))
    return title, subtext, rows


def local_office_name(title: str, used_terms: dict) -> str:
    base = OFFICE_MAP.get(title.lower().strip())
    if base is None:
        base = title
    n = used_terms.get(title, 0)
    used_terms[title] = n + 1
    # term suffix only when the municipality runs multiple contests of the
    # same base office (6/4/2 year seats)
    return base  # suffix appended by caller after all panels are known


def parse_municipality_file(path: str, county: str = "Monroe"):
    with open(path, encoding="utf-8", errors="replace") as fh:
        soup = BeautifulSoup(fh.read(), "html.parser")
    prec_el = soup.select_one("#MainContent_precinct")
    if prec_el:
        precinct = prec_el.get_text(strip=True)
    else:
        base = os.path.basename(path)
        precinct = re.sub(r"^Monroe County Precinct Results ", "", base)
        precinct = re.sub(r" 2023 General\.html$", "", precinct)
    rows = []
    contests = []
    for pan in soup.select("div.panel-group > div.panel.panel-default"):
        title, subtext, cands = parse_panel(pan)
        parsed = []
        for cell, votes in cands:
            name, party = parse_party(cell)
            if not name:
                continue
            parsed.append((name, party, int(votes)))
        contests.append((title, subtext, parsed))
    # decide term suffixes per office within this municipality
    from collections import Counter
    base_counts = Counter()
    for title, _s, _c in contests:
        base_counts[title] += 1
    for title, subtext, parsed in contests:
        base = OFFICE_MAP.get(title.strip().lower(), title.strip())
        suffix = ""
        m = re.search(r"\((\d+)\s*Year\s*Term\)", subtext or "")
        if base_counts[title] > 1 and m:
            suffix = f" ({m.group(1)} Year)"
        office = base + suffix
        for name, party, votes in parsed:
            rows.append({
                "county": county, "precinct": precinct, "office": office,
                "district": precinct, "party": party, "candidate": name,
                "votes": votes, "election_day": "", "mail": "",
                "provisional": "",
            })
    return rows


def map_county_office(title: str) -> tuple[str, str]:
    """(office, district) for summary-file contest titles."""
    t = title.strip()
    m = re.match(r"^Magisterial District Judge\s+(\d+-\d+-\d+)", t, re.I)
    if m:
        return "Magisterial District Judge", m.group(1)
    m = re.match(r"^(.*?)\s*School District\s*-\s*School Director\s*(.*)$", t, re.I)
    if m:
        district = m.group(1).strip() + " School District"
        region = m.group(2).strip().lower()
        rm = re.search(r"region\s*-?\s*(\d+)", region)
        if rm:
            district += f" Region {rm.group(1)}"
        return "School Director", district
    return COUNTY_OFFICE_MAP.get(t, t), ""


def parse_summary_file(path: str, county: str = "Monroe"):
    with open(path, encoding="utf-8", errors="replace") as fh:
        soup = BeautifulSoup(fh.read(), "html.parser")
    from collections import Counter
    contests = []
    for pan in soup.select("div.panel-group > div.panel.panel-default"):
        title, subtext, cands = parse_panel(pan)
        if not title:
            continue
        parsed = []
        for cell, votes in cands:
            name, party = parse_party(cell)
            if not name:
                continue
            parsed.append((name, party, int(votes)))
        contests.append((title, subtext, parsed))
    title_counts = Counter(t for t, _s, _c in contests)
    rows = []
    for title, subtext, parsed in contests:
        office, district = map_county_office(title)
        m = re.search(r"\((\d+)\s*Year\s*Term\)", subtext or "")
        if title_counts[title] > 1 and m:
            office += f" ({m.group(1)} Year)"
        for name, party, votes in parsed:
            rows.append({
                "county": county, "office": office, "district": district,
                "party": party, "candidate": name, "votes": votes,
                "election_day": "", "mail": "", "provisional": "",
            })
    return rows


def gather_inputs(inputs: list[str]) -> list[str]:
    files = []
    for inp in inputs:
        if os.path.isdir(inp):
            files.extend(sorted(glob.glob(
                os.path.join(inp, "Monroe County Precinct Results * 2023 General.html"))))
        else:
            files.append(inp)
    return files


def write_csv(path: str, rows: list[dict], fieldnames: list[str]):
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fieldnames)
        w.writeheader()
        for r in rows:
            w.writerow(r)
    print(f"wrote {len(rows)} rows -> {path}")


def main(argv: list[str]) -> int:
    args = argv[1:]
    summary = False
    if args and args[0] == "--summary":
        summary = True
        args = args[1:]
    if len(args) < 2:
        sys.exit(__doc__)
    output = args[-1]
    inputs = args[:-1]
    if summary:
        rows = parse_summary_file(inputs[0])
        write_csv(output, rows, COUNTY_FIELDNAMES)
    else:
        rows = []
        for f in gather_inputs(inputs):
            rows.extend(parse_municipality_file(f))
        write_csv(output, rows, FIELDNAMES)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))