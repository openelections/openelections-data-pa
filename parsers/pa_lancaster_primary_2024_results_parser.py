#!/usr/bin/env python3
"""Lancaster County, PA 2024 Primary (April 23, 2024) — precinct-level results parser.

Source situation (documented):
  * The county's certified report
    "~/code/openelections-sources-pa/2024/primary/Lancaster PA 2024 Primary
    Official Summary Results.pdf" (Summary Results Report, 289 pages, generated
    01/08/2025) is COUNTYWIDE ONLY.  Every contest section prints a single TOTAL
    column; the only precinct references are "Precincts Reported 241 of 241"
    headers.  Verified by reading the full pdftotext -layout extract
    (work2024p/lancaster/lancaster_summary.txt).
  * Precinct-level figures therefore come from the PA Department of State
    certified precinct-returns bulk file for the primary:
      ElectionReturns_2024_Primary_PrecinctReturns.txt
    https://www.pa.gov/agencies/dos/resources/voting-and-elections-resources/voting-and-election-statistics/election-data
    (comma-delimited, Lancaster = county code 36, election type "P"; 8,209
    Lancaster rows, 241 precincts).  A verbatim slice is kept at
    work2024p/lancaster/dos_prcres.txt (full file).

Consequences (inherent to the DOS bulk source, not parsing losses):
  * No election-day / absentee / mail-in / provisional breakdown -> 7-column
    output header.
  * No per-precinct Registered Voters / Ballots Cast rows (the county summary
    carries RV 290,061 / BC 84,557, turnout 29.15%, countywide only).
  * No write-in rows of any kind (DOS carries only ballot-named candidates), so
    write-in-only ballot sections have no rows: DEM State Senate 13 (2,463
    write-ins countywide), DEM State House 43/98/99/100, REP State House 49,
    and the uncontested-but-write-in sections of every office.  The party
    State Committee contests (Member of the Democratic/Republican State
    Committee - DSC/RSC) are not carried for Lancaster in the DOS file at all,
    though the county summary prints them countywide-only.  The county
    summary's write-in detail is county-level and cannot be assigned to
    precincts, so per the no-invention rule no Write Ins rows are emitted.
  * Candidate names are those printed in the county's certified summary report
    (the anchor file); they were matched to the DOS (LAST, FIRST, MIDDLE,
    SUFFIX) fields one-for-one, every pairing confirmed by exact vote-sum
    equality (see --verify).  Notable DOS keying oddities the summary corrects:
    DED "TEMIN, JANET" -> Janet Diaz, DED "MAGTABNOB, EFREM" -> Efrem Magtagnob.
  * Lancaster City Ward 7 Precinct 8 is split into two "School District"
    precincts in the DOS file (codes 925/927); the DOS labels are kept as
    "... (School District 1)" / "... (School District 2)" because DOS does not
    say which is which (the 2022 county source called them 7th Ward - 8th
    Precinct (CV) / (LS)).

Contests carried (241 precincts): President (DEM/REP), U.S. Senate (DEM/REP),
U.S. House 11 (DEM/REP), Attorney General (DEM/REP), Auditor General (DEM/REP),
State Treasurer (DEM/REP), State Senate 13 (REP only), State House 37/41/43/49/
96/97/98/99/100 (party sections with ballot candidates only), Delegate to the
Democratic/Republican National Convention (CD 11) and Alternate Delegate to the
Republican National Convention (CD 11).  Local party Committee Person contests
are countywide-only in the DOS file and are omitted.

Usage:
    python parsers/pa_lancaster_primary_2024_results_parser.py <dos_prcres_txt> <out.csv>
    python parsers/pa_lancaster_primary_2024_results_parser.py --verify <dos_prcres_txt> <out.csv>
"""
from __future__ import annotations

import csv
import os
import sys

COUNTY = "Lancaster"
COUNTY_CODE = "36"
YEAR = "2024"
ETYPE = "P"

HEADER = ["county", "precinct", "office", "district", "party", "candidate", "votes"]

