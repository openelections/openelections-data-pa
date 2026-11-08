#!/usr/bin/env python3
"""DOS-fallback precinct parser for four 2026-primary counties — Bradford,
Columbia, Perry, Schuylkill.

Source situation (documented, per the pa_snyder_primary_2022_results_parser
write-up style):

  * Each county's own 2026-primary source is a COUNTYWIDE-ONLY summary PDF.
    The four countywide CSVs in 2026/counties/ (Bradford, Columbia,
    Schuylkill from JasperReports "Total votes" tables transcribed via
    parsers/pa_bradford_columbia_primary_2026_county_parser.py and friends;
    Perry from its SOVC "All Precincts" summary via
    parsers/pa_perry_primary_2026_results_parser.py) carry contest totals but
    no per-precinct sections.

  * Precinct-level figures therefore come from the PA Department of State
    official certified precinct-returns bulk file for the 2026 primary:
        PA DOS 2026 General Primary Certified Precinct Returns Statewide.txt
    (~/code/openelections-sources-pa/2026/primary/; ASCII comma-delimited,
    quoted fields; election type "P").  The statewide file was split per
    county; verbatim county slices (md5-checked greps) are kept at
        work2026p/dos/<county>__dos_precinctreturns.txt
    with county codes 8=Bradford, 19=Columbia, 50=Perry, 54=Schuylkill:
        bradford:   549 rows = 61 precincts x 9 named-candidate rows
        columbia:   378 rows = 42 precincts x 9 named-candidate rows
        perry:      372 rows = 31 precincts x 12 named-candidate rows
        schuylkill: 1107 rows = 125 precincts x 9 rows (HD 116 carries no
                    named DEM candidate, so its 18 precincts have 8 rows)

    2026 field layout (0-indexed; shifted vs the 2022 Clarion-style parser):
    0=year, 1=election type, 2=county code, 3=precinct code, 4=office rank,
    5=contest district (0 for GOV/LTG; CD for USC; SD for STS; HD for STH),
    6=ballot position family, 7=ballot position, 8=office code
    (GOV/LTG/USC/STS/STH), 9=party, 10=candidate number (2026Cxxxx),
    11-14=candidate last/first/middle/suffix, 15=vote total, 21=municipality
    type (2=City, 4=Township, 5=Town, 6=Borough), 22=precinct name, 23=
    precinct qualifier (W/D/P/X), 24=precinct split.

DOS quirks inherent to this bulk source (2022/2024 precedent, all documented,
not parsing losses):
  * No write-in rows — the countywide sources' Write-In Totals / cross-party
    write-in rows (e.g. Bradford county CSV's Governor-DEM Stacy Garrity 38,
    Mastriano 33, Shapiro 107; Perry's full Overvotes/Undervotes/Write-In
    detail; Schuylkill's 111 DEM / 418 REP Governor write-ins) cannot be
    assigned to precincts.  Per the no-invention rule no Write-In rows are
    emitted.
  * No Registered Voters / Ballots Cast rows — none emitted (7-column
    header: county,precinct,office,district,party,candidate,votes).
  * No per-candidate vote breakdowns (election day / absentee / mail-in /
    provisional) — breakdown columns left out entirely.
  * No State Committee — the contests "Member of Democratic State Committee"
    / "Member of Republican State Committee" present in all four countywide
    CSVs and the statewide county anchor are absent from the DOS precinct
    bulk file, so they have no per-precinct rows (Clarion 2022 /
    Bedford-Cameron 2026 precedent).  Omitted rows are itemized in the
    --verify "not covered" report rather than asserted away, since the
    anchor itself carries them.
  * Contest coverage is what the DOS file reports per county —
    Bradford/Columbia: Governor, Lieutenant Governor, U.S. House 9, State
    House 68/110 (Bradford) and 109 (Columbia); Perry adds State Senate 34
    and State House 86; Schuylkill: Governor, Lieutenant Governor, U.S.
    House 9, State House 107/116/123/124 (no State Senate — SD 29 was not
    up in 2026; neither countywide source prints one).

Precinct naming (each county follows its own repo lineage):
  * Bradford — title case, "Athens Borough 1st Ward" / "Athens Township
    1st District" wording, exactly the 2022/2024-primary precinct list
    (all 61 units 1:1 with 2022/2024 files).
  * Columbia — ALLCAPS county style ("BENTON TWP", "BERWICK 1ST WARD",
    "BLOOMSBURG 3-1 WARD", "BRIARCREEK TWP NORTHEAST", "SCOTT TWP EAST");
    all 42 units 1:1 with the 2022-primary file (including "BRIARCREEK
    BORO", spelled exactly as the county's own files spell it).
  * Perry — ALLCAPS county style ("BLAIN BOROUGH", "TOBOYNE TOWNSHIP 1ST").
    One structural difference vs the county's 2022/2024 files: DOS 2026
    lists Madison Township as two units, NORTH EAST MADISON and SOUTH WEST
    MADISON, where the 2022/2024 lists carry "MADISON TOWNSHIP" plus a
    separate "SANDY HILL DISTRICT"; the DOS 2026 precinct set is
    authoritative for this file, so the two DOS units are emitted as
    "NORTH EAST MADISON TOWNSHIP" / "SOUTH WEST MADISON TOWNSHIP" and the
    old names are not reused.  All other 29 units 1:1 with 2022/2024 files.
  * Schuylkill — county style ("Ashland 1st", "Barry Twp.", "Butler Twp. -
    Englewood", "Pottsville 3-1"); the generated set equals the 2022
    primary's 125-name set exactly (verified by set comparison), i.e. the
    tabulation structure carries over unchanged.

One shared parser is used rather than four per-county scripts because the
DOS handling is identical and only precinct names / county codes differ;
the county is a required first argument.  Nothing is registered in
oepa/registry.py (the coordinator updates it at the end).

Verification (--verify): sums per (office, party, last name) of the emitted
rows are compared (case-insensitive, last-name keyed, per the brief) against
BOTH (a) the statewide county anchor 2026/20260519__pa__primary__county.csv
and (b) the county's own countywide CSV 2026/counties/<name>__precinct...
__county.csv.  Anchor rows for contests the DOS file does not carry (State
Committee) and countywide rows that are source-detail only (write-ins,
cross-party write-ins, Overvotes/Undervotes) are reported as "not covered",
not asserted equal.

Usage:
    uv run python parsers/pa_dos_primary_2026_results_parser.py <county> <out_csv>
    uv run python parsers/pa_dos_primary_2026_results_parser.py <county> <out_csv> --verify

  <county> = bradford | columbia | perry | schuylkill (lowercase)
  <out_csv> defaults to
  2026/counties/20260519__pa__primary__<county>__precinct.csv
"""
from __future__ import annotations

