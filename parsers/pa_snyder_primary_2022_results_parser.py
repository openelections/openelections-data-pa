#!/usr/bin/env python3
"""Snyder County, PA 2022 Primary — precinct-level results parser.

Source situation (documented):
  * Snyder's two county PDFs in
    ~/code/openelections-sources-pa/2022/primary/ are BOTH Electionware
    "Primary Election - Recount" reports generated 06/02/2022:
      - "Snyder PA 2022 Primary Precinct Summary.pdf" (25 pages = 25
        precincts) carries ONE contest only: REP U.S. Senator, with
        Election Day / Mail Votes / Provisional breakdowns and a
        Write-In Totals row per precinct.  Text extract kept at
        work2022p/snyder/precinct.txt.
      - "Snyder PA 2022 Primary Election Summary.pdf" is the countywide
        (not per-precinct) summary of the same recount (text extract
        work2022p/snyder/summary.txt).
  * Every other 2022-primary contest (DEM contests, REP Governor, Lt.
    Governor, U.S. House 15, State House 85) has no per-precinct county
    source, so those come from the PA Department of State official
    certified precinct-returns bulk file:
        ElectionReturns_2022_Primary_PrecinctReturns.txt
    (county code 55 = Snyder; 875 rows = 25 precincts x 35 named-candidate
    contest rows).  Snyder slice kept at work2022p/snyder/dos_all.txt.

Recount handling: the county precinct PDF is POST-recount.  Its per-precinct
values (e.g. Adams Township: Barnette 61/45 Oz/1 Bochetto/8 Bartos/59
McCormick/0 Gale/8 Sands) equal the DOS bulk file rows exactly, and its
county totals (Barnette 1,805; Oz 1,618; Bochetto 38; Bartos 301;
McCormick 2,221; Gale 51; Sands 449; Write-Ins 33; cast 6,516) equal both
the countywide Election Summary and the ENR anchor
2022/20220517__pa__primary__county.csv — so the recount is fully
incorporated and no amendment merge was needed.

Inherent to the sources (documented, not parsing losses):
  * The DOS bulk file has no per-candidate breakdown -> non-Senate contest
    rows carry empty election_day / mail / provisional cells (Clearfield
    2022-primary precedent).  The GOP U.S. Senate rows carry the county
    report's own breakdown.
  * The DOS bulk file has no write-in rows; per-precinct write-ins are only
    available for the GOP U.S. Senate contest ("Write-In Totals" row of the
    county recount PDF) — emitted, tagged party REP.
  * No per-precinct Registered Voters / Ballots Cast in either source (the
    recount precinct report carries no STATISTICS blocks) — none emitted.
  * The Member of Republican State Committee contest (Kantz / Saylor) is
    countywide in every available source (the DOS bulk file does not carry
    State Committee at all; the only county PDFs are recount-only) and
    cannot be assigned to precincts — no rows emitted (Fulton precedent,
    documented omission).

Usage:
    uv run python parsers/pa_snyder_primary_2022_results_parser.py \
        <dos_prcres_txt> <out_csv> [county_precinct_pdf_text] [--verify]

Defaults: <dos_prcres_txt> = work2022p/snyder/dos_all.txt
          [county_precinct_pdf_text] = work2022p/snyder/precinct.txt
"""
from __future__ import annotations

import csv
import os
import re
import sys

COUNTY_NAME = "Snyder"
COUNTY_CODE = "55"  # PA DOS county code
ELECTION_TYPE = "P"

# 10-column primary header (GOP U.S. Senate carries the county report's
# breakdown; DOS-sourced contests leave the three cells empty).
HEADER = ["county", "precinct", "office", "district", "party", "candidate",
          "votes", "election_day", "mail", "provisional"]