# DOS candidate (last, first) -> name as printed in the county certified summary
CAND_NAMES = {
    ("BIDEN", "JOSEPH"): "Joseph R Biden Jr",
    ("PHILLIPS", "DEAN"): "Dean Phillips",
    ("TRUMP", "DONALD"): "Donald J Trump",
    ("HALEY", "NIKKI"): "Nikki R Haley",
    ("CASEY", "ROBERT"): "Robert P Casey Jr",
    ("MCCORMICK", "DAVID"): "Dave McCormick",
    ("DEPASQUALE", "EUGENIO"): "Eugene DePasquale",
    ("BRADFORD-GREY", "KEIR"): "Keir Bradford-Grey",
    ("KHAN", "JOSEPH"): "Joe Khan",
    ("SOLOMON", "JARED"): "Jared Solomon",
    ("STOLLSTEIMER", "JACK"): "Jack Stollsteimer",
    ("SUNDAY", "DAVID"): "Dave Sunday",
    ("WILLIAMS", "WENDELL"): "Craig Williams",
    ("KENYATTA", "MALCOLM"): "Malcolm Kenyatta",
    ("PINSLEY", "MARK"): "Mark Pinsley",
    ("DEFOOR", "TIMOTHY"): "Tim DeFoor",
    ("MCCLELLAND", "ERIN"): "Erin McClelland",
    ("BIZZARRO", "RYAN"): "Ryan Bizzarro",
    ("GARRITY", "STACY"): "Stacy Garrity",
    ("ATKINSON", "JAMES"): "Jim Atkinson",
    ("SMUCKER", "LLOYD"): "Lloyd K Smucker",
    ("MARTIN", "SCOTT"): "Scott Martin",
    ("GEORGE", "JOHN"): "John George",
    ("FEE", "MELINDA"): "Mindy Fee",
    ("CHAMBERS", "BRADFORD"): "Brad Chambers",
    ("MILLER", "BRETT"): "Brett R Miller",
    ("GREINER", "KEITH"): "Keith J Greiner",
    ("SMITH-WADE-EL", "ISMAIL"): "Izzy Smith-Wade-El",
    ("STURLA", "PETER"): "Mike Sturla",
    ("BEEZER", "ERIC"): "Eric Beezer",
    ("RUDY", "ROBERT"): "Bob Rudy",
    ("MENTZER", "STEVEN"): "Steve Mentzer",
    ("JONES", "THOMAS"): "Tom Jones",
    ("ZIMMERMAN", "DAVID"): "Dave Zimmerman",
    ("NISSLEY", "DAVID"): "Dave Nissley",
    ("CUTLER", "BRYAN"): "Bryan D Cutler",
    ("DIAZ", "JANET"): "Janet Diaz",
    ("TEMIN", "JANET"): "Janet Diaz",  # DOS miskey; county summary prints Janet Diaz
    ("JONES", "PAUL"): "Mike Jones",
    ("SORACE", "CAROLE"): "Danene Sorace",
    ("O'BRIEN", "TOM"): "Tom O'Brien",
    ("MAGTABNOB", "EFREM"): "Efrem Magtagnob",
    ("RADANOVIC", "S"): "S Kirk Radanovic",
    ("GLICK", "BRYANT"): "Bryant J Glick",
    ("GEESEY", "JENNA"): "Jenna I Geesey",
    ("OGILVIE", "SHARON"): "Sharon L Ogilvie",
}

OFFICES = {  # office code -> output office, and office-sort rank
    "USP": ("President", 0),
    "USS": ("U.S. Senate", 1),
    "ATT": ("Attorney General", 2),
    "AUD": ("Auditor General", 3),
    "TRE": ("State Treasurer", 4),
    "USC": ("U.S. House", 5),
    "STS": ("State Senate", 6),
    "STH": ("State House", 7),
    "DED": ("Delegate to the Democratic National Convention", 8),
    "DER": ("Delegate to the Republican National Convention", 9),
    "ADR": ("Alternate Delegate to the Republican National Convention", 10),
}

# Districted offices: district comes from the DOS "Candidate District" field
DISTRICTED = {"USC", "STS", "STH", "DED", "DER", "ADR"}