import csv
import os
import re
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
YEAR = "2026"
ELECTION_TYPE = "P"

HEADER = ["county", "precinct", "office", "district", "party", "candidate",
          "votes"]
OFFICE_ORDER = ["Governor", "Lieutenant Governor", "U.S. House", "State Senate",
                "State House"]

OFFICES = {  # DOS office code -> office
    "GOV": "Governor",
    "LTG": "Lieutenant Governor",
    "USC": "U.S. House",
    "STS": "State Senate",
    "STH": "State House",
}

# DOS candidate (Last, First, Middle, Suffix) -> printed name, matching the
# counties' countywide CSVs and the statewide county anchor.
NAME_FIX = {
    ("SHAPIRO", "JOSHUA", "D", ""): "Josh Shapiro",
    ("GARRITY", "STACY", "LORRAINE", ""): "Stacy Garrity",
    ("DAVIS", "AUSTIN", "", ""): "Austin Davis",
    ("VENTRE", "JOHN", "", ""): "John Ventre",
    ("RICHEY", "JASON", "L", ""): "Jason Richey",
    ("WALLACE", "RACHEL", "LILLIAN", ""): "Rachel Wallace",
    ("MEUSER", "DANIEL", "PHILIP", ""): "Dan Meuser",
    ("FARNHAM", "ELIZABETH", "RHOADS", ""): "Beth Farnham",
    ("JOYCE", "JOHN", "", ""): "John Joyce",
    ("FORSMAN", "RICHARD", "J", "II"): "Rich Forsman",
    ("WOOD", "NATHAN", "", ""): "Nathan Wood",
    ("ROTHMAN", "WILLIAM", "GREGORY", ""): "Greg Rothman",
    ("GEE", "QUENTIN", "JOSEPH", ""): "Quentin Gee",
    ("OWLETT", "CLINTON", "D", ""): "Clint Owlett",
    ("HEMANN", "PAMELA", "J", ""): "Pamela J Hemann",
    ("PICKETT", "TINA", "", ""): "Tina Pickett",
    ("LEADBETER", "ROBERT", "MEDIN", "II"): "Robert Leadbeter",
    ("HUMMEL", "JUSTIN", "", ""): "Justin C. Hummel",
    ("MORGAN", "WENDY", "", ""): "Wendy Morgan",
    ("STAMBAUGH", "PERRY", "A", ""): "Perry Stambaugh",
    ("BURNS", "TINA", "M", ""): "Tina Burns",
    ("COLEMAN", "RYAN", "T", ""): "Ryan T. Coleman",
    ("DEATRICH", "JOHN", "A", "JR"): "John Deatrich",
    ("BARTON", "JAMES", "A", ""): "Jamie Barton",
    ("STEHR", "JOANNE", "C", ""): "Joanne Stehr",
    ("TWARDZIK", "TIMOTHY", "F", ""): "Tim Twardzik",
    ("WATRO", "DANE", "JEFFREY", "JR."): "Dane Watro",
}