OFFICES = {  # DOS office code -> (standard office, district source field)
    "USS": ("U.S. Senate", None),
    "GOV": ("Governor", None),
    "LTG": ("Lieutenant Governor", None),
    "USC": ("U.S. House", "uscd"),
    "STH": ("State House", "shd"),
}
OFFICE_ORDER = {o: i for i, o in enumerate(
    ["U.S. Senate", "Governor", "Lieutenant Governor", "U.S. House",
     "State House"])}

# DOS candidate (Last, First, Middle) -> printed name (mixed case, the same
# statewide candidates appear in the Cameron/Fulton 2022-primary files).
NAME_FIX = {
    ("KHALIL", "ALEXANDRIA", "GLORIA"): "Alex Khalil",
    ("LAMB", "CONOR", "J"): "Conor Lamb",
    ("KENYATTA", "MALCOLM", ""): "Malcolm Kenyatta",
    ("FETTERMAN", "JOHN", "K"): "John Fetterman",
    ("BARNETTE", "KATHY", "J"): "Kathy Barnette",
    ("OZ", "MEHMET", "C"): "Mehmet Oz",
    ("BOCHETTO", "GEORGE", "A"): "George Bochetto",
    ("BARTOS", "JEFFREY", "A"): "Jeff Bartos",
    ("MCCORMICK", "DAVID", "H"): "Dave McCormick",
    ("GALE", "SEAN", "PETER"): "Sean Gale",
    ("SANDS", "CARLA", "HERD"): "Carla Sands",
    ("SHAPIRO", "JOSHUA", "D"): "Josh Shapiro",
    ("BARLETTA", "LOUIS", ""): "Lou Barletta",
    ("MASTRIANO", "DOUGLAS", "V"): "Douglas V Mastriano",
    ("ZAMA", "NCHE", ""): "Nche Zama",
    ("WHITE", "DAVID", "J"): "Dave White",
    ("HART", "MELISSA", "A"): "Melissa Hart",
    ("MCSWAIN", "WILLIAM", "M"): "Bill McSwain",
    ("GEROW", "CHARLES", "R"): "Charlie Gerow",
    ("GALE", "JOSEPH", "CHARLES"): "Joe Gale",
    ("CORMAN", "JACOB", "DOYLE"): "Jake Corman",
    ("DAVIS", "AUSTIN", ""): "Austin Davis",
    ("SOSA", "RAYMOND", "L"): "Ray Sosa",
    ("SIMS", "BRIAN", ""): "Brian Sims",
    ("SCHILLINGER", "CLARICE", "D"): "Clarice Schillinger",
    ("JONES", "JAMES", "E"): "James Earl Jones",
    ("SACCONE", "RICHARD", ""): "Rick Saccone",
    ("BROWN", "JOHN", "A"): "John Brown",
    ("FRYE", "CHRISTOPHER", "C"): "Chris Frye",
    ("COLEMAN", "JEFFREY", "H."): "Jeff Coleman",
    ("DIAMOND", "RUSSELL", "H"): "Russ Diamond",
    ("DELROSSO", "CARRIE", ""): "Carrie Lewis Delrosso",
    ("DANIELS", "THEODORE", ""): "Teddy Daniels",
    ("THOMPSON", "GLENN", "WILLIAM"): "Glenn GT Thompson",
    ("ROWE", "DAVID", "HUMMER"): "David Hummer Rowe",
}

# The county recount PDF's own candidate block / row order for REP US SENATOR
# (1 contest per page; same printed names as the DOS candidate labels).
PDF_US_SENATE_REP = [
    "Kathy Barnette", "Mehmet Oz", "George Bochetto", "Jeff Bartos",
    "Dave McCormick", "Sean Gale", "Carla Sands",
]