# Countywide totals printed in the certified county summary PDF, per
# (office, party, candidate).  Verified against the DOS precinct sums below --verify.
PDF_TOTALS = {
    ("President", "DEM"): {"Joseph R Biden Jr": 27445, "Dean Phillips": 1571},
    ("President", "REP"): {"Donald J Trump": 40889, "Nikki R Haley": 10495},
    ("U.S. Senate", "DEM"): {"Robert P Casey Jr": 29759},
    ("U.S. Senate", "REP"): {"Dave McCormick": 47273},
    ("Attorney General", "DEM"): {
        "Eugene DePasquale": 12459, "Keir Bradford-Grey": 5229, "Joe Khan": 4403,
        "Jared Solomon": 3994, "Jack Stollsteimer": 3049,
    },
    ("Attorney General", "REP"): {"Dave Sunday": 40762, "Craig Williams": 8398},
    ("Auditor General", "DEM"): {"Malcolm Kenyatta": 19890, "Mark Pinsley": 9223},
    ("Auditor General", "REP"): {"Tim DeFoor": 47236},
    ("State Treasurer", "DEM"): {"Erin McClelland": 15281, "Ryan Bizzarro": 13665},
    ("State Treasurer", "REP"): {"Stacy Garrity": 47000},
    ("U.S. House", "DEM"): {"Jim Atkinson": 28660},
    ("U.S. House", "REP"): {"Lloyd K Smucker": 48091},
    ("State Senate", "REP"): {"Scott Martin": 19478},
    ("State House", "DEM"): {
        "John George": 3713, "Brad Chambers": 4535,
        "Izzy Smith-Wade-El": 3511, "Mike Sturla": 5568, "Bob Rudy": 4050,
    },
    ("State House", "REP"): {
        "Mindy Fee": 7474, "Brett R Miller": 5496, "Keith J Greiner": 5163,
        "Eric Beezer": 3718, "Steve Mentzer": 6910, "Tom Jones": 5646,
        "Dave Zimmerman": 4395, "Dave Nissley": 4362, "Bryan D Cutler": 5029,
    },
    ("Delegate to the Democratic National Convention", "DEM"): {
        "Janet Diaz": 25499, "Izzy Smith-Wade-El": 25394, "Danene Sorace": 24548,
        "Tom O'Brien": 24182, "Efrem Magtagnob": 22758,
    },
    ("Delegate to the Republican National Convention", "REP"): {
        "Lloyd K Smucker": 45618, "Mike Jones": 35104, "S Kirk Radanovic": 31622,
    },
    ("Alternate Delegate to the Republican National Convention", "REP"): {
        "Bryant J Glick": 38932, "Jenna I Geesey": 35579, "Sharon L Ogilvie": 35034,
    },
}

# Countywide "Total Votes Cast" per contest as printed in the summary PDF
# (write-ins included).  DOS candidate sums must be <= these.
PDF_TOTAL_VOTES_CAST = {
    ("President", "DEM"): 29344, ("President", "REP"): 51975,
    ("U.S. Senate", "DEM"): 29930, ("U.S. Senate", "REP"): 47756,
    ("Attorney General", "DEM"): 29179, ("Attorney General", "REP"): 49284,
    ("Auditor General", "DEM"): 29148, ("Auditor General", "REP"): 47390,
    ("State Treasurer", "DEM"): 28989, ("State Treasurer", "REP"): 47148,
    ("U.S. House", "DEM"): 28855, ("U.S. House", "REP"): 48724,
    ("State Senate", "DEM"): 2463, ("State Senate", "REP"): 19561,
    ("State House", "DEM"): 22716, ("State House", "REP"): 48593,
    ("Delegate to the Democratic National Convention", "DEM"): 122787,
    ("Delegate to the Republican National Convention", "REP"): 113063,
}  # ADR "Total Votes Cast" not reliably attributable in the summary layout
# (the county summary's delegate/committee sections run together); the strict
# named-candidate check covers ADR.


def f(v):
    return int(float(v)) if v else 0


def county_rows(path):
    """Read the DOS prcres structure, keep Lancaster 2024 primary rows."""
    out = []
    with open(path, newline="", encoding="latin-1") as fh:
        for row in csv.reader(fh):
            if len(row) < 21:
                continue
            try:
                if f(row[0]) != int(YEAR) or row[1].strip() != ETYPE or f(row[2]) != int(COUNTY_CODE):
                    continue
            except ValueError:
                continue
            code = row[8]
            if code not in OFFICES:
                raise SystemExit("unexpected office code %r in %s" % (code, path))
            out.append({
                "precinct_code": f(row[3]),
                "office_code": code,
                "party": row[9].strip(),
                "cand_last": row[11].strip(),
                "cand_first": row[12].strip(),
                "cand_middle": row[13].strip(),
                "cand_suffix": row[14].strip(),
                "votes": f(row[15]),
                "cand_district": f(row[5]),  # DOS "Candidate District" field
                "uscd": f(row[18]),
                "shd": f(row[20]),
            })
    return out


