#!/usr/bin/env python3
"""
Indiana County, PA 2024 General Primary (April 23, 2024) — precinct-level results.

Source situation (documented): the county's certified document
"Indiana PA 2024 Primary Official Summary Report.pdf" is an Electionware
"Summary Results Report, OFFICIAL RESULTS" (32 pages) which reports results
COUNTYWIDE ONLY — 69 of 69 precincts reported, but there are no per-precinct
vote rows anywhere in the file (the county's 2022-primary and 2026-primary
per-precinct "Precinct Summary"/"Precinct-by-Precinct" report was not posted
for the 2024 primary).  Precinct-level figures therefore come from the PA
Department of State official certified precinct returns bulk file

  https://www.pa.gov/content/dam/copapwp-pagov/en/dos/resources/
      voting-and-elections/bulk-data/electionreturns_2024_primary_precinctreturns.txt

(same fallback used by the Pike/Cameron/Monroe 2022 and Bradford/Monroe/
Westmoreland 2024-primary parsers; Indiana = county code 32; ASCII
comma-delimited, 3,121 Indiana rows).  The county summary PDF remains the
cross-check anchor: every printed named-candidate county total must equal the
sum of the parser's precinct rows for that contest.

Consequences inherent to the DOS bulk source (not parsing losses), reported:
  * no election-day / mail / provisional breakdown -> 7-column output header;
  * no per-precinct Registered Voters / Ballots Cast rows -> none emitted;
  * DOS carries no write-in rows for Indiana -> all write-in totals (countywide
    in the summary PDF: e.g. DEM President 302, REP President 120, DEM State
    Senate 388, DEM State House 62nd 469, ...) are not precinct-assignable and
    are omitted; named-candidate sums are identical to the summary PDF.
  * the DEM State Senate and DEM State House 62nd/66th contests had no printed
    candidates on the ballot (write-ins only) -> no DOS rows exist -> those
    contests are not in the precinct file.

Precinct naming follows the county's own files (the repo's
2024/counties/20241105__pa__general__indiana__precinct.csv): "Armstrong #1",
"Indiana East #2-1", "White #4-5", i.e. the DOS "MUNI X n / MUNI X n X m"
labels rendered in title case with "#" separators. South Mahoning spans
U.S. Congressional Districts 14/15 and appears twice in the DOS file
("SOUTH MAHONING X 1 (USC 14)" and "SOUTH MAHONING X 1 (USC 15)"); the two
codes are merged back into the single precinct "South Mahoning" (the same
precinct as the county's Nov. 2024 file): each contest's votes from both
halves are summed, and each U.S. House contest contributes from its own half
only, so no double counting.

Office normalization: President, U.S. Senate, Attorney General, Auditor
General, State Treasurer, U.S. House, State Senate, State House,
"Delegate to the Democratic/Republican National Convention", "Alternate
Delegate to the Republican National Convention" (districted; district from
the DOS candidate-district column).

Usage:
    uv run python parsers/pa_indiana_primary_2024_results_parser.py \
        <dos_prcres_txt> <output_csv> [summary_txt]

The PA DOS ReadMe (field layout) and the county summary PDF
(pdftotext -layout output) live in work2024p/indiana/.
"""

import csv
import re
import sys
from collections import defaultdict

COUNTY_NAME = "Indiana"
COUNTY_CODE_RAW = "32"
COUNTY_CODE = "32.0"   # DOS bulk stores numeric fields as python floats

HEADER = ["county", "precinct", "office", "district", "party", "candidate",
          "votes"]

# DOS office code -> office name (primary 2024)
OFFICES = {
    "USP": "President",
    "USS": "U.S. Senate",
    "ATT": "Attorney General",
    "AUD": "Auditor General",
    "TRE": "State Treasurer",
    "USC": "U.S. House",
    "STS": "State Senate",
    "STH": "State House",
    "DED": "Delegate to the Democratic National Convention",
    "DER": "Delegate to the Republican National Convention",
    "ADR": "Alternate Delegate to the Republican National Convention",
}