# DOS precinct code -> printed precinct name (matches the county's 2022
# general file 20221108__pa__general__precinct.csv rows for Snyder).
PRECINCT_NAMES = {
    "10": "Adams Township",
    "20": "Beaver Township",
    "30": "Beavertown Borough",
    "40": "Center Township",
    "50": "Chapman Township",
    "60": "Franklin Township",
    "70": "Freeburg Borough",
    "80": "Jackson Township",
    "90": "McClure Borough",
    "100": "Middleburg Borough",
    "110": "Middlecreek Township",
    "120": "Monroe Township 1",
    "130": "Monroe Township 2",
    "140": "Penn Township 1",
    "145": "Penn Township 2",
    "150": "Perry Township",
    "160": "Selinsgrove Borough 1",
    "170": "Selinsgrove Borough 2",
    "180": "Selinsgrove Borough 3",
    "200": "Shamokin Dam Borough",
    "210": "Spring Township",
    "220": "Union Township",
    "230": "Washington Township",
    "240": "West Beaver Township",
    "250": "West Perry Township",
}

DEFAULT_DOS = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "work2022p", "snyder", "dos_all.txt")
DEFAULT_PDF_TEXT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "work2022p", "snyder", "precinct.txt")

# Countywide totals printed in "Snyder PA 2022 Primary Election Summary.pdf"
# (recount report; every value equals the ENR anchor for GOP U.S. Senate).
SUMMARY_US_SENATE_REP = {
    "Kathy Barnette": (1805, 1712, 92, 1),
    "Mehmet Oz": (1618, 1527, 90, 1),
    "George Bochetto": (38, 31, 7, 0),
    "Jeff Bartos": (301, 245, 56, 0),
    "Dave McCormick": (2221, 2038, 183, 0),
    "Sean Gale": (51, 41, 10, 0),
    "Carla Sands": (449, 363, 85, 1),
    "Write Ins": (33, 23, 9, 1),
}
SUMMARY_US_SENATE_REP_TOTAL = (6516, 5980, 532, 4)  # printed "Total Votes Cast"

ENR_NAME_FIX = {  # printed name -> ENR anchor formal name (named candidates)
    "Alex Khalil": "Alexandria Gloria Khalil", "Conor Lamb": "Conor J. Lamb",
    "Malcolm Kenyatta": "Malcolm Kenyatta", "John Fetterman": "John K. Fetterman",
    "Kathy Barnette": "Kathy J. Barnette", "Mehmet Oz": "Mehmet C. Oz",
    "George Bochetto": "George A. Bochetto", "Jeff Bartos": "Jeffrey A. Bartos",
    "Dave McCormick": "David H. McCormick", "Sean Gale": "Sean Peter Gale",
    "Carla Sands": "Carla Herd Sands",
    "Josh Shapiro": "Joshua D. Shapiro",
    "Lou Barletta": "Louis Barletta", "Douglas V Mastriano": "Douglas V. Mastriano",
    "Nche Zama": "Nche Zama", "Dave White": "David J. White",
    "Melissa Hart": "Melissa A. Hart", "Bill McSwain": "William M. McSwain",
    "Charlie Gerow": "Charles R. Gerow", "Joe Gale": "Joseph Charles Gale",
    "Jake Corman": "Jacob Doyle Corman III",
    "Austin Davis": "Austin Davis", "Ray Sosa": "Raymond L. Sosa",
    "Brian Sims": "Brian Sims",
    "Clarice Schillinger": "Clarice D. Schillinger",
    "James Earl Jones": "James E. Jones", "Rick Saccone": "Richard Saccone",
    "John Brown": "John A. Brown", "Chris Frye": "Christopher C. Frye Jr",
    "Jeff Coleman": "Jeffrey H. Coleman", "Russ Diamond": "Russell H. Diamond",
    "Carrie Lewis Delrosso": "Carrie Delrosso", "Teddy Daniels": "Theodore Daniels",
    "Glenn GT Thompson": "Glenn William Thompson Jr",
    "David Hummer Rowe": "David Hummer Rowe",
}

INT_RE = None
PRECINCT_HEADER_RE = re.compile(
    r"^[A-Z][A-Z .#()0-9-]*(TOWNSHIP|BOROUGH|CITY)(\s*#\s*\d+)?\s*$")


