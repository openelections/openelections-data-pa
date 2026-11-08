#!/usr/bin/env python3
"""Cambria County, PA 2026 General Primary (May 19, 2026) — precinct + countywide results parser.

Source situation (documented):

  * Cambria's only county source for the 2026 primary is
    "Cambria_PA_DOS_Official_Summary_Text_Groups_6.8.26.pdf"
    (~/code/openelections-sources-pa/2026/primary/), an Electionware
    "Summary Results Official" report generated 06/08/2026 9:14 AM (107
    pages).  It is COUNTYWIDE-only: the first ~577 lines carry the countywide
    contest summaries (DEM Governor / Lt. Governor / U.S. House 13 / State
    House 71-72-73 / Member of Democratic State Committee District 11, then
    the same slate REP), each block printing the countywide
    total / election-day / absentee+mail-in / provisional breakdown per
    candidate, named "Write-In: <name>" vote groups, a "Write-In Totals" row,
    Not Assigned / Total Votes Cast / Overvotes / Undervotes / Contest Totals.
    The remaining pages are per-precinct DEM/REP COUNTY COMMITTEE PERSON
    races only — no statewide contest is ever tabulated per precinct there.

  * Because the county source has no per-precinct vote data, every precinct
    row comes from the PA DOS certified precinct-returns bulk file
    (source: "PA DOS 2026 General Primary Certified Precinct Returns
    Statewide.txt", county code 11 = Cambria; slice kept at
    work2026p/dos/cambria__dos_precinctreturns.txt, 1,058 rows over 125
    precincts).  Per DOS precedent (Snyder/Clearfield 2022, Fulton 2024:
    see parsers/pa_snyder_primary_2022_results_parser.py):
      - no write-in rows exist in the DOS bulk file -> none emitted;
      - no Registered Voters / Ballots Cast rows exist -> none emitted;
      - no vote breakdown columns -> the precinct CSV uses the plain
        7-column header county,precinct,office,district,party,candidate,votes.
    Contests the DOS file does not carry are omitted from the precinct file
    (State Committee; SD 35 — Langerholc was not up in this cycle; the
    uncontested sides of State House 71 (DEM), 72 (DEM has Burns... see
    anchor) and 73 (DEM) print no candidate rows in DOS because only the
    contested sides carry named candidates).

  * Precinct naming: the DOS export gives only a bare municipality name plus
    qualifier/split fields (22=name, 23=qualifier, 24=split).  Cambria DOS
    names are bare municipality ("ALLEGHENY"), "W 17 X 1" ward entries and
    "X DUNLO" / "X 1" split entries.  The municipality suffix
    (Borough/Township; "Johnstown" for the city) is restored through a
    municipality map keyed on the export's municipality-sequence field
    (column 28), which lists Cambria's 60 municipalities in alphabetical
    order by their full registered names — verified against the county's own
    2022 primary files (Cresson Borough 75 < Cresson Township 80,
    Portage Borough 215 < Portage Township 220, Summerhill Borough 265 <
    Summerhill Township 270, all matching the borough/township pairs in
    2022/counties/20220517__pa__primary__cambria__precinct.csv).
    One pair is ambiguous in the raw export: DOS prints two bare "GALLITZIN"
    precincts (codes 610 and 635; Cambria has Gallitzin Borough and
    Gallitzin Township, both single-division).  They are disambiguated by
    municipality-sequence 145 vs 150 -> "Gallitzin Borough" = code 635,
    "Gallitzin Township" = code 610 (Borough sorts first in every verified
    same-name pair).  Either order changes only the two precinct labels,
    not any vote value.
    Splits: numeric -> "<mun> Precinct N", village/"X" splits -> "<mun> -
    <Village>", wards -> "<mun> Ward N[ Precinct D]" (brief naming rule).

  * The countywide CSV comes from the Electionware summary PDF (exact
    printed values incl. its total / election-day / absentee+mail-in /
    provisional breakdowns).  Named candidate rows are title-cased and one
    aggregated "Write-In Totals" row per contest is emitted (Berks/Blair
    2026 counties precedent); the source prints individual write-in groups
    but no per-group totals beyond the printed Write-In Totals row, so
    individual write-in names are NOT emitted.  Not Assigned / Total Votes
    Cast / Overvotes / Undervotes / Contest Totals rows are not emitted;
    local (per-precinct) county-committee-person races are not emitted.

Usage:
    uv run python parsers/pa_cambria_primary_2026_results_parser.py <input> <output.csv>

Input auto-detected: .pdf (or extracted text of the Electionware summary)
  -> countywide CSV from the summary PDF; DOS precinct-returns text (first
  line starts '"2026","P"') -> precinct CSV.
"""