# DOS candidate (last, first, middle, suffix) -> printed candidate name as in
# the county summary PDF / repo conventions.
NAME_MAP = {
    # Names are printed as in the county summary PDF (the anchor); DOS
    # spelling differs in several places (McCormick's middle initial,
    # Zacheray Womer vs "Zach Womer", "GLENN WILLIAM JR." vs the summary's
    # "Glenn GT Thompson", "Thomas J." Uram, "Sonia Stopperich Sulc" vs the
    # summary's "Sonia Stopperich", the county's nicknames for delegates).
    ("BIDEN", "JOSEPH", "R", "JR"): "Joseph R. Biden, Jr.",
    ("PHILLIPS", "DEAN", "BENSON", ""): "Dean Phillips",
    ("CASEY", "ROBERT", "P", "JR"): "Robert P. Casey, Jr.",
    ("MCCORMICK", "DAVID", "H", ""): "Dave McCormick",
    ("BRADFORD-GREY", "KEIR", "N", ""): "Keir Bradford-Grey",
    ("KHAN", "JOSEPH", "JAMIL", ""): "Joe Khan",
    ("DEPASQUALE", "EUGENIO", "", ""): "Eugene DePasquale",
    ("STOLLSTEIMER", "JACK", "", ""): "Jack Stollsteimer",
    ("SOLOMON", "JARED", "G", ""): "Jared Solomon",
    ("KENYATTA", "MALCOLM", "", ""): "Malcolm Kenyatta",
    ("PINSLEY", "MARK", "E", ""): "Mark Pinsley",
    ("DEFOOR", "TIMOTHY", "LIONEL", ""): "Tim DeFoor",
    ("BIZZARRO", "RYAN", "", ""): "Ryan Bizzarro",
    ("MCCLELLAND", "ERIN", "R", ""): "Erin McClelland",
    ("GARRITY", "STACY", "L", ""): "Stacy Garrity",
    ("BACH", "KENNETH", "JAMES", ""): "Ken Bach",
    ("DZIADOS", "CHRISTOPHER", "RYAN", ""): "Chris Dziados",
    ("WOMER", "ZACHERAY", "WILLIAM", ""): "Zach Womer",
    ("RESCHENTHALER", "GUY", "", ""): "Guy Reschenthaler",
    ("THOMPSON", "GLENN", "WILLIAM", "JR."): "Glenn GT Thompson",
    ("PITTMAN", "JOSEPH", "A", ""): "Joe Pittman",
    ("STRUZZI", "JAMES", "B.", "II"): "Jim Struzzi",
    ("SMITH", "BRIAN", "A", ""): "Brian Smith",
    ("BURGESS", "JOSEPH", "W", ""): "Jojo Burgess",
    ("LAFFEY", "SHARON", "", ""): "Sharon Laffey",
    ("BOERIO", "BIBIANA", "", ""): "Bibiana Boerio",
    ("KOPAS", "TED", "", ""): "Ted Kopas",
    ("PROCTOR", "LILLIAN", "", ""): "Lillian Proctor",
    ("NANES", "EZRA", "J", ""): "Ezra Nanes",
    ("SHADLE", "LAURA", "", ""): "Laura Shadle",
    ("WHITE", "BRAXTON", "A", ""): "Braxton White",
    ("SWOBODA", "MARGARET", "M", ""): "Margie Swoboda",
    ("TRUMP", "DONALD", "JOHN", ""): "Donald J. Trump",
    ("HALEY", "NIKKI", "R", ""): "Nikki R. Haley",
    ("SUNDAY", "DAVID", "", ""): "Dave Sunday",
    ("WILLIAMS", "WENDELL", "CRAIG", ""): "Craig Williams",
    # Republican convention delegates (the summary reports nickname spellings;
    # those are the repo's canonical names)
    ("DARR", "SUSAN", "E", ""): "Sue Darr",
    ("ROSSI", "LESLIE", "", ""): "Leslie Rossi",
    ("PLESKOVITCH", "NANCY", "L", ""): "Nancy Pleskovitch",
    ("RENZ", "STEPHEN", "L", ""): "Steve Renz",
    ("URAM", "THOMAS", "J.", ""): "Thomas J. Uram",
    ("STOPPERICH SULC", "SONIA", "MARIE", ""): "Sonia Stopperich",
    ("WARD", "THOMAS", "", ""): "Tom Ward",
    ("DEJEET", "SUSANNA", "N.", ""): "Susanna N. Dejeet",
    ("ROWE", "DAVID", "HUMMER", ""): "David H. Rowe",
    ("CAUSER", "MATTHEW", "MARTIN", ""): "Matthew M. Causer",
    ("HAAS", "JOYCE", "C", ""): "Joyce C. Haas",
    ("KUNSELMAN", "DANIEL", "S", ""): "Danny Kunselman",
    ("SMAIL", "DARLENE", "", ""): "Darlene Smail",
    ("LAYNG", "KENNETH", "", ""): "Ken Layng",
    ("KELLER", "FREDERICK", "B", ""): "Fred Keller",
    ("HARRIS", "MARK", "J", ""): "Mark J. Harris",
    ("ZERFOSS", "JAMES", "CRAIG", ""): "James Zerfoss",
    ("CLARK", "DANIEL", "F", ""): "Daniel F. Clark",
    ("PATTERSON", "MELANIE", "STRINGHILL", ""): "Melanie Stringhill Patterson",
    ("OVERDORFF", "HUNTER", "JAY", ""): "Hunter Overdorff",
    ("HARRIS", "IRENE", "C", ""): "Irene Harris",
}