def dos_rows(path: str) -> list[dict]:
    """Snyder (county 55, election type 'P') rows of the PA DOS prcres file."""
    out = []
    with open(path, newline="", encoding="latin-1") as fh:
        for r in csv.reader(fh):
            if len(r) < 21 or r[:2] != ["2022", ELECTION_TYPE] or r[2] != COUNTY_CODE:
                continue
            if r[8] not in OFFICES:
                raise SystemExit("unexpected DOS office code %r in %s" % (r[8], path))
            key = (r[11], r[12], r[13])
            if key not in NAME_FIX:
                raise SystemExit("unknown DOS candidate %r in %s" % (key, path))
            out.append({
                "precinct_code": r[3],
                "contest_no": int(r[4]),
                "ballot_no": int(r[7]),
                "office_code": r[8],
                "party": r[9],
                "cand_id": r[10],
                "name": NAME_FIX[key],
                "votes": int(r[15]),
                "uscd": r[18],
                "shd": r[20],
            })
    return out


def district_for(r: dict) -> str:
    office, df = OFFICES[r["office_code"]]
    if office in ("U.S. Senate", "Governor", "Lieutenant Governor"):
        return ""
    if office == "U.S. House":
        return str(int(r["uscd"]))
    return str(int(r["shd"]))  # State House


def parse_pdf(text: str) -> dict[str, dict]:
    """Per-precinct REP U.S. Senate blocks from the county recount PDF extract.

    Returns {precinct_header: {"cands": {name: (total, ed, mail, prov)},
    "writeins": (t, ed, mail, prov), "cast": (t, ed, mail, prev)}}.
    """
    result = {}
    current = None
    seen_cast = False

    def flush():
        nonlocal current, seen_cast
        if current is not None:
            if not seen_cast:
                raise SystemExit("precinct %r has no Total Votes Cast row"
                                 % current["header"])
            if len(current["cands"]) != len(PDF_US_SENATE_REP):
                raise SystemExit("precinct %r candidate block incomplete: %r"
                                 % (current["header"], current["cands"]))
            result[current["header"]] = current
        current, seen_cast = None, False

    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("Summary Results Report"):
            flush()
            continue
        if not line:
            continue
        if PRECINCT_HEADER_RE.match(line):
            flush()
            current = {"header": line, "cands": {}}
            continue
        if current is None:
            continue
        toks = " ".join(line.split())
        if toks == "REP US SENATOR":
            continue
        if toks == "Vote For 1":
            continue
        # data rows: label + 4 trailing integer vote counts
        m = re.match(r"^(.+?)\s+([\d,]+)\s+([\d,]+)\s+([\d,]+)\s+([\d,]+)$", toks)
        if not m:
            continue  # column captions, page footers, other non-data lines
        values = tuple(int(num.replace(",", "")) for num in m.groups()[1:])
        label = m.group(1)
        if label.startswith("Write-In Totals"):
            current["writeins"] = values
        elif label == "Total Votes Cast":
            current["cast"] = values
            seen_cast = True
        else:
            if not label.isupper():
                raise SystemExit("unexpected non-candidate row %r" % line)
            cand = " ".join(label.split())
            upper_map = {c.upper(): c for c in PDF_US_SENATE_REP}
            if cand not in upper_map:
                raise SystemExit("unexpected candidate row %r" % line)
            cand = upper_map[cand]
            if cand in current["cands"]:
                raise SystemExit("duplicate candidate row %r at %r"
                                 % (line, current["header"]))
            current["cands"][cand] = values
    flush()
    return result