def cand_name(r):
    return CAND_NAMES[(r["cand_last"], r["cand_first"])]


def office_district(r):
    office = OFFICES[r["office_code"]][0]
    if office == "U.S. House":
        cd = r["uscd"] or r["cand_district"]
        if not cd or cd != r["cand_district"]:
            raise SystemExit("U.S. House district mismatch %s %s" % (r["precinct_code"], r))
        return str(cd)
    return str(r["cand_district"]) if r["office_code"] in DISTRICTED else ""


PRECINCT_NAMES = {
    10: "Adamstown Boro", 20: "Akron Boro - East Dist", 30: "Akron Boro - West Dist",
    40: "Bart Twp", 50: "Brecknock Twp - Bowmansville Dist", 60: "Brecknock Twp - Muddy Creek Dist",
    70: "Caernarvon Twp", 80: "Christiana Boro", 90: "Clay Twp - North", 95: "Clay Twp - South",
    100: "Colerain Twp", 110: "Columbia Boro - 1st Ward", 140: "Columbia Boro - 4th Ward",
    150: "Columbia Boro - 5th Ward", 160: "Columbia Boro - 6th Ward", 170: "Columbia Boro - 7th Ward",
    190: "Columbia Boro - 9th Ward", 200: "Conestoga Twp", 210: "Conoy Twp", 220: "Denver Boro",
    230: "Drumore Twp", 240: "Earl Twp - Martindale Dist", 250: "Earl Twp - New Holland Dist",
    260: "Cocalico East Twp - Smokestown Dist", 265: "Cocalico East Twp - Swartzville Dist",
    270: "Cocalico East Twp - Reamstown Dist", 275: "Cocalico East Twp - Stevens Dist",
    280: "Donegal East Twp - Maytown Dist", 285: "Donegal East Twp - Maytown West Dist",
    290: "Donegal East Twp - Springville Dist", 300: "Drumore East Twp",
    310: "Earl East Twp - Blue Ball Dist", 320: "Earl East Twp - Terre Hill Dist",
    330: "Hempfield East Twp - Barrcrest Dist", 340: "Hempfield East Twp - Centerville Dist",
    350: "Hempfield East Twp - Cherry Hill Dist", 360: "Hempfield East Twp - Chestnut Ridge Dist",
    370: "Hempfield East Twp - Friendly Dist", 390: "Hempfield East Twp - Hempland Dist",
    400: "Hempfield East Twp - Indian Springs Dist", 410: "Hempfield East Twp - Kings District",
    430: "Hempfield East Twp - Landisville Dist", 440: "Hempfield East Twp - Millcreek Dist",
    450: "Hempfield East Twp - Petersburg Dist", 460: "Hempfield East Twp - Pleasant View Dist",
    465: "Hempfield East Twp - Spring Dist", 470: "Hempfield East Twp - Rohrerstown Dist",
    480: "Hempfield East Twp - Scotland Dist", 485: "Lampeter East Twp - 1st Dist",
    490: "Lampeter East Twp - 2nd Dist", 495: "Lampeter East Twp - 3rd Dist",
    500: "Lampeter East Twp - 4th Dist", 505: "Lampeter East Twp - 5th Dist",
    510: "Lampeter East Twp - 6th Dist", 515: "Lampeter East Twp - 7th Dist",
    517: "Lampeter East Twp - 8th Dist", 520: "East Petersburg Boro North",
    530: "East Petersburg Boro South", 540: "East Petersburg Boro West", 550: "Eden Twp",
    560: "Elizabeth Twp", 570: "Elizabethtown Boro - 1st Ward", 580: "Elizabethtown Boro - 2nd Ward",
    590: "Elizabethtown Boro - 3rd Ward - 1st Precinct", 600: "Elizabethtown Boro - 3rd Ward - 2nd Precinct",
    605: "Elizabethtown Boro - 3rd Ward - 3rd Precinct", 610: "Ephrata Twp - Lincoln Dist",
    615: "Ephrata Twp - Trout Run Dist", 620: "Ephrata Twp - Murrell Dist",
    630: "Ephrata Boro - 1st Ward", 640: "Ephrata Boro - 2nd Ward", 650: "Ephrata Boro - 3rd Ward",
    660: "Ephrata Boro - 4th Ward", 670: "Fulton Twp", 680: "Lancaster City - 1st Ward",
    690: "Lancaster City - 2nd Ward - 1st Precinct", 700: "Lancaster City - 2nd Ward - 2nd Precinct",
    710: "Lancaster City - 3rd Ward", 730: "Lancaster City - 4th Ward - 1st Precinct",
    740: "Lancaster City - 4th Ward - 2nd Precinct", 750: "Lancaster City - 5th Ward - 1st Precinct",
    760: "Lancaster City - 5th Ward - 2nd Precinct", 770: "Lancaster City - 5th Ward - 3rd Precinct",
    780: "Lancaster City - 6th Ward - 1st Precinct", 790: "Lancaster City - 6th Ward - 2nd Precinct",
    800: "Lancaster City - 6th Ward - 3rd Precinct", 810: "Lancaster City - 6th Ward - 4th Precinct",
    820: "Lancaster City - 6th Ward - 5th Precinct", 830: "Lancaster City - 6th Ward - 6th Precinct",
    840: "Lancaster City - 6th Ward - 7th Precinct", 850: "Lancaster City - 6th Ward - 8th Precinct",
    870: "Lancaster City - 7th Ward - 1st Precinct", 875: "Lancaster City - 7th Ward - 2nd Precinct",
    880: "Lancaster City - 7th Ward - 3rd Precinct", 890: "Lancaster City - 7th Ward - 4th Precinct",
    900: "Lancaster City - 7th Ward - 5th Precinct", 910: "Lancaster City - 7th Ward - 6th Precinct",
    920: "Lancaster City - 7th Ward - 7th Precinct",
    925: "Lancaster City - 7th Ward - 8th Precinct (School District 1)",
    927: "Lancaster City - 7th Ward - 8th Precinct (School District 2)",
    930: "Lancaster City - 8th Ward - 1st Precinct", 940: "Lancaster City - 8th Ward - 2nd Precinct",
    950: "Lancaster City - 8th Ward - 3rd Precinct", 960: "Lancaster City - 8th Ward - 4th Precinct",
    970: "Lancaster City - 8th Ward - 5th Precinct", 980: "Lancaster City - 8th Ward - 6th Precinct",
    990: "Lancaster City - 8th Ward - 7th Precinct", 1000: "Lancaster City - 8th Ward - 8th Precinct",
    1010: "Lancaster City - 8th Ward - 9th Precinct", 1030: "Lancaster City - 9th Ward - 1st Precinct",
    1040: "Lancaster City - 9th Ward - 2nd Precinct", 1050: "Lancaster City - 9th Ward - 3rd Precinct",
    1060: "Lancaster City - 9th Ward - 4th Precinct", 1070: "Lancaster City - 9th Ward - 5th Precinct",
    1080: "Lancaster Twp - 1st Dist", 1090: "Lancaster Twp - 2nd Dist", 1100: "Lancaster Twp - 3rd Dist",
    1110: "Lancaster Twp - 4th Dist", 1120: "Lancaster Twp - 5th Dist", 1130: "Lancaster Twp - 6th Dist",
    1140: "Lancaster Twp - 7th Dist", 1143: "Lancaster Twp - 8th Dist", 1146: "Lancaster Twp - 9th Dist",
    1150: "Leacock Twp", 1160: "Lititz Boro - 1st Ward - 1st Precinct",
    1170: "Lititz Boro - 1st Ward - 2nd Precinct", 1180: "Lititz Boro - 2nd Ward - 1st Precinct",
    1190: "Lititz Boro - 2nd Ward - 2nd Precinct", 1200: "Lititz Boro - 3rd Ward - 1st Precinct",
    1210: "Lititz Boro - 3rd Ward - 2nd Precinct", 1220: "Little Britain Twp",
    1230: "Manheim Twp - 1st Dist", 1240: "Manheim Twp - 2nd Dist", 1250: "Manheim Twp - 3rd Dist",
    1260: "Manheim Twp - 4th Dist", 1270: "Manheim Twp - 5th Dist", 1280: "Manheim Twp - 6th Dist",
    1290: "Manheim Twp - 7th Dist", 1310: "Manheim Twp - 8th Dist", 1320: "Manheim Twp - 9th Dist",
    1330: "Manheim Twp - 10th Dist", 1340: "Manheim Twp - 11th Dist", 1350: "Manheim Twp - 12th Dist",
    1360: "Manheim Twp - 13th Dist", 1370: "Manheim Twp - 14th Dist", 1380: "Manheim Twp - 15th Dist",
    1390: "Manheim Twp - 16th Dist", 1400: "Manheim Twp - 17th Dist", 1410: "Manheim Twp - 18th Dist",
    1420: "Manheim Twp - 19th Dist", 1422: "Manheim Twp - 20th Dist", 1425: "Manheim Twp - 21st Dist",
    1427: "Manheim Twp - 22nd Dist", 1428: "Manheim Twp - 23rd Dist", 1430: "Manheim Boro - 1st Ward",
    1440: "Manheim Boro - 2nd Ward", 1450: "Manor Twp - Bethel Dist", 1460: "Manor Twp - Hambright Dist",
    1470: "Manor Twp - Indiantown Dist", 1480: "Manor Twp - Leisure Dist",
    1485: "Manor Twp - Hershey Mill Dist", 1490: "Manor Twp - Manor New Dist",
    1495: "Manor Twp - New East Dist", 1500: "Manor Twp - Washington Boro Dist",
    1510: "Manor Twp - West Lancaster Dist", 1520: "Marietta Boro - 1st Dist",
    1530: "Marietta Boro - 2nd Dist", 1540: "Martic Twp - Holtwood Dist",
    1545: "Martic Twp - Marticville Dist", 1550: "Millersville Boro - 1st Dist",
    1560: "Millersville Boro - 2nd Dist", 1570: "Millersville Boro - 3rd Dist",
    1580: "Millersville Boro - 4th Dist", 1600: "Mountville Boro",
    1610: "Mount Joy Twp - Cloverleaf", 1620: "Mount Joy Twp - Fairview",
    1630: "Mount Joy Twp - Milton Grove Dist", 1640: "Mount Joy Twp - Hershey Road",
    1650: "Mount Joy Boro - East Ward", 1660: "Mount Joy Boro - Florin Ward",
    1670: "Mount Joy Boro - West Ward", 1680: "New Holland Boro - 1st Dist",
    1684: "New Holland Boro - 2nd Dist", 1688: "New Holland Boro - 3rd Dist",
    1690: "Paradise Twp", 1700: "Penn Twp - Junction Dist", 1710: "Penn Twp - Penryn",
    1720: "Penn Twp - South Penn Dist", 1730: "Pequea Twp - New Danville Dist",
    1735: "Pequea Twp - West Willow Dist", 1740: "Providence Twp - East Dist",
    1750: "Providence Twp - West Dist", 1760: "Quarryville Boro",
    1765: "Rapho Twp - Elm Tree I Dist", 1775: "Rapho Twp - Elm Tree II Dist",
    1780: "Rapho Twp - Willow Creek Dist", 1785: "Rapho Twp - Sporting Hill Dist",
    1795: "Rapho Twp - Union Square Dist", 1800: "Sadsbury Twp",
    1810: "Salisbury Twp - Cambridge Dist", 1820: "Salisbury Twp - Gap Dist",
    1830: "Salisbury Twp - White Horse Dist", 1840: "Strasburg Twp",
    1850: "Strasburg Boro - 1st Ward", 1870: "Strasburg Boro - 3rd Ward", 1880: "Terre Hill Boro",
    1890: "Leacock Upper Twp - Bareville Dist", 1900: "Leacock Upper Twp - Leacock Dist",
    1910: "Leacock Upper Twp - Leola Dist", 1920: "Warwick Twp - Brunnerville Dist",
    1923: "Warwick Twp - Clay Newport Dist", 1926: "Warwick Twp - Hilltop Manor Dist",
    1930: "Warwick Twp - Kissel Hill Dist", 1935: "Warwick Twp - Millport Dist",
    1938: "Warwick Twp - Newport West Dist", 1940: "Warwick Twp - Rothsville Dist",
    1945: "Warwick Twp - Southwest Dist", 1950: "Warwick Twp - Woodcrest Dist",
    1960: "Cocalico West Twp - Reinholds Dist", 1970: "Cocalico West Twp - Schoeneck Dist",
    1980: "Donegal West Twp - 1st Dist", 1990: "Donegal West Twp - 2nd Dist",
    2000: "Donegal West Twp - 3rd Dist", 2005: "Donegal West Twp - 4th Dist",
    2010: "Earl West Twp - Earlville Dist", 2020: "Earl West Twp - Farmersville Dist",
    2025: "Earl West Twp - North District", 2030: "Hempfield West Twp - Chestnut Hill Dist",
    2040: "Hempfield West Twp - Farmdale Dist", 2050: "Hempfield West Twp - Highland Park Dist",
    2060: "Hempfield West Twp - Ironville Dist", 2070: "Hempfield West Twp - Oyster Point Dist",
    2080: "Hempfield West Twp - Salunga Dist", 2090: "Hempfield West Twp - Silver Spring Dist",
    2095: "Lampeter West Twp - Lakes Dist", 2100: "Lampeter West Twp - Lampeter Dist",
    2105: "Lampeter West Twp - Locust Dist", 2110: "Lampeter West Twp - Lyndon Dist",
    2120: "Lampeter West Twp - Millport Dist", 2125: "Lampeter West Twp - Windy Hill Dist",
    2130: "Lampeter West Twp - Willow Street Dist",
}