# ---------------------------------------------------------------------------
# Bradford: 61 units, title case (matches the 2022/2024-primary lineage)
BRADFORD_NAMES = {
    "10": "Alba Borough", "20": "Albany Township", "30": "Armenia Township",
    "40": "Asylum Township", "50": "Athens Township 1st District",
    "60": "Athens Township 2nd District", "70": "Athens Borough 1st Ward",
    "80": "Athens Borough 2nd Ward", "90": "Athens Borough 3rd Ward",
    "100": "Athens Borough 4th Ward", "110": "Burlington Township",
    "120": "Burlington Borough", "130": "Canton Township",
    "140": "Canton Borough", "150": "Columbia Township",
    "160": "Franklin Township", "170": "Granville Township",
    "180": "Herrick Township", "190": "Leraysville Borough",
    "200": "Leroy Township", "210": "Litchfield Township",
    "220": "Monroe Township", "230": "Monroe Borough",
    "240": "New Albany Borough", "250": "North Towanda Township",
    "260": "Orwell Township", "270": "Overton Township", "280": "Pike Township",
    "290": "Ridgebury Township", "300": "Rome Township", "310": "Rome Borough",
    "320": "Sayre Borough 1st Ward", "330": "Sayre Borough 2nd Ward",
    "340": "Sayre Borough 3rd Ward", "350": "Sayre Borough 4th Ward",
    "360": "Sayre Borough 5th Ward", "370": "Sheshequin Township",
    "380": "Smithfield Township", "390": "South Creek Township",
    "400": "South Waverly Borough", "410": "Springfield Township",
    "420": "Standing Stone Township", "430": "Stevens Township",
    "440": "Sylvania Borough", "450": "Terry Township",
    "460": "Towanda Township", "470": "Towanda Borough 1st Ward",
    "480": "Towanda Borough 2nd Ward", "490": "Towanda Borough 3rd Ward",
    "500": "Troy Township", "510": "Troy Borough", "520": "Tuscarora Township",
    "530": "Ulster Township", "540": "Warren Township", "550": "Wells Township",
    "560": "West Burlington Township", "570": "Wilmot Township",
    "580": "Windham Township", "590": "Wyalusing Township",
    "600": "Wyalusing Borough", "610": "Wysox Township",
}