def verify_pdf(pdf: dict) -> tuple[int, int, list[str]]:
    """Per-precinct internal checks: breakdown sums and cast totals."""
    problems = []
    n = 0
    for header, block in sorted(pdf.items()):
        cands = block["cands"]
        w = block["writeins"]
        cast = block["cast"]
        row_sum = [sum(v[i] for v in cands.values()) + w[i] for i in range(4)]
        if row_sum != list(cast):
            problems.append("%s: candidate rows %r != Total Votes Cast %r"
                            % (header, row_sum, cast))
        for name, v in cands.items():
            n += 1
            if v[1] + v[2] + v[3] != v[0]:
                problems.append("%s / %s: ED %d + mail %d + prov %d != total %d"
                                % (header, name, v[1], v[2], v[3], v[0]))
        if w[1] + w[2] + w[3] != w[0]:
            problems.append("%s / Write Ins: breakdown does not sum: %r"
                            % (header, w))
    return len(pdf), n, problems


def verify_pdf_vs_summary(pdf: dict, problems: list[str]) -> int:
    """Precinct sums vs the countywide Election Summary's printed totals."""
    sums = {}
    for block in pdf.values():
        for name, v in block["cands"].items():
            sums[name] = [(a + b) for a, b in
                          zip(sums.get(name, (0, 0, 0, 0)), v)]
        sums["Write Ins"] = [(a + b) for a, b in zip(sums.get("Write Ins", (0, 0, 0, 0)),
                                                     block["writeins"])]
    ns = 0
    total = (0, 0, 0, 0)
    for name, want in SUMMARY_US_SENATE_REP.items():
        ns += 1
        if tuple(sums.get(name, (0, 0, 0, 0))) != want:
            problems.append("county summary %s: precinct sum %r != printed %r"
                            % (name, sums.get(name), want))
    for block in pdf.values():
        total = tuple(a + b for a, b in zip(total, block["cast"]))
    if total != SUMMARY_US_SENATE_REP_TOTAL:
        problems.append("county summary Total Votes Cast: precinct sum %r != printed %r"
                        % (total, SUMMARY_US_SENATE_REP_TOTAL))
    return ns + 1


def pretty_precinct(header: str) -> str:
    """PDF header -> printed name ("MONROE TOWNSHIP #1" -> "Monroe Township 1")."""
    name = re.sub(r"\s*#\s*(\d+)\s*$", r" \1", header.strip())
    PRECINCT_WORD_FIX = {"Mcclure": "McClure"}
    words = [PRECINCT_WORD_FIX.get(w.capitalize(), w.capitalize())
             for w in name.split()]
    return " ".join(words)