from __future__ import annotations

import csv
import os
import re
import subprocess
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# ---------------------------------------------------------------- precincts

# Municipality sequence (column 28 of the DOS export) -> full municipality
# name.  Cambria's sequence is alphabetical by full registered name; boroughs
# sort before same-root townships (see docstring).
MUNICIPALITIES = {
    "5": "Adams Township",
    "10": "Allegheny Township",
    "15": "Ashville Borough",
    "25": "Barr Township",
    "30": "Blacklick Township",
    "35": "Brownstown Borough",
    "40": "Cambria Township",
    "45": "Carrolltown Borough",
    "50": "Cassandra Borough",
    "57": "Chest Township",
    "60": "Chest Springs Borough",
    "65": "Clearfield Township",
    "70": "Conemaugh Township",
    "75": "Cresson Borough",
    "80": "Cresson Township",
    "85": "Croyle Township",
    "90": "Daisytown Borough",
    "95": "Dale Borough",
    "100": "Dean Township",
    "105": "East Carroll Township",
    "110": "East Conemaugh Borough",
    "115": "East Taylor Township",
    "120": "Ebensburg Borough",
    "125": "Ehrenfeld Borough",
    "130": "Elder Township",
    "135": "Ferndale Borough",
    "140": "Franklin Borough",
    "145": "Gallitzin Borough",
    "150": "Gallitzin Township",
    "155": "Geistown Borough",
    "160": "Hastings Borough",
    "165": "Jackson Township",
    "170": "Johnstown",
    "175": "Lilly Borough",
    "180": "Lorain Borough",
    "185": "Loretto Borough",
    "190": "Lower Yoder Township",
    "195": "Middle Taylor Township",
    "200": "Munster Township",
    "205": "Nanty Glo Borough",
    "208": "Northern Cambria Borough",
    "210": "Patton Borough",
    "215": "Portage Borough",
    "220": "Portage Township",
    "225": "Reade Township",
    "230": "Richland Township",
    "235": "Sankertown Borough",
    "240": "Scalp Level Borough",
    "245": "South Fork Borough",
    "250": "Southmont Borough",
    "260": "Stonycreek Township",
    "265": "Summerhill Borough",
    "270": "Summerhill Township",
    "275": "Susquehanna Township",
    "280": "Tunnelhill Borough",
    "285": "Upper Yoder Township",
    "290": "Vintondale Borough",
    "295": "Washington Township",
    "300": "West Carroll Township",
    "305": "Westmont Borough",
    "310": "West Taylor Township",
    "315": "White Township",
    "320": "Wilmore Borough",
    # Johnstown city: DOS reports its own municipality-sequence values but the
    # ballot title is plain "Johnstown" (2026/2022 county precedent).
}

# A few printed village/split labels differ from a raw title-case of the DOS
# split text; these match the county's own 2022/2024 precinct names.
SPLIT_OVERRIDE = {
    "CENTER TOWN": "Centertown",
    "OLD CONEMAUGH WOODVALE": "Old Conemaugh/Woodvale",
}


def _title(text: str) -> str:
    """'ST. MICHAEL' -> 'St. Michael', 'COVER HILL' -> 'Cover Hill'."""
    out = []
    for word in text.split():
        out.append(word.capitalize() if not word.startswith("MC") else "Mc" + word[2:].capitalize())
    return " ".join(out)


WARD_SPLIT_RE = re.compile(r"^(.+?) [Ww] (.+?)(?: [Xx] (.+))?$")


def dos_precinct_name(rec: dict) -> str:
    """DOS name/qualifier/split/munseq -> repo precinct label."""
    name = rec["name"].strip()
    qual = rec["qual"].strip().upper()
    split = rec["split"].strip()
    mun = MUNICIPALITIES.get(rec["munseq"], _title(name))
    if qual == "W":
        m = WARD_SPLIT_RE.match(name)
        ward, div = (m.group(2), m.group(3)) if m else (split, None)
        if ward and ward.isdigit():
            label = f"{mun} Ward {int(ward)}"
        else:
            label = f"{mun} Ward {_title(ward or split)}"
        if div and div.isdigit():
            label += f" Precinct {int(div)}"
        elif div:
            label += f" Precinct {_title(div)}"
        return label
    if qual == "X":
        if split.isdigit():
            return f"{mun} Precinct {int(split)}"
        label = SPLIT_OVERRIDE.get(split.upper())
        return f"{mun} - {label or _title(split)}"
    return mun