def to_output_rows(rows):
    out = []
    for r in rows:
        office, rank = OFFICES[r["office_code"]]
        out.append({
            "county": COUNTY,
            "precinct": PRECINCT_NAMES[r["precinct_code"]],
            "office": office,
            "district": office_district(r),
            "party": r["party"],
            "candidate": cand_name(r),
            "votes": r["votes"],
            "_k": (r["precinct_code"], rank, office_district(r), cand_name(r)),
        })
    out.sort(key=lambda x: x["_k"])
    return [[o["county"], o["precinct"], o["office"], o["district"], o["party"],
             o["candidate"], str(o["votes"])] for o in out]


def run_checks(rows):
    """Sum precinct votes per (office, party, district?, candidate) and compare
    with the county certified summary PDF's printed TOTALS."""
    sums = {}
    for r in rows:
        office = OFFICES[r["office_code"]][0]
        key = (office, r["party"], cand_name(r))
        sums[key] = sums.get(key, 0) + r["votes"]
    problems = []
    n = 0
    for (office, party), cands in PDF_TOTALS.items():
        for cand, total in cands.items():
            n += 1
            mine = sums.get((office, party, cand), 0)
            if mine != total:
                problems.append(f"{office} [{party}]: {cand}: precinct sum {mine} != PDF total {total}")
    # contest-level: named-candidate sums must not exceed Total Votes Cast
    per_contest = {}
    for (office, party, cand), v in sums.items():
        per_contest[(office, party)] = per_contest.get((office, party), 0) + v
    for key, tv in sorted(PDF_TOTAL_VOTES_CAST.items()):
        mine = per_contest.get(key, 0)
        if mine > tv:
            problems.append(f"{key[0]} [{key[1]}]: named-candidate sum {mine} > Total Votes Cast {tv}")
        n += 1
    return n, problems