def build_rows(dos: list[dict], pdf: dict) -> tuple[list[dict], list[str]]:
    """Merge: DOS rows for all contests except REP U.S. Senate; the county
    recount PDF supplies that contest (with breakdown) + write-ins."""
    problems = []

    # The PDF's alphabetical precinct order == DOS code order (both 25, the
    # county's certified ballot order); pair them up so the PDF precinct name
    # can be matched to the DOS code independently of the printed label.
    dos_precincts = sorted({r["precinct_code"] for r in dos}, key=int)
    if len(dos_precincts) != len(PRECINCT_NAMES):
        problems.append("DOS precinct count %d != expected %d"
                        % (len(dos_precincts), len(PRECINCT_NAMES)))
    if len(pdf) != len(PRECINCT_NAMES):
        problems.append("PDF precinct count %d != expected %d"
                        % (len(pdf), len(PRECINCT_NAMES)))
    pdf_headers = list(pdf.keys())
    dos_header_by_code: dict[str, str] = {}
    for code, header in zip(dos_precincts, pdf_headers):
        if code not in PRECINCT_NAMES:
            problems.append("DOS code %r not in PRECINCT_NAMES" % code)
            continue
        want = PRECINCT_NAMES[code]
        got = pretty_precinct(header)
        if got != want:
            problems.append("PDF header %r (DOS code %s) -> %r != expected %r"
                            % (header, code, got, want))
        dos_header_by_code[code] = header

    # DOS GOP U.S. Senate rows (used as the ballot-order reference and for
    # the totals cross-check against the county recount PDF).
    dos_senate: dict[str, dict[str, int]] = {}
    senate_order: dict[str, dict[str, int]] = {}
    for r in dos:
        if r["office_code"] == "USS" and r["party"] == "REP":
            dos_senate.setdefault(r["precinct_code"], {})[r["name"]] = r["votes"]
            senate_order.setdefault(r["precinct_code"], {})[r["name"]] = r["ballot_no"]

    rows = []
    for r in dos:
        if r["office_code"] == "USS" and r["party"] == "REP":
            continue  # the county recount PDF supplies this contest
        rows.append({
            "precinct": PRECINCT_NAMES[r["precinct_code"]],
            "precinct_code": int(r["precinct_code"]),
            "office": OFFICES[r["office_code"]][0],
            "district": district_for(r),
            "party": r["party"],
            "candidate": r["name"],
            "votes": r["votes"],
            "election_day": "",
            "mail": "",
            "provisional": "",
            "sort": (int(r["precinct_code"]), r["contest_no"], r["ballot_no"]),
        })

    for code, header in sorted(dos_header_by_code.items(), key=lambda c: int(c[0])):
        block = pdf[header]
        base_name = PRECINCT_NAMES[code]
        for name in PDF_US_SENATE_REP:
            t, ed, mail, prov = block["cands"][name]
            if ed + mail + prov != t:
                problems.append("%s / %s: ED %d + mail %d + prov %d != total %d "
                                "(breakdown does not sum)"
                                % (base_name, name, ed, mail, prov, t))
            dos_total = dos_senate.get(code, {}).get(name)
            if dos_total is None or dos_total != t:
                problems.append("%s / %s: PDF total %r != DOS row %r"
                                % (base_name, name, t, dos_total))
            rows.append({
                "precinct": base_name,
                "precinct_code": int(code),
                "office": "U.S. Senate",
                "district": "",
                "party": "REP",
                "candidate": name,
                "votes": t,
                "election_day": str(ed),
                "mail": str(mail),
                "provisional": str(prov),
                "sort": (int(code), 1, 100 + senate_order.get(code, {}).get(name, 0)),
            })
        w = block["writeins"]
        rows.append({
            "precinct": base_name,
            "precinct_code": int(code),
            "office": "U.S. Senate",
            "district": "",
            "party": "REP",
            "candidate": "Write Ins",
            "votes": w[0],
            "election_day": str(w[1]),
            "mail": str(w[2]),
            "provisional": str(w[3]),
            "sort": (int(code), 1, 199),
        })
    rows.sort(key=lambda r: r["sort"])
    return rows, problems


def verify_dos_vs_pdf_totals(dos: list[dict]) -> tuple[int, int, list[str]]:
    """DOS precinct-row sums vs the countywide Election Summary totals."""
    sums = {}
    for r in dos:
        office, _ = OFFICES[r["office_code"]]
        key = (office, r["party"], r["name"])
        sums[key] = sums.get(key, 0) + r["votes"]
    problems, n = [], 0
    for pname in SUMMARY_US_SENATE_REP:
        if pname == "Write Ins":
            continue  # DOS carries no write-in rows
        got = sums.get(("U.S. Senate", "REP", pname), 0)
        n += 1
        if got != SUMMARY_US_SENATE_REP[pname][0]:
            problems.append("U.S. Senate [REP] %s: DOS precinct sum %d != "
                            "county summary %d"
                            % (pname, got, SUMMARY_US_SENATE_REP[pname][0]))
    return n, 1, problems