# ---------------------------------------------------------------------------
# Columbia: 42 units, ALLCAPS county style (matches the 2022-primary lineage,
# including "BRIARCREEK BORO" as that file spells it)
COLUMBIA_NAMES = {
    "10": "BEAVER TWP", "20": "BENTON TWP", "30": "BENTON BOROUGH",
    "55": "BERWICK 1ST WARD", "70": "BERWICK 2ND WARD",
    "85": "BERWICK 3RD WARD", "105": "BERWICK 4TH WARD",
    "120": "BLOOMSBURG 1ST WARD", "130": "BLOOMSBURG 2ND WARD",
    "140": "BLOOMSBURG 3-1 WARD", "150": "BLOOMSBURG 3-2 WARD",
    "170": "BLOOMSBURG 4TH WARD", "210": "BRIARCREEK TWP NORTHEAST",
    "230": "BRIARCREEK TWP WEST", "240": "BRIARCREEK BORO",
    "250": "CATAWISSA TWP", "265": "CATAWISSA BOROUGH",
    "285": "CENTRALIA BOROUGH", "300": "CLEVELAND TWP",
    "320": "CONYNGHAM TWP", "345": "FISHINGCREEK TWP", "360": "FRANKLIN TWP",
    "375": "GREENWOOD TWP", "395": "HEMLOCK TWP", "410": "JACKSON TWP",
    "425": "LOCUST TWP", "440": "MADISON TWP", "450": "MAIN TWP",
    "460": "MIFFLIN TWP", "470": "MILLVILLE BOROUGH", "480": "MONTOUR TWP",
    "490": "MOUNT PLEASANT TWP", "500": "NORTH CENTRE TWP", "510": "ORANGE TWP",
    "520": "ORANGEVILLE BOROUGH", "535": "PINE TWP", "550": "ROARINGCREEK TWP",
    "565": "SCOTT TWP EAST", "585": "SCOTT TWP WEST", "600": "SOUTH CENTRE TWP",
    "610": "STILLWATER BOROUGH", "620": "SUGARLOAF TWP",
}

# ---------------------------------------------------------------------------
# Perry: 31 units, ALLCAPS county style.  DOS 2026 splits Madison Township
# into NORTH EAST / SOUTH WEST MADISON (the county's 2022/2024 lists carry
# "MADISON TOWNSHIP" + "SANDY HILL DISTRICT" instead).
PERRY_NAMES = {
    "10": "BLAIN BOROUGH", "20": "BLOOMFIELD BOROUGH",
    "30": "BUFFALO TOWNSHIP", "40": "CARROLL TOWNSHIP",
    "50": "CENTRE TOWNSHIP", "60": "DUNCANNON BOROUGH",
    "80": "GREENWOOD TOWNSHIP", "90": "HOWE TOWNSHIP",
    "100": "JACKSON TOWNSHIP", "110": "JUNIATA TOWNSHIP",
    "120": "LANDISBURG BOROUGH", "130": "LIVERPOOL TOWNSHIP",
    "140": "LIVERPOOL BOROUGH", "150": "MARYSVILLE BOROUGH",
    "160": "MILLER TOWNSHIP", "170": "MILLERSTOWN BOROUGH",
    "180": "NEW BUFFALO BOROUGH", "190": "NEWPORT BOROUGH",
    "210": "NORTH EAST MADISON TOWNSHIP", "225": "OLIVER TOWNSHIP",
    "230": "PENN TOWNSHIP", "240": "RYE TOWNSHIP", "250": "SAVILLE TOWNSHIP",
    "260": "SOUTH WEST MADISON TOWNSHIP", "270": "SPRING TOWNSHIP",
    "280": "TOBOYNE TOWNSHIP 1ST", "290": "TOBOYNE TOWNSHIP 2ND",
    "300": "TUSCARORA TOWNSHIP", "310": "TYRONE TOWNSHIP",
    "320": "WATTS TOWNSHIP", "330": "WHEATFIELD TOWNSHIP",
}