# DOS candidate-number -> name, filled from the summary PDF parse (see
# --print-summary and build the map when a name is ambiguous by
# (last, first) alone, e.g. Guy Reschenthaler appears in both USC-14 and
# DER-14 with the same tuple).
CAND_ID_NAME = {}

# DOS precinct code -> repo precinct name (matches the county's official
# 2024 general file 2024/counties/20241105__pa__general__indiana__precinct.csv
# and the county's 2022-primary files).
PRECINCT_NAMES = {
    "10": "Armagh",
    "20": "Armstrong #1",
    "30": "Armstrong #2",
    "40": "Banks",
    "55": "Blacklick",
    "70": "Blairsville #1",
    "80": "Blairsville #2",
    "90": "Blairsville #3",
    "100": "Brushvalley",
    "110": "Buffington #1",
    "120": "Buffington #2",
    "130": "Burrell #1",
    "140": "Burrell #2",
    "155": "Canoe",
    "170": "Center #1",
    "180": "Center #2",
    "190": "Center #3",
    "200": "Cherry Tree",
    "210": "Cherryhill #1",
    "220": "Cherryhill #2",
    "230": "Clymer",
    "240": "Conemaugh #1",
    "255": "Conemaugh #2",
    "260": "Conemaugh #3",
    "280": "Creekside",
    "290": "East Mahoning",
    "300": "East Wheatfield #1",
    "310": "East Wheatfield #2",
    "320": "Ernest",
    "330": "Glen Campbell",
    "345": "Grant",
    "365": "Green #1",
    "370": "Green #2",
    "380": "Green #3",
    "400": "Homer City #1",
    "410": "Homer City #2",
    "420": "Indiana East #1",
    "430": "Indiana East #2-1",
    "440": "Indiana East #2-2",
    "450": "Indiana East #3-1",
    "460": "Indiana East #3-2",
    "470": "Indiana East #3-4",
    "480": "Indiana East #4",
    "500": "Marion Center",
    "510": "Montgomery",
    "520": "North Mahoning",
    "530": "Pine #1",
    "540": "Pine #2",
    "550": "Pine #3",
    "560": "Plumville",
    "570": "Rayne",
    "580": "Saltsburg",
    "590": "Shelocta",
    "600": "Smicksburg",
    "610": "South Mahoning",   # DOS code 610 = (USC 14) half
    "615": "South Mahoning",   # DOS code 615 = (USC 15) half (merged)
    "620": "Washington",
    "630": "West Mahoning",
    "640": "West Wheatfield #1",
    "650": "West Wheatfield #2",
    "660": "White #1",
    "670": "White #2",
    "680": "White #3-1",
    "695": "White #3-2",
    "700": "White #3-3",
    "710": "White #4-5",       # DOS "WHITE X 4"
    "720": "White #6",
    "730": "Young #1",
    "740": "Young #2",
    "750": "Young #3",
}

SKIP_CANDIDATES = (
    ".", "WRITE-IN", "WRITE IN", "SCATTERED", "NOT ASSIGNED",
)


def norm_num(v):
    """DOS numeric strings come as '3.0'."""
    f = float(v)
    if f != int(f):
        raise ValueError("non-integer vote: %r" % v)
    return int(f)