# DOS office code -> (office name, district-ful flag)
OFFICES = {
    "GOV": ("Governor", ""),
    "LTG": ("Lieutenant Governor", ""),
    "USC": ("U.S. House", None),  # district from the office rank/district field
    "STS": ("State Senate", None),
    "STH": ("State House", None),
}

# DOS (office, party, LAST, FIRST, MIDDLE) -> printed candidate name.  The
# countywide summary PDF / anchor print ballot names (e.g. "BETH FARNHAM",
# "PHIL RICE"), not the DOS-style full legal names.
CANDIDATES = {
    ("GOV", "DEM", "SHAPIRO"): "Josh Shapiro",
    ("GOV", "REP", "GARRITY"): "Stacy Garrity",
    ("LTG", "DEM", "DAVIS"): "Austin Davis",
    ("LTG", "REP", "RICHEY"): "Jason Richey",
    ("LTG", "REP", "VENTRE"): "John Ventre",
    ("USC", "DEM", "FARNHAM"): "Beth Farnham",
    ("USC", "REP", "JOYCE"): "John Joyce",
    ("STH", "DEM", "BURNS"): "Frank Burns",
    ("STH", "REP", "RICE"): "Phil Rice",
    ("STH", "REP", "RIGBY"): "Jim Rigby",
    ("STH", "REP", "KEPHART"): "Dallas Kephart",
}


def parse_dos_precinct_returns(txt_path: str, out_csv: str) -> list[str]:
    """Build the precinct CSV from a DOS certified precinct-returns slice."""
    with open(txt_path) as fh:
        rows = list(csv.reader(fh))
    header = [
        "county", "precinct", "office", "district", "party", "candidate", "votes",
    ]
    out = []
    for r in rows:
        if len(r) < 29:
            continue
        office_code, party = r[8], r[9]
        if office_code not in OFFICES:
            continue
        office, dist_field = OFFICES[office_code]
        if dist_field is None:
            district = r[5].lstrip("0") or "0"
        else:
            district = dist_field
        key = (office_code, party, r[11].strip().upper())
        candidate = CANDIDATES.get(key)
        if candidate is None:
            candidate = f"{_title(r[12])} {_title(r[11])}"
        out.append({
            "county": "Cambria",
            "precinct": dos_precinct_name(
                {"name": r[22], "qual": r[23], "split": r[24], "munseq": r[28]}
            ),
            "office": office,
            "district": district,
            "party": party,
            "candidate": candidate,
            "votes": str(int(r[15])),
        })
    out.sort(key=lambda d: (d["precinct"], header.index("office")))
    order = {p: i for i, p in enumerate(sorted({d["precinct"] for d in out}))}
    contest_order = {"Governor": 0, "Lieutenant Governor": 1, "U.S. House": 2,
                     "State Senate": 3, "State House": 4}
    out.sort(key=lambda d: (order[d["precinct"]], contest_order.get(d["office"], 9),
                            d["party"], d["candidate"]))
    os.makedirs(os.path.dirname(out_csv) or ".", exist_ok=True)
    with open(out_csv, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=header)
        w.writeheader()
        w.writerows(out)
    return out


# ------------------------------------------------------------ county summary

CONTEST_RE = re.compile(r"^\s*(DEM|REP)\s+(.+)$")


def pdf_text(path: str) -> str:
    if not path.lower().endswith(".pdf"):
        with open(path) as fh:
            return fh.read()
    txt = path + ".txt"
    if os.path.exists(txt):
        with open(txt) as fh:
            return fh.read()
    with tempfile.TemporaryDirectory() as td:
        subprocess.run(
            ["pdftotext", "-layout", path, os.path.join(td, "t.txt")], check=True
        )
        with open(os.path.join(td, "t.txt")) as fh:
            return fh.read()


def _normalize_contest(title: str) -> tuple[str, str]:
    """'REPRESENTATIVE IN THE GENERAL ASSEMBLY 71ST DISTRICT' -> ('State House','71')."""
    t = re.sub(r"\s+", " ", title).strip()
    low = t.lower()
    if "governor" in low and "lieutenant" not in low and "attorney" not in low:
        return "Governor", ""
    if "lieutenant governor" in low:
        return "Lieutenant Governor", ""
    m = re.search(r"congress\s+(\d+)(?:st|nd|rd|th)?", low)
    if m or "congress" in low:
        return "U.S. House", m.group(1) if m else ""
    m = re.search(r"general assembly\s+(\d+)(?:st|nd|rd|th)?", low)
    if m:
        return "State House", m.group(1)
    m = re.search(r"senator in the general assembly.*?(\d+)", low)
    if m:
        return "State Senate", m.group(1)
    if "democratic state committee" in low:
        return "Member of Democratic State Committee", ""
    if "republican state committee" in low:
        return "Member of Republican State Committee", ""
    return _title(t), ""