# ---------------------------------------------------------------------------
# Schuylkill: 125 units, county style ("Ashland 1st", "Barry Twp.",
# "Butler Twp. - Englewood", "Pottsville 3-1"); set-identical to the
# 2022-primary precinct list.
SCHUYLKILL_NAMES = {
    "10": "Ashland 1st", "20": "Ashland 2nd", "60": "Auburn",
    "70": "Barry Twp.", "80": "Blythe Twp.", "100": "Branch Twp.",
    "120": "Butler Twp. - Englewood", "135": "Butler Twp. - Fountain Springs",
    "155": "Butler Twp. - Lavelle", "160": "Butler Twp. - Northeast",
    "170": "Cass Twp. - North", "180": "Cass Twp. - South",
    "200": "Coaldale - East", "220": "Coaldale - West", "230": "Cressona",
    "250": "Deer Lake", "260": "Delano Twp.", "270": "East Brunswick Twp.",
    "280": "East Norwegian Twp.", "300": "East Union Twp.",
    "330": "Eldred Twp.", "340": "Foster Twp.", "355": "Frackville - Middle",
    "375": "Frackville - North", "380": "Frackville - South",
    "400": "Frailey Twp.", "410": "Gilberton", "430": "Girardville",
    "460": "Gordon", "470": "Hegins Twp. - East", "480": "Hegins Twp. - West",
    "490": "Hubley Twp.", "500": "Kline Twp.", "520": "Landingville",
    "530": "Mahanoy Twp.", "550": "Mahanoy City 1st",
    "560": "Mahanoy City 2nd", "570": "Mahanoy City 3rd",
    "580": "Mahanoy City 4th", "625": "McAdoo 1st", "645": "McAdoo 2nd",
    "660": "Mechanicsville", "670": "Middleport", "695": "Minersville 1st",
    "705": "Minersville 2nd", "715": "Minersville 3rd",
    "725": "Minersville 4th", "740": "Mount Carbon", "750": "New Castle Twp.",
    "765": "New Philadelphia", "780": "New Ringgold",
    "790": "North Manheim Twp. 1st", "800": "North Manheim Twp. 2nd",
    "810": "North Manheim Twp. 3rd", "815": "North Union Twp.",
    "830": "Norwegian Twp. - Currans", "840": "Norwegian Twp. - Marlin",
    "850": "Orwigsburg 1st", "860": "Orwigsburg 2nd", "870": "Palo Alto 1st",
    "880": "Palo Alto 2nd", "890": "Pine Grove Twp. 1st",
    "900": "Pine Grove Twp. 2nd", "915": "Pine Grove - North",
    "930": "Pine Grove - South", "940": "Port Carbon 1st",
    "950": "Port Carbon 2nd", "960": "Port Clinton",
    "970": "Porter Twp. - East", "980": "Porter Twp. - West",
    "990": "Pottsville 1st", "1010": "Pottsville 2nd",
    "1030": "Pottsville 3-1", "1040": "Pottsville 3-2",
    "1050": "Pottsville 3-3", "1070": "Pottsville 4th",
    "1100": "Pottsville 5-1", "1110": "Pottsville 5-2",
    "1140": "Pottsville 6th", "1160": "Pottsville 7-1",
    "1170": "Pottsville 7-2", "1200": "Reilly Twp.", "1210": "Ringtown",
    "1220": "Rush Twp. - East", "1230": "Rush Twp. - Elixir",
    "1250": "Rush Twp. - West", "1260": "Ryan Twp.", "1270": "Schuylkill Twp.",
    "1290": "Schuylkill Haven - East", "1300": "Schuylkill Haven - North",
    "1310": "Schuylkill Haven - South", "1320": "Schuylkill Haven - West",
    "1330": "Shenandoah 1st", "1340": "Shenandoah 2nd",
    "1350": "Shenandoah 3rd", "1360": "Shenandoah 4th",
    "1370": "Shenandoah 5th", "1380": "Shenandoah 6th",
    "1440": "South Manheim Twp.", "1450": "St. Clair - Middle",
    "1460": "St. Clair - North", "1470": "St. Clair - South",
    "1520": "Tamaqua 1st", "1530": "Tamaqua 2nd", "1540": "Tamaqua 3rd",
    "1550": "Tamaqua 4th", "1560": "Tower City", "1580": "Tremont Twp.",
    "1595": "Tremont", "1610": "Union Twp.", "1620": "Upper Mahantongo Twp.",
    "1640": "Walker Twp.", "1650": "Washington Twp. - North",
    "1660": "Washington Twp. - South", "1670": "Wayne Twp. 1st",
    "1680": "Wayne Twp. 2nd", "1685": "Wayne Twp. 3rd",
    "1690": "West Brunswick Twp. - North",
    "1700": "West Brunswick Twp. - South",
    "1710": "West Mahanoy Twp. - Altamont", "1720": "West Mahanoy Twp. 1st",
    "1730": "West Mahanoy Twp. 2nd", "1760": "West Penn Twp. 1st",
    "1770": "West Penn Twp. 2nd", "1775": "West Penn Twp. 3rd",
}