DEFAULT_SOURCE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "work2024p", "lancaster", "dos_prcres.txt",
)


def main(argv):
    verify = "--verify" in argv
    args = [a for a in argv if a != "--verify"]
    src = args[0] if args and os.path.exists(args[0]) else DEFAULT_SOURCE
    if not os.path.exists(src):
        print("No DOS 2024 primary precinct-returns source found (tried %s)" % (args or DEFAULT_SOURCE),
              file=sys.stderr)
        return 1
    rows = county_rows(src)
    if not rows:
        print("No Lancaster 2024 primary rows found in %s" % src, file=sys.stderr)
        return 1
    missing = sorted({r["precinct_code"] for r in rows} - set(PRECINCT_NAMES))
    if missing:
        print("Unknown precinct codes: %s" % missing, file=sys.stderr)
        return 1
    unknown_cands = [(r["cand_last"], r["cand_first"]) for r in rows
                     if (r["cand_last"], r["cand_first"]) not in CAND_NAMES]
    if unknown_cands:
        print("Unmapped candidates: %s" % sorted(set(unknown_cands)), file=sys.stderr)
        return 1

    out_rows = to_output_rows(rows)

    if len(args) > 1:
        with open(args[1], "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(HEADER)
            w.writerows(out_rows)
        print("wrote %s (%d data rows, %d precincts) from %s"
              % (args[1], len(out_rows), len(PRECINCT_NAMES), src))

    if verify:
        n, problems = run_checks(rows)
        print("verify: %d totals checked against the county certified summary PDF" % n)
        if problems:
            for p in problems:
                print("  MISMATCH " + p)
            return 1
        print("verify: all contest sums match the county summary PDF TOTALS")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