def parse_county_summary(pdf_path: str, out_csv: str) -> list[str]:
    """Build the countywide CSV from the Electionware summary PDF."""
    text = pdf_text(pdf_path)
    lines = text.splitlines()
    header = ["county", "office", "district", "party", "candidate",
              "votes", "election_day", "mail", "provisional"]
    out = []
    contest = None  # (party, office_raw)
    for line in lines:
        if re.match(r"^\s*(DEM|REP)\s+[A-Z]", line) and "Vote" not in line:
            m = CONTEST_RE.match(line)
            if m and not re.search(r"COMMITTEE PERSON|MUNICIPAL|TOWNSHIP \d|WARD \d|BORO", line.upper()):
                party, title = m.group(1), m.group(2)
                # Local per-precinct committee races repeat party+raw title;
                # they contain lowercase-ish details later — filter those by
                # the trailing "( ...)" page label handled below.
                contest = (party, title)
            continue
        if contest is None:
            continue
    party, title = contest
    office, district = _normalize_contest(title)
    m = re.match(
        r"^(\S.*?)\s\s+([\d,]+)\s+(\d[\d,]*)\s+(\d[\d,]*)\s+(\d+)\s*$", t
    )
    if not m:
        continue
    name, total, ed, mail, prov = m.groups()
    # Page-footer noise ("3 of 107") never matches the two-space number
    # pattern; local per-precinct committee-person races are excluded by the
    # header filter above.
        if re.search(r"COMMITTEE|COMMITTEEMAN|COMMITTEEWOMAN", title.upper()) and \
                not re.search(r"STATE COMMITTEE", title.upper()):
            contest = None
            continue
        if name.upper().startswith("WRITE-IN: "):
            continue  # individual write-in groups; only Write-In Totals emitted
        candidate = _title(name)
        if name.upper() == "WRITE-IN TOTALS":
            candidate = "Write-In Totals"
        out.append({
            "county": "Cambria",
            "office": office,
            "district": district,
            "party": party,
            "candidate": candidate,
            "votes": total.replace(",", ""),
            "election_day": ed.replace(",", ""),
            "mail": mail.replace(",", ""),
            "provisional": prov.replace(",", ""),
        })
    # order: Governor, Lt. Governor, U.S. House, State House, State Committee
    rank_order = {"Governor": 0, "Lieutenant Governor": 1, "U.S. House": 2,
                  "State House": 3, "State Senate": 4,
                  "Member of Democratic State Committee": 7,
                  "Member of Republican State Committee": 7,
                  "Member of State Committee": 7}
    def sort_key(d):
        party_rank = 0 if d["party"] == "DEM" else 1
        return (rank_order.get(d["office"], 5), d["district"].zfill(4),
                party_rank, d["candidate"] == "Write-In Totals", d["candidate"])
    # State Committee rows: group DEM then REP
    dem_sc = [d for d in out if d["office"] == "Member of Democratic State Committee"]
    rep_sc = [d for d in out if d["office"] == "Member of Republican State Committee"]
    others = [d for d in out if d["office"] not in (dem_sc[0]["office"], rep_sc[0]["office"])]
    # keep original candidate order inside each State Committee contest
    ordered = sorted(others, key=sort_key)
    dedup = []
    seen = set()
    for d in ordered + dem_sc + rep_sc:
        k = tuple(d[c] for c in header)
        if k in seen:
            continue
        seen.add(k)
        dedup.append(d)
    os.makedirs(os.path.dirname(out_csv) or ".", exist_ok=True)
    with open(out_csv, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=header)
        w.writeheader()
        w.writerows(dedup)
    return dedup


# ---------------------------------------------------------------- driver

def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(__doc__)
        return 2
    src, out_csv = argv[1], argv[2]
    low = src.lower()
    is_dos = low.endswith(".txt")
    if is_dos:
        with open(src) as fh:
            first = fh.readline()
        is_dos = first.startswith('"2026","P"') or first.startswith("2026,P")
    if is_dos:
        rows = parse_dos_precinct_returns(src, out_csv)
        print(f"precinct rows: {len(rows)} precincts: {len({r['precinct'] for r in rows})}")
    else:
        rows = parse_county_summary(src, out_csv)
        print(f"county rows: {len(rows)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