COUNTIES = {
    "bradford": {
        "display": "Bradford", "code": "8",
        "names": BRADFORD_NAMES,
    },
    "columbia": {
        "display": "Columbia", "code": "19",
        "names": COLUMBIA_NAMES,
    },
    "perry": {
        "display": "Perry", "code": "50",
        "names": PERRY_NAMES,
    },
    "schuylkill": {
        "display": "Schuylkill", "code": "54",
        "names": SCHUYLKILL_NAMES,
    },
}

ANCHOR_CSV = os.path.join(REPO_ROOT, "2026", "20260519__pa__primary__county.csv")
PARTY_CANON = {  # anchor's full-name parties -> DEM/REP
    "DEMOCRATIC": "DEM", "DEM": "DEM", "REPUBLICAN": "REP", "REP": "REP",
}
ROW_DETAIL_CANDIDATES = {  # countywide-CSV source-detail rows, not contests
    "OVERVOTES", "UNDERVOTES", "WRITE-IN TOTALS", "WRITE INS",
}


def county_rows_from_dos(path: str, county: str) -> list[dict]:
    """Read a PA DOS 2026 primary certified precinct-returns slice (CSV,
    quoted fields), keep the requested county and election type."""
    cfg = COUNTIES[county]
    out = []
    with open(path, newline="", encoding="latin-1") as fh:
        for row in csv.reader(fh):
            if len(row) < 26 or row[:2] != [YEAR, ELECTION_TYPE]:
                continue
            if row[2] != cfg["code"]:
                continue
            if row[8] not in OFFICES:
                raise SystemExit("unexpected office code %r in %s"
                                 % (row[8], path))
            out.append(
                {
                    "precinct_code": row[3],
                    "office_code": row[8],
                    "party": row[9],
                    "cand_last": row[11],
                    "cand_first": row[12],
                    "cand_middle": row[13],
                    "cand_suffix": row[14],
                    "votes": int(row[15]),
                    "district": row[5],  # contest district (CD/SD/HD, 0=none)
                }
            )
    return out