def candidate_name(row):
    """Build the printed candidate name from the DOS (last, first, middle,
    suffix) fields, consulting the summary-PDF-derived ID map first."""
    cand_id = row[10].strip()
    if cand_id in CAND_ID_NAME:
        return CAND_ID_NAME[cand_id]
    last, first, middle, suffix = (s.strip() for s in row[11:15])
    key = (last, first, middle, suffix)
    if key in NAME_MAP:
        return NAME_MAP[key]
    mkey = (last, first, middle, "")
    if mkey in NAME_MAP:
        return NAME_MAP[mkey]
    # Fallbacks
    uplast = last.upper()
    if uplast in (".", "", "WRITE-IN", "WRITE IN"):
        raise ValueError("write-in row with no name: %r" % row)
    name = first if not middle else (first + " " + middle)
    name += (", " + suffix) if suffix else ""
    return NAME_MAP.setdefault(key, name)


# --------------------------------------------------------------------------
# PA DOS bulk file
# --------------------------------------------------------------------------

def parse_dos(dos_txt):
    """Yield (precinct_code, office_code, cand_district, party, candidate,
    votes) for Indiana."""
    import io
    rows = 0
    with io.open(dos_txt, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            if not line.strip():
                continue
            r = next(csv.reader([line]))
            if len(r) < 23:
                continue
            if r[2] not in (COUNTY_CODE, COUNTY_CODE_RAW, "32"):
                continue
            if r[1].strip() != "P":
                continue
            rows += 1
            precinct_code = str(norm_num(r[3]))
            if precinct_code not in PRECINCT_NAMES:
                raise ValueError("unknown DOS precinct code %r: %r"
                                 % (precinct_code, r))
            office_code = r[8].strip()
            if office_code not in OFFICES:
                raise ValueError("unknown office code %r" % office_code)
            party = r[9].strip()
            dist = norm_num(r[5])
            votes = norm_num(r[15])
            if votes == 0 and not r[11].strip() or \
               r[11].upper().strip() in SKIP_CANDIDATES:
                continue
            yield (precinct_code,
                   OFFICES[office_code],
                   str(dist) if dist else "",
                   party,
                   candidate_name(r),
                   votes)
    if rows == 0:
        raise ValueError("no Indiana rows found in %s" % dos_txt)


def build_rows(dos_txt):
    agg = defaultdict(int)
    for precinct_code, office, dist, party, candidate, votes in parse_dos(dos_txt):
        precinct = PRECINCT_NAMES[precinct_code]
        agg[(precinct, office, dist, party, candidate)] += votes
    return agg


# --------------------------------------------------------------------------
# county summary PDF anchor (Electionware Summary Results Report)
# --------------------------------------------------------------------------

CONTEST_TITLE_RE = re.compile(r"^\s+(DEM|REP)\s+(?P<title>.+?)\s*$")
DATA_ROW_RE = re.compile(
    r"^\s*(?P<name>[A-Z][A-Za-z .,/'\-&]*[A-Za-z.]*?)\s{2,}"
    r"(?P<v>[\d,]+)\s+([\d,]+)\s+([\d,]+)\s+([\d,]+)\s*$")


def normalize_contest(party, title):
    """Map the summary PDF contest title to (office, district) like the DOS
    rows carry."""
    t = title.strip()
    m = re.match(r"^PRESIDENT OF THE UNITED STATES$", t, re.I)
    if m:
        return "President", ""
    m = re.match(r"^UNITED STATES SENATOR$", t, re.I)
    if m:
        return "U.S. Senate", ""
    m = re.match(r"^ATTORNEY GENERAL$", t, re.I)
    if m:
        return "Attorney General", ""
    m = re.match(r"^AUDITOR GENERAL$", t, re.I)
    if m:
        return "Auditor General", ""
    m = re.match(r"^STATE TREASURER$", t, re.I)
    if m:
        return "State Treasurer", ""
    m = re.match(r"^REP\. IN CONGRESS (\d+(?:TH|ND|RD|ST)) DISTRICT$", t, re.I)
    if m:
        return "U.S. House", str(int(re.sub(r"\D", "", m.group(1))))
    m = re.match(r"^SENATOR IN THE GENERAL ASSEMBLY", t, re.I)
    if m:  # the district is not printed in the summary title; DOS says 41
        return "State Senate", "41"
    m = re.match(r"^PA GENERAL ASSEMBLY (\d+)\w*\s+DIST", t, re.I)
    if m:
        return "State House", m.group(1)
    m = re.match(r"^DELEGATE TO DNC (\d+)(?:TH|ND|RD|ST) District$", t, re.I)
    if m:
        return ("Delegate to the Democratic National Convention", m.group(1))
    m = re.match(r"^DELEGATE TO RNC (\d+)(?:TH|ND|RD|ST) District$", t, re.I)
    if m:
        return ("Delegate to the Republican National Convention", m.group(1))
    m = re.match(r"^ALTERNATE DELEGATE: RNC (\d+)(?:TH|ND|RD|ST) District$",
                 t, re.I)
    if m:
        return ("Alternate Delegate to the Republican National Convention",
                m.group(1))
    raise ValueError("unmapped summary contest title: %r (%s)" % (title, party))


def summary_candidate_name(name):
    """Normalize whitespace in a summary-PDF candidate name."""
    return re.sub(r"\s+", " ", name).strip()


def parse_summary(summary_txt):
    """Return {(office, district, party, name): total} for named candidate
    rows in the Electionware summary report, accumulating page splits."""
    totals = {}
    current = None
    with open(summary_txt) as f:
        for line in f:
            s = line.rstrip("\n")
            m = CONTEST_TITLE_RE.match(s)
            if m:
                current = (normalize_contest(m.group(1), m.group("title"))
                           + (m.group(1),))
                continue
            if current is None:
                continue
            if re.match(r"^\s*(Write-In Totals|Not Assigned|Total Votes Cast|"
                        r"Overvotes|Undervotes|Contest Totals)", s, re.I):
                current = current  # contest may continue on next page; keep
                continue
            if "Precincts Reporting" in s:
                current = None  # contest fully closed
                continue
            if re.match(r"\s*Write-In:", s):
                continue
            mrow = DATA_ROW_RE.match(s)
            if mrow and current is not None:
                vals = mrow.group("v").replace(",", "")
                name = summary_candidate_name(mrow.group("name"))
                key = (current[0], current[1], current[2], name)
                totals[key] = totals.get(key, 0) + int(vals)
    return totals


# --------------------------------------------------------------------------
# output + verification
# --------------------------------------------------------------------------

def write_precinct_csv(agg, out_csv):
    rows = []
    for (precinct, office, dist, party, candidate), votes in sorted(
            agg.items(), key=lambda k: (k[0][0], k[0][1], _dist_sort(k[0][2]),
                                        k[0][3], k[0][4])):
        rows.append([COUNTY_NAME, precinct, office, dist, party, candidate,
                     votes])
    with open(out_csv, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(HEADER)
        w.writerows(rows)
    return rows


def _dist_sort(d):
    try:
        return (0, int(d), "")
    except (TypeError, ValueError):
        return (1, 0, d or "")


def verify(agg, summary_totals):
    """Compare contest-level precinct-row sums with the summary PDF's printed
    totals."""
    dos_total = defaultdict(int)
    for (_p, office, dist, party, candidate), votes in agg.items():
        dos_total[(office, dist, party, candidate)] += votes
    mismatches = []
    matched = 0
    keys = set(dos_total) | set(summary_totals)
    for key in sorted(keys, key=lambda k: (k[0], _dist_sort(k[1]), k[2], k[3])):
        d = dos_total.get(key, 0)
        s = summary_totals.get(key, None)
        if key not in summary_totals:
            mismatches.append((key, d, s, "not printed in summary PDF"))
        elif key not in dos_total:
            mismatches.append((key, d, s, "not in DOS file (write-in only "
                                          "contest, or DOS omits)"))
        elif d != s:
            mismatches.append((key, d, s, "TOTAL MISMATCH"))
        else:
            matched += 1
    return matched, mismatches, dos_total


def main(argv):
    dos_txt, out_csv = argv[1], argv[2]
    summary_txt = argv[3] if len(argv) > 3 else None
    agg = build_rows(dos_txt)
    rows = write_precinct_csv(agg, out_csv)
    print("wrote %s: %d data rows, %d unique precincts" % (
        out_csv, len(rows), len({r[1] for r in rows})))
    if summary_txt:
        summary_totals = parse_summary(summary_txt)
        matched, mismatches, dos_total = verify(agg, summary_totals)
        print("verified %d candidate totals vs summary PDF" % matched)
        if mismatches:
            print("mismatches / notes (%d):" % len(mismatches))
            for (office, dist, party, name), d, s, why in mismatches:
                print("  %-6s %-6s %s | %r: DOS=%s summary=%s (%s)" % (
                    party, dist, office, name, d, s, why))
        else:
            print("all contest-level sums match the summary PDF exactly")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