def verify_enr(out_rows: list[dict]) -> tuple[int, int, list[str]]:
    """Cross-check per (office, district, party, candidate) vs ENR county
    anchor (named candidates only; no RV/BC/write-ins in the anchor)."""
    enr_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "2022", "20220517__pa__primary__county.csv")
    if not os.path.exists(enr_path):
        return 0, 0, ["ENR anchor not found at %s" % enr_path]
    enr = {}
    with open(enr_path, newline="", encoding="utf-8") as fh:
        for row in csv.reader(fh):
            if row and row[0] == COUNTY_NAME:
                key = (row[1], row[2], row[3], row[4])
                enr[key] = int(row[5])
    mine = {}
    for r in out_rows:
        if r["candidate"] == "Write Ins":
            continue
        key = (r["office"], str(r["district"]), r["party"], r["candidate"])
        mine[key] = mine.get(key, 0) + int(r["votes"])
    problems, n = [], 0
    for key, total in sorted(mine.items()):
        enr_key = (key[0], key[1], key[2], ENR_NAME_FIX.get(key[3], key[3]))
        if enr_key not in enr:
            problems.append("our row %r has no ENR anchor counterpart" % (key,))
            continue
        n += 1
        if enr[enr_key] != total:
            problems.append("%s %s [%s] %s: ours %d != ENR %d"
                            % (key[0], key[1], key[2], key[3], total, enr[enr_key]))
    # anchored contests not covered by ours = the countywide-only State
    # Committee contest (documented omission)
    for key in sorted(enr):
        if key[0] == "Member of Republican State Committee":
            continue
        covered = any(
            (k[0], k[1], k[2], ENR_NAME_FIX.get(k[3], k[3])) == key for k in mine)
        if not covered:
            problems.append("ENR candidate not covered by ours: %s" % (key,))
    return len(mine), len(enr), problems


def main(argv: list[str]) -> int:
    args = [a for a in argv if a != "--verify"]
    dos = args[0] if len(args) > 0 and os.path.exists(args[0]) else DEFAULT_DOS
    out_csv = args[1] if len(args) > 1 else None
    pdf_text = args[2] if len(args) > 2 else DEFAULT_PDF_TEXT
    if not os.path.exists(dos):
        print("Snyder 2022 primary DOS source not found (tried %r)" % dos,
              file=sys.stderr)
        return 1
    rows = dos_rows(dos)
    if not rows:
        print("No Snyder primary rows found in %s" % dos, file=sys.stderr)
        return 1
    missing = sorted({r["precinct_code"] for r in rows} - set(PRECINCT_NAMES))
    if missing:
        print("Unknown DOS precinct codes: %s" % missing, file=sys.stderr)
        return 1
    if not os.path.exists(pdf_text):
        print("Snyder county recount precinct text not found (tried %r)" %
              pdf_text, file=sys.stderr)
        return 1
    pdf = parse_pdf(open(pdf_text, encoding="utf-8", errors="replace").read())
    npdf, ncand, pdf_problems = verify_pdf(pdf)
    totals_checked = verify_pdf_vs_summary(pdf, pdf_problems)
    n_dos, _, dos_problems = verify_dos_vs_pdf_totals(rows)
    out_rows, merge_problems = build_rows(rows, pdf)

    problems = pdf_problems + dos_problems + merge_problems
    print("verify: %d precinct REP U.S. Senate blocks parsed (%d candidate "
          "breakdown rows checked)" % (npdf, ncand))
    print("verify: %d county-summary totals checked (incl. write-ins and "
          "Total Votes Cast 6,516)" % totals_checked)
    print("verify: %d DOS candidate sums checked against the county summary" % n_dos)

    if "--verify" in argv:
        nm, ne, enr_problems = verify_enr(out_rows)
        print("verify: %d of %d ENR anchor candidates cross-checked"
              % (nm, ne - 2))
        problems += enr_problems

    if problems:
        for p in problems:
            print("  MISMATCH " + str(p))
        return 1

    if out_csv:
        with open(out_csv, "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(HEADER)
            for r in out_rows:
                w.writerow([r["county"] if "county" in r else COUNTY_NAME,
                            r["precinct"], r["office"], r["district"],
                            r["party"], r["candidate"], r["votes"],
                            r["election_day"], r["mail"], r["provisional"]])
        print("wrote %s (%d data rows)" % (out_csv, len(out_rows)))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))