def cand_name(r: dict) -> str:
    key = (r["cand_last"], r["cand_first"], r["cand_middle"].strip(),
           r["cand_suffix"])
    name = NAME_FIX.get(key)
    if name is None:
        raise SystemExit("no printed name mapped for DOS candidate %r" % (key,))
    return name


def district_for(r: dict) -> str:
    office = OFFICES[r["office_code"]]
    if office in ("Governor", "Lieutenant Governor"):
        return ""
    return str(int(r["district"]))  # U.S. House / State Senate / State House


def to_output_rows(rows: list[dict], county: str) -> list[list]:
    cfg = COUNTIES[county]
    out = []
    for r in rows:
        office = OFFICES[r["office_code"]]
        out.append(
            {
                "county": cfg["display"],
                "precinct": cfg["names"][r["precinct_code"]],
                "office": office,
                "district": district_for(r),
                "party": r["party"],
                "candidate": cand_name(r),
                "votes": r["votes"],
                "_code": int(r["precinct_code"]),
            }
        )
    out.sort(key=lambda x: (x["_code"], OFFICE_ORDER.index(x["office"]),
                            x["party"], x["candidate"]))
    return [
        [o["county"], o["precinct"], o["office"], o["district"], o["party"],
         o["candidate"], str(o["votes"])]
        for o in out
    ]


def _key(office: str, party: str, candidate: str) -> tuple[str, str, str]:
    # case-insensitive, last-name keyed (brief verification rule)
    last = candidate.split()[-1].upper()
    return (office, PARTY_CANON.get(party.upper(), party.upper()), last)


