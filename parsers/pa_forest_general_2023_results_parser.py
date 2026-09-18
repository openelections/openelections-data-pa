#!/usr/bin/env python3
"""Forest County, PA 2023 General — precinct results parser.

Source: "Forest County Official Results 2023 General.pdf" — a scanned
county-compiled per-precinct report. Recovered via PaddleOCR-VL
(work2023/txt/Forest__ocr.txt); this parser consumes the OCR markdown
(tables) or the raw PDF passed as input (it re-runs nothing; pass the
.txt extract).

Layout: one HTML table per contest; header row = blank | 9 precinct
columns | Totals; candidate rows carry per-precinct machine totals.
"SCATTERED" rows are write-ins. Retention questions appear as
"RETENTION QUESTION #1/#2" (unnamed — ballot positions correspond to
the statewide Superior/Commonwealth retentions; corroborated by the
~62%/65% Yes margins other counties report for Panella/Stabile).

No Registered Voters / Ballots Cast rows and no party labels exist in
the source. Parties for the statewide slate are filled from the same
election's other counties (Berks/Adams official results); local
candidates' parties are left empty — the source does not report them.

Usage: python parsers/pa_forest_general_2023_results_parser.py <ocr.txt> <out.csv>
"""
from __future__ import annotations

import csv
import re
import sys

COUNTY = "Forest"

# OCR column headers -> precinct names (Forest County roster, 9 precincts).
PRECS = [
    ("Barnett", "Barnett Township"),
    ("Green", "Green Township"),
    ("Harmony", "Harmony Township"),
    ("Hickory", "Hickory Township"),
    ("Howe", "Howe Township"),
    ("Jenks", "Jenks Township"),
    ("Kingsley", "Kingsley Township"),
    ("Tionesta", "Tionesta Township"),
    ("Borough", "Tionesta Borough"),
]

# Statewide 2023 slate parties (from other counties' official results).
STATEWIDE_PARTY = {
    "DANIEL MCCAFFERY": "DEM",
    "CAROLYN CARLUCCIO": "REP",
    "JILL BECK": "DEM",
    "TIMIKA LANE": "DEM",
    "MARIA BATTISTA": "REP",
    "HARRY F. SMAIL": "REP",
    "MATT WOLF": "DEM",
    "MEGAN MARTIN": "REP",
    "JACK PANELLA": "",
    "VICTOR P. STABILE": "",
}

OFFICE_MAP = {
    "JUSTICE OF THE SUPREME COURT": ("Justice of the Supreme Court", ""),
    "JUDGE OF THE SUPERIOR COURT": ("Judge of the Superior Court", ""),
    "JUDGE OF THE COMMONWEALTH COURT": ("Judge of the Commonwealth Court", ""),
    "DISTRICT ATTORNEY": ("District Attorney", ""),
    "COUNTY CORONER": ("Coroner", ""),
    "RETENTION QUESTION #1": ("Superior Court Retention - Jack Panella", ""),
    "RETENTION QUESTION #2": ("Superior Court Retention - Victor P. Stabile", ""),
}


def map_office(header: str):
    h = " ".join(header.split())
    if h in OFFICE_MAP:
        return OFFICE_MAP[h]
    m = re.match(r"SCHOOL DIRECTOR REGION ([A-Z]) (\d) YEAR TERM$", h, re.I)
    if m:
        return (f"School Director Region {m.group(1)} ({m.group(2)} Year)", "")
    m = re.match(r"TOWNSHIP SUPERVISOR (\d) YEAR TERM$", h, re.I)
    if m:
        # Each township votes for its own supervisors; candidate rows show
        # which precinct they ran in (votes in that column only).
        return (f"Township Supervisor ({m.group(1)} Year)", "*MUNI*")
    m = re.match(r"TOWNSHIP AUDITOR (\d) YEAR TERM$", h, re.I)
    if m:
        return (f"Township Auditor ({m.group(1)} Year)", "*MUNI*")
    m = re.match(r"BOROUGH COUNCIL (\d) YEAR TERM$", h, re.I)
    if m:
        return (f"Borough Council ({m.group(1)} Year)", "*MUNI*")
    m = re.match(r"TAX COLLECTOR (\d) YEAR TERM$", h, re.I)
    if m:
        return (f"Tax Collector ({m.group(1)} Year)", "*MUNI*")
    m = re.match(r"CONSTABLE (\d) YEAR TERM$", h, re.I)
    if m:
        return (f"Constable ({m.group(1)} Year)", "*MUNI*")
    return (" ".join(w.capitalize() for w in h.split()), "")


def title_case(name: str) -> str:
    out = []
    for i, w in enumerate(name.split()):
        if re.match(r"^[IVX]+$", w.upper()):
            out.append(w.upper())
        elif i and w.lower() in {"of", "the", "and", "for"}:
            out.append(w.lower())
        else:
            out.append(w.capitalize())
    s = " ".join(out)
    s = re.sub(r"\bMc([a-z])", lambda m: "Mc" + m.group(1).upper(), s)
    s = re.sub(r"\bO'([a-z])", lambda m: "O'" + m.group(1).upper(), s)
    s = re.sub(r"(?<=-)([a-z])", lambda m: m.group(1).upper(), s)  # hyphenated
    return s


# surname capitalization OCR/case fixes
NAME_FIX = {
    "Daniel Mccaffery": "Daniel McCaffery",
    "Carolyn Carluccio": "Carolyn Carluccio",
    "Tammy O'Rourke-Thompson": "Tammy O'Rourke-Thompson",
    "Harry Smail, Jr.": "Harry F. Smail Jr.",  # official ballot name
}


def parse(path: str):
    import html as htmllib
    src = open(path, encoding="utf-8").read()
    tables = re.findall(r"<table.*?</table>", src, re.S)
    rows = []
    for t in tables:
        trs = re.findall(r"<tr>(.*?)</tr>", t, re.S)
        office = mode = None
        for tr in trs:
            cells = [htmllib.unescape(re.sub(r"<[^>]+>", "", c)).strip()
                     for c in re.findall(r"<td[^>]*>(.*?)</td>", tr, re.S)]
            if len(cells) == 1:
                # header-ish row: contest title, merged OCR precinct header,
                # or a blank spacer row
                txt = cells[0]
                if not txt:
                    continue
                # merged OCR precinct-header row ("Barnett Green ... Totals")
                if "Totals" in txt and "Barnett" in txt:
                    continue
                office, mode = map_office(txt)
                continue
            # candidate row: name + 9 precinct columns + Totals
            if office is None or len(cells) < 11:
                continue
            name = cells[0]
            if not name or name.lower().startswith("final results"):
                continue
            if name.upper() in ("SCATTERED", "WRITE-IN", "WRITE-IN SCATTERED"):
                candidate, party = "Write-ins", ""
            elif name.upper() in ("YES", "NO"):
                candidate, party = name.capitalize(), ""
            else:
                candidate = title_case(name)
                candidate = NAME_FIX.get(candidate, candidate)
                party = ""
                for key, p in STATEWIDE_PARTY.items():
                    if candidate.upper().startswith(key.upper()):
                        party = p
                        break
            vals = cells[1:11]  # 9 precincts + Totals
            # OCR occasionally dropped the single non-empty precinct cell
            # (Tionesta Borough column) while keeping Totals; restore it.
            if all(not v.strip() for v in vals[:9]) and vals[9].strip():
                vals[8] = vals[9]
            if mode == "*MUNI*":
                # municipal office: emit only precincts with a value
                for (col, prec), v in zip(PRECS, vals[:9]):
                    if v.strip():
                        rows.append([COUNTY, prec, office, "", party,
                                     candidate, v, "", "", ""])
            else:
                for (col, prec), v in zip(PRECS, vals[:9]):
                    rows.append([COUNTY, prec, office, "", party,
                                 candidate, v or "0", "", "", ""])
    # de-dup identical (precinct, office, candidate) rows
    seen, out = set(), []
    for r in rows:
        key = tuple(r[:6])
        if key in seen:
            continue
        seen.add(key)
        out.append(r)
    return out


def main() -> None:
    inp, out = sys.argv[1], sys.argv[2]
    rows = parse(inp)
    with open(out, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["county", "precinct", "office", "district", "party",
                    "candidate", "votes", "election_day", "mail", "provisional"])
        w.writerows(rows)
    print(f"{COUNTY}: wrote {len(rows)} rows -> {out}")


if __name__ == "__main__":
    main()