def read_reference_csvs(county: str) -> tuple[dict[tuple, int],
                                              dict[tuple, int]]:
    """(office, party, lastname) -> votes from the statewide county anchor
    and from the county's own certified countywide CSV."""
    cfg = COUNTIES[county]
    anchor: dict[tuple, int] = {}
    county_csv: dict[tuple, int] = {}
    county_path = os.path.join(
        REPO_ROOT, "2026", "counties",
        "20260519__pa__primary__%s__county.csv" % county)
    county_upper = cfg["display"].upper()
    with open(ANCHOR_CSV, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            if row["county"].upper() != county_upper:
                continue
            if row["candidate"].upper() in ROW_DETAIL_CANDIDATES:
                continue  # defensively: the anchor carries no write-in rows
            k = _key(row["office"], row["party"], row["candidate"])
            anchor[k] = anchor.get(k, 0) + int(row["votes"])
    if os.path.exists(county_path):
        with open(county_path, newline="", encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                k = _key(row["office"], row["party"], row["candidate"])
                if k[2] in ROW_DETAIL_CANDIDATES:
                    continue  # write-ins / Overvotes / Undervotes: DOS detail
                county_csv[k] = county_csv.get(k, 0) + int(row["votes"])
    return anchor, county_csv


def verify(county: str, rows: list[dict]) -> int:
    """Sum per (office, party, lastname) and compare against (a) the statewide
    county anchor and (b) the county's own __county.csv.  Returns 0 only if
    every shared key matches; keys the DOS source cannot carry (State
    Committee, write-in/cross-party detail) are reported, not asserted."""
    cfg = COUNTIES[county]
    sums: dict[tuple, int] = {}
    for r in rows:
        key = _key(OFFICES[r["office_code"]], r["party"], cand_name(r))
        sums[key] = sums.get(key, 0) + r["votes"]
    anchor, county_csv = read_reference_csvs(county)
    problems: list[tuple[str, str]] = []  # (severity, message)

    matched = sum(1 for k in sums if k in anchor)
    for key in sorted(set(sums) & set(anchor)):
        if sums[key] != anchor[key]:
            problems.append(("MISMATCH",
                             "anchor mismatch %s: precinct sum %d != anchor %d"
                             % ((key,), sums[key], anchor[key])))
    anchor_not_covered = [k for k in sorted(anchor) if k not in sums]
    extras = [k for k in sorted(sums) if k not in anchor]

    matched_csv = sum(1 for k in sums if k in county_csv)
    for key in sorted(set(sums) & set(county_csv)):
        if sums[key] != county_csv[key]:
            problems.append(("MISMATCH",
                             "countywide-CSV mismatch %s: precinct sum %d != "
                             "countywide %d"
                             % ((key,), sums[key], county_csv[key])))
    csv_not_covered = [k for k in sorted(county_csv) if k not in sums]

    print("verify: %d named-candidate contest sums over %d precincts"
          % (len(sums), len(cfg["names"])))
    print("verify: matched %d anchor keys (%d anchor rows total)"
          % (matched, len(anchor)))
    print("verify: matched %d countywide-CSV keys (%d candidate rows total)"
          % (matched_csv, len(county_csv)))
    if anchor_not_covered:
        # expected: Member of ... State Committee rows (DOS carries none)
        problems.append(("NOTE",
                         "anchor rows not covered (DOS carries no State "
                         "Committee): %s"
                         % ["%s/%s/%s" % k for k in anchor_not_covered]))
    if csv_not_covered:
        problems.append(("NOTE",
                         "countywide-CSV rows not covered (DOS bulk source "
                         "has no write-ins, cross-party write-ins, "
                         "Overvotes/Undervotes or State Committee): %s"
                         % ["%s/%s/%s" % k for k in csv_not_covered]))
    if extras:
        problems.append(("MISMATCH",
                         "precinct sums with no anchor row: %s"
                         % ["%s/%s/%s" % k for k in extras]))
    has_mismatch = any(sev == "MISMATCH" for sev, _ in problems)
    for sev, msg in problems:
        print("  %s %s" % (sev, msg))
    if has_mismatch:
        return 1
    print("verify: all covered contest sums equal the statewide county "
          "anchor and the countywide CSV; only the DOS-inherent gaps remain")
    return 0


def main(argv: list[str]) -> int:
    want_verify = "--verify" in argv
    args = [a for a in argv if a != "--verify"]
    if not args or args[0].lower() not in COUNTIES or "--help" in argv \
            or "-h" in argv:
        print("usage: %s <county> [<out_csv>] [--verify]\n"
              "  <county>: %s"
              % (os.path.basename(sys.argv[0]), " | ".join(COUNTIES)),
              file=sys.stderr)
        return 2 if (not args or args[0].lower() not in COUNTIES) else 0
    county = args[0].lower()
    cfg = COUNTIES[county]
    src = os.path.join(REPO_ROOT, "work2026p", "dos",
                       "%s__dos_precinctreturns.txt" % county)
    out_path = args[1] if len(args) > 1 else os.path.join(
        REPO_ROOT, "2026", "counties",
        "20260519__pa__primary__%s__precinct.csv" % county)
    if not os.path.exists(src):
        print("No DOS source slice found at %s" % src, file=sys.stderr)
        return 1
    rows = county_rows_from_dos(src, county)
    if not rows:
        print("No %s primary rows found in source %s" % (county, src),
            file=sys.stderr)
        return 1
    unknown_precincts = sorted({r["precinct_code"] for r in rows}
                               - set(cfg["names"]))
    if unknown_precincts:
        print("Unknown precinct codes: %s" % unknown_precincts, file=sys.stderr)
        return 1
    unused = sorted(set(cfg["names"]) - {r["precinct_code"] for r in rows})
    if unused:
        print("note: configured precinct codes with no DOS rows: %s" % unused,
              file=sys.stderr)
    out_rows = to_output_rows(rows, county)
    if want_verify:
        problems = verify(county, rows)
        if problems:
            return 1
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    with open(out_path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(HEADER)
        w.writerows(out_rows)
    print("wrote %s (%d data rows, %d precincts) from %s"
          % (out_path, len(out_rows), len({r["precinct_code"] for r in rows}),
             src))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
