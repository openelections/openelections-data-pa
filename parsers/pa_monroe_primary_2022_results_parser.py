#!/usr/bin/env python3
"""Parse Monroe County 2022 primary election precinct results.

Sources (in parse order):

1. PA Department of State bulk precinct-returns file for the 2022 Primary
   ("prcres.txt"; ElectionReturns_2022_Primary_PrecinctReturns.txt from
   https://www.pa.gov/agencies/dos/resources/voting-and-elections-resources/
   voting-and-election-statistics/election-data, county code 45 = Monroe).
   The county's own "Monroe PA 2022 Primary Official Certified Results.xlsx"
   is countywide-only (no precinct detail), so DOS is the precinct-level
   source, exactly as for the 2022 general.  The DOS precinct returns match
   the ENR county-level anchor (2022/20220517__pa__primary__county.csv)
   exactly for every named candidate, including the post-recount certified
   GOP U.S. Senate figures (Oz 5008 / McCormick 4029).  The county workbook
   is an earlier tabulation: its GOP U.S. Senate totals are the pre-recount
   figures (Oz 4999 / McCormick 4021) and its other contests also lag the
   certified final by small amounts (Shapiro 11595 vs 11647, Cartwright
   10597 vs 10667, ...).  The DOS/ENR figures are what this parser emits.

2. "051722_DemWriteins.pdf" and "051722_RepWriteins.pdf" (county Board of
   Elections write-in review reports for the primary,
   monroecountypa.gov/getmedia/...).  Per contest they give, by precinct
   column: "Write-in Votes reviewed but not assigned to a candidate", then
   one row per write-in candidate NAME that received reviewed write-ins
   (cross-party names included, e.g. DAVE MCCORMICK in the DEM U.S. Senate
   race), then "SCATTERED"; write-in-only contests additionally report
   "BLANK" rows (write-in spaces left empty, not votes -- excluded).
   All non-BLANK categories are combined into one `Write Ins` row per
   (precinct, contest), party = the contest's ballot party, per repo
   convention ("party-empty write-ins fail duplicate_entries in primaries").
   For each contest the report's printed per-category "Total" values are
   verified against the parsed per-precinct column sums.
   Interpretation of "Write Ins": the certified workbook's countywide
   "Write-in" rows include the reports' BLANK rows (reviewed write-in spaces
   left empty, not votes for anyone) -- workbook write-in row == not-assigned
   + named + SCATTERED + BLANK for 14 of the 20 contests (plus small
   residuals elsewhere, notably the State Committee contests).  The emitted
   per-precinct Write Ins rows EXCLUDE BLANK; the parser reports the deltas.
   The PDFs also carry per-precinct COMMITTEE-person write-in sections
   (DEMOCRATIC COMMITTEEMAN/WOMAN x precinct, MEMBER OF REPUBLICAN COUNTY
   COMMITTEE); those contests are taken from the certified workbook instead
   and the PDF sections are skipped.
   Quirk: the reports have a single "Ross" column; per county convention
   Ross Township is one precinct but DOS splits it into Ross (USC 7) and
   Ross (USC 8).  As in the general parser, the "Ross" column is Ross (USC 8)
   only inside the CD-8 table and Ross (USC 7) in every other contest
   (DOS shows Ross (USC 8) carrying zeros for every contest but CD-8).

3. The certified workbook's per-precinct COMMITTEE contests, which exist
   only there (DOS omits them): "DEMOCRATIC COMMITTEEMAN/PRECINCT",
   "DEMOCRATIC COMMITTEEWOMAN" and "MEMBER OF REPUBLICAN COUNTY COMMITTEE",
   one contest per precinct.  Emitted with the precinct embedded in the
   office name (the Allegheny 2022-primary convention for per-precinct
   committee races) and party from the contest.  Whole-Ross committee rows
   are attached to Ross (USC 7) (Ross (USC 8) reports only the CD-8 contest).

Not available in any source: Registered Voters / Ballots Cast; per-precinct
breakdowns (election day / mail / provisional); named-candidate per-precinct
votes for the two State Committee contests (countywide only, in the workbook
and the ENR anchor) -> for those contests only per-precinct Write Ins rows
are emitted.  Output is a 7-column file.

Note: the DOS bulk file lacks one Lt. Governor REP candidate row (Jeff
Coleman) for the Ross (USC 8) precinct; the bulk file's row is absent
entirely (every other candidate's Ross (USC 8) row is a zero row, so the
missing value is a zero vote that is NOT invented and no row is emitted).

Usage:
  python pa_monroe_primary_2022_results_parser.py <prcres.txt> <dem_wi.pdf> \
      <rep_wi.pdf> <certified.xlsx> <out.csv> [anchor_county.csv]
"""
from __future__ import annotations

import collections
import csv
import re
import sys
from collections import OrderedDict

import openpyxl
import pdfplumber

COUNTY = "Monroe"
COUNTY_CODE = "45"
YEAR = "2022"
ETYPE = "P"

FIELDNAMES = ["county", "precinct", "office", "district", "party", "candidate", "votes"]

# ---------------------------------------------------------------- precincts
ORDINALS = {1: "One", 2: "Two", 3: "Three", 4: "Four", 5: "Five", 6: "Six", 7: "Seven"}
WARDORDINALS = {1: "First", 2: "Second", 3: "Third", 4: "Fourth",
                5: "Fifth", 6: "Sixth", 7: "Seventh"}

MUNI_NAMES = {
    "BARRETT": "Barrett", "CHESTNUTHILL": "Chestnuthill", "COOLBAUGH": "Coolbaugh",
    "DELAWARE WATER GAP": "Delaware Water Gap", "EAST STROUDSBURG": "East Stroudsburg",
    "ELDRED": "Eldred", "HAMILTON": "Hamilton", "JACKSON": "Jackson",
    "MIDDLE SMITHFIELD": "Middle Smithfield", "MOUNT POCONO": "Mount Pocono",
    "MT. POCONO": "Mount Pocono", "PARADISE": "Paradise", "POCONO": "Pocono",
    "POLK": "Polk", "PRICE": "Price", "SMITHFIELD": "Smithfield", "STROUD": "Stroud",
    "STROUDSBURG": "Stroudsburg", "TOBYHANNA": "Tobyhanna", "TUNKHANNOCK": "Tunkhannock",
}

# (muni base name, breakdown name uppercase) -> county's printed-style suffix
_X_NAMES = {
    ("Hamilton", "NORTH"): "Northern", ("Hamilton", "SOUTH"): "Southern",
    ("Jackson", "NORTH"): "North", ("Jackson", "SOUTH"): "South",
    ("Middle Smithfield", "EAST"): "Eastern", ("Middle Smithfield", "WEST"): "Western",
    ("Tobyhanna", "EAST"): "Eastern", ("Tobyhanna", "WEST"): "Western",
}


def precinct_name(_code: str, muni: str, b1c: str, b1n: str) -> str:
    """Output precinct name from the DOS full precinct description."""
    muni = muni.strip()
    if muni in ("ROSS (USC 7)", "ROSS (USC 8)"):
        return "Ross (USC 7)" if muni.endswith("7)") else "Ross (USC 8)"
    core, xpart = (muni.split(" X ", 1) + [""])[:2]
    if xpart:
        xpart = xpart.strip()
    core = core.strip()
    m = re.match(r"^(.*\S)\s+[DW]\s+(\d+)$", core)
    if m:
        base = MUNI_NAMES.get(m.group(1).strip().upper())
        if base is None:
            raise ValueError(f"unknown Monroe municipality/precinct description: {muni!r}")
        n = int(m.group(2))
        if base in ("East Stroudsburg", "Stroudsburg"):
            return f"{base} {WARDORDINALS[n]} Ward"
        return f"{base} {ORDINALS[n]}"
    base = MUNI_NAMES.get(core.upper())
    if base is None:
        raise ValueError(f"unknown Monroe municipality/precinct description: {muni!r}")
    if b1c == "X" and b1n:
        suffix = _X_NAMES.get((base, b1n.upper()))
        if suffix is None:
            raise ValueError(f"unmapped X breakdown {b1c}/{b1n} for {muni!r}")
        return f"{base} {suffix}"
    return base


# ---------------------------------------------------------------- contests
OFFICE_MAP = {
    "USS": "U.S. Senate",
    "GOV": "Governor",
    "LTG": "Lieutenant Governor",
    "USC": "U.S. House",     # district from the row's district field
    "STS": "State Senate",
    "STH": "State House",
}
NO_DISTRICT = {"USS", "GOV", "LTG"}

# DOS candidate number -> (office code, party, output name).  Names follow the
# short forms already used for the 2022 primary data (Allegheny county file /
# statewide conventions), not the ENR formal variants.
CANDIDATES = {
    "2022C0216": ("USS", "DEM", "Alex Khalil"),
    "2022C0167": ("USS", "DEM", "Conor Lamb"),
    "2022C0126": ("USS", "DEM", "Malcolm Kenyatta"),
    "2022C0203": ("USS", "DEM", "John Fetterman"),
    "2022C0147": ("USS", "REP", "Mehmet Oz"),
    "2022C0176": ("USS", "REP", "Dave McCormick"),
    "2022C0138": ("USS", "REP", "Kathy Barnette"),
    "2022C0142": ("USS", "REP", "Jeff Bartos"),
    "2022C0154": ("USS", "REP", "Carla Sands"),
    "2022C0193": ("USS", "REP", "Sean Gale"),
    "2022C0210": ("USS", "REP", "George Bochetto"),
    "2022C0206": ("GOV", "DEM", "Josh Shapiro"),
    "2022C0161": ("GOV", "REP", "Lou Barletta"),
    "2022C0139": ("GOV", "REP", "Douglas V. Mastriano"),
    "2022C0247": ("GOV", "REP", "Nche Zama"),
    "2022C0163": ("GOV", "REP", "Dave White"),
    "2022C0224": ("GOV", "REP", "Melissa Hart"),
    "2022C0195": ("GOV", "REP", "Bill McSwain"),
    "2022C0238": ("GOV", "REP", "Charlie Gerow"),
    "2022C0192": ("GOV", "REP", "Joe Gale"),
    "2022C0204": ("GOV", "REP", "Jake Corman"),
    "2022C0137": ("LTG", "DEM", "Brian Sims"),
    "2022C0207": ("LTG", "DEM", "Austin Davis"),
    "2022C0249": ("LTG", "DEM", "Ray Sosa"),
    "2022C0145": ("LTG", "REP", "Clarice Schillinger"),
    "2022C0160": ("LTG", "REP", "Teddy Daniels"),
    "2022C0166": ("LTG", "REP", "Rick Saccone"),
    "2022C0169": ("LTG", "REP", "Carrie Lewis DelRosso"),
    "2022C0234": ("LTG", "REP", "Russ Diamond"),
    "2022C0239": ("LTG", "REP", "Chris Frye"),
    "2022C0240": ("LTG", "REP", "James Earl Jones"),
    "2022C0243": ("LTG", "REP", "Jeff Coleman"),
    "2022C0237": ("LTG", "REP", "John Brown"),
    "2022C0200": ("USC", "DEM", "Susan Wild"),
    "2022C0140": ("USC", "REP", "Kevin Dellicker"),
    "2022C0202": ("USC", "REP", "Lisa Scheller"),
    "2022C0183": ("USC", "DEM", "Matt Cartwright"),
    "2022C0157": ("USC", "REP", "Jim Bognet"),
    "2022C0173": ("USC", "REP", "Michael Marsicano"),
    "2022C1120": ("STS", "DEM", "Jennifer Shukaitis"),
    "2022C0274": ("STS", "REP", "Rosemary Brown"),
    "2022C1119": ("STH", "DEM", "Maureen Madden"),
    "2022C0452": ("STH", "REP", "Krista Paolucci"),
    "2022C1117": ("STH", "DEM", "Hope Christman"),
    "2022C1113": ("STH", "REP", "Jack Rader"),
    "2022C1051": ("STH", "DEM", "Tarah Probst"),
    "2022C1132": ("STH", "REP", "Steve Ertle"),
    "2022C1331": ("STH", "REP", "John Petrizzo"),
}

# workbook "LAST, FIRST [MIDDLE]" -> output name (known candidates)
WB_TO_NAME = {
    "KHALIL, ALEX": "Alex Khalil", "LAMB, CONOR": "Conor Lamb",
    "KENYATTA, MALCOLM": "Malcolm Kenyatta", "FETTERMAN, JOHN": "John Fetterman",
    "OZ, MEHMET": "Mehmet Oz", "MCCORMICK, DAVE": "Dave McCormick",
    "BARNETTE, KATHY": "Kathy Barnette", "BARTOS, JEFF": "Jeff Bartos",
    "SANDS, CARLA": "Carla Sands", "GALE, SEAN": "Sean Gale",
    "BOCHETTO, GEORGE": "George Bochetto",
    "SHAPIRO, JOSH": "Josh Shapiro",
    "BARLETTA, LOU": "Lou Barletta", "MASTRIANO, DOUGLAS V": "Douglas V. Mastriano",
    "ZAMA, NCHE": "Nche Zama", "WHITE, DAVE": "Dave White",
    "HART, MELISSA": "Melissa Hart", "MCSWAIN, BILL": "Bill McSwain",
    "GEROW, CHARLIE": "Charlie Gerow", "GALE, JOE": "Joe Gale",
    "CORMAN, JAKE": "Jake Corman",
    "SIMS, BRIAN": "Brian Sims", "DAVIS, AUSTIN": "Austin Davis",
    "SOSA, RAY": "Ray Sosa",
    "SCHILLINGER, CLARICE D": "Clarice Schillinger",
    "DANIELS, THEODORE": "Teddy Daniels", "SACCONE, RICHARD": "Rick Saccone",
    "DELROSSO, CARRIE": "Carrie Lewis DelRosso", "DIAMOND, RUSSELL H": "Russ Diamond",
    "FRYE, CHRISTOPHER C": "Chris Frye", "JONES, JAMES E": "James Earl Jones",
    "COLEMAN, JEFFREY H": "Jeff Coleman", "BROWN, JOHN A": "John Brown",
    "CARTWRIGHT, MATT": "Matt Cartwright", "WILD, SUSAN": "Susan Wild",
    "SCHELLER, LISA": "Lisa Scheller", "BOGNET, JIM": "Jim Bognet",
    "MARSICANO, MICHAEL PAUL": "Michael Marsicano",
    "DELLICKER, KEVIN WILLARD": "Kevin Dellicker",
    "SHUKAITIS, JENNIFER": "Jennifer Shukaitis", "BROWN, ROSEMARY": "Rosemary Brown",
    "MADDEN, MAUREEN E": "Maureen Madden", "PAOLUCCI, KRISTA ROSE": "Krista Paolucci",
    "CHRISTMAN, HOPE": "Hope Christman", "RADER, JACK B": "Jack Rader",
    "PROBST, TARAH": "Tarah Probst", "ERTLE, STEPHEN WALTER": "Steve Ertle",
    "PETRIZZO, JOHN": "John Petrizzo",
    # State Committee (workbook/ENR names; emitted countywide-only -> only
    # used inside verification output)
    "WILLIAMS, CLAUDETTE": "Claudette Williams",
    "TIRACCHIA, ANNE": "Anne Tiracchia", "SANDERS, TIM": "Tim Sanders",
    "CROWNER, CYNTHIA": "Cynthia Crowner", "PRIDE, JACOB A": "Jacob A. Pride",
    "FERRO, JOSEPHINE": "Josephine Ferro", "BIRD, SHELLI": "Shelli Bird",
    "WHITEHEAD, THOMAS CHARLES": "Thomas Charles Whitehead",
}


def wb_name(raw: str) -> str:
    """Workbook 'LAST, FIRST [MIDDLE]' -> output name; generic fallback."""
    raw = " ".join(raw.split())
    if raw in WB_TO_NAME:
        return WB_TO_NAME[raw]
    m = re.match(r"^([A-Z'.\- ]+), (.+)$", raw)
    if not m:
        raise ValueError(f"unmapped workbook candidate name {raw!r}")
    last = re.sub(r"\s+", " ", m.group(1).title()).strip()
    first = " ".join(p.capitalize() for p in m.group(2).split())
    return f"{first} {last}"


# write-in PDF / workbook contest title -> (office, district)
WI_CONTESTS = {
    "US SENATOR": ("U.S. Senate", ""),
    "GOVERNOR": ("Governor", ""),
    "LIEUTENANT GOVERNOR": ("Lieutenant Governor", ""),
    "SENATOR IN THE GENERAL ASSEMBLY 40TH DISTRICT": ("State Senate", "40"),
    "REPRESENTATIVE IN CONGRESS 7TH DISTRICT": ("U.S. House", "7"),
    "REPRESENTATIVE IN CONGRESS 8TH DISTRICT": ("U.S. House", "8"),
    "REPRESENTATIVE IN THE GENERAL ASSEMBLY 115TH DISTRICT": ("State House", "115"),
    "REPRESENTATIVE IN THE GENERAL ASSEMBLY 176TH DISTRICT": ("State House", "176"),
    "REPRESENTATIVE IN THE GENERAL ASSEMBLY 189TH DISTRICT": ("State House", "189"),
    "MEMBER OF DEMOCRATIC STATE COMMITTEE":
        ("Member of Democratic State Committee", "45"),
    "MEMBER OF REPUBLICAN STATE COMMITTEE":
        ("Member of Republican State Committee", "45"),
}

# per-precinct committee contests (workbook only): title prefix ->
# (office stem, party); precinct is embedded in the output office name
COMMITTEE_CONTESTS = {
    "DEMOCRATIC COMMITTEEMAN": ("Democratic Committeeman", "DEM"),
    "DEMOCRATIC COMMITTEEWOMAN": ("Democratic Committeewoman", "DEM"),
    "MEMBER OF REPUBLICAN COUNTY COMMITTEE":
        ("Member of Republican County Committee", "REP"),
}

# write-in PDF column header -> output precinct name ("__ROSS__" per contest)
PDF_NAMES = {
    "Barrett One": "Barrett One", "Barrett Two": "Barrett Two",
    "Chestnuthill One": "Chestnuthill One", "Chestnuthill Two": "Chestnuthill Two",
    "Chestnuthill Three": "Chestnuthill Three", "Chestnuthill Four": "Chestnuthill Four",
    "Coolbaugh One": "Coolbaugh One", "Coolbaugh Two": "Coolbaugh Two",
    "Coolbaugh Three": "Coolbaugh Three", "Coolbaugh Four": "Coolbaugh Four",
    "Delaware Water Gap": "Delaware Water Gap",
    "Eas Str First Ward": "East Stroudsburg First Ward",
    "Eas Str Second Ward": "East Stroudsburg Second Ward",
    "Eas Str Third Ward": "East Stroudsburg Third Ward",
    "Eas Str Fourth Ward": "East Stroudsburg Fourth Ward",
    "Eas Str Fifth Ward": "East Stroudsburg Fifth Ward",
    "Eas Str Sixth Ward": "East Stroudsburg Sixth Ward",
    "Eldred": "Eldred",
    "Hamilton Northern": "Hamilton Northern", "Hamilton Southern": "Hamilton Southern",
    "Jackson North": "Jackson North", "Jackson South": "Jackson South",
    "Mid Smi Eastern": "Middle Smithfield Eastern",
    "Mid Smi Western": "Middle Smithfield Western",
    "Mount Pocono": "Mount Pocono", "Paradise": "Paradise",
    "Pocono One": "Pocono One", "Pocono Two": "Pocono Two",
    "Pocono Three": "Pocono Three", "Pocono Four": "Pocono Four",
    "Polk": "Polk", "Price": "Price",
    "Ross": "__ROSS__",
    "Smithfield One": "Smithfield One", "Smithfield Two": "Smithfield Two",
    "Smithfield Three": "Smithfield Three",
    "Str First Ward": "Stroudsburg First Ward",
    "Str Second Ward": "Stroudsburg Second Ward",
    "Str Third Ward": "Stroudsburg Third Ward",
    "Str Fourth Ward": "Stroudsburg Fourth Ward",
    "Str Fifth Ward": "Stroudsburg Fifth Ward",
    "Str Sixth Ward": "Stroudsburg Sixth Ward",
    "Stroud One": "Stroud One", "Stroud Two": "Stroud Two",
    "Stroud Three": "Stroud Three", "Stroud Four": "Stroud Four",
    "Stroud Five": "Stroud Five", "Stroud Six": "Stroud Six",
    "Stroud Seven": "Stroud Seven",
    "Tobyhanna Eastern": "Tobyhanna Eastern", "Tobyhanna Western": "Tobyhanna Western",
    "Tunkhannock": "Tunkhannock",
}

BLANK_LABEL = "BLANK"
_NUMROW = "Total Votes - visual inspection"


def _cell(v) -> str:
    return " ".join(str(v).split()) if v is not None else ""


def resolve_pdf_precinct(name: str, district: str) -> str:
    """Map a write-in PDF column header to the output precinct name."""
    mapped = PDF_NAMES.get(name.strip())
    if mapped is None:
        raise ValueError(f"unknown write-in column header {name!r}")
    if mapped == "__ROSS__":
        # Ross (USC 8) carries only the CD-8 contest; "Ross" columns in every
        # other contest are Ross (USC 7).
        return "Ross (USC 8)" if district == "8" else "Ross (USC 7)"
    return mapped


# ------------------------------------------------------------ prcres reader
def read_prcres(path: str):
    """Read Monroe 2022 primary rows from the DOS bulk precinct returns file."""
    contests: OrderedDict = OrderedDict()  # (precinct, office, district) -> entry
    seen_prec: set = set()
    n_rows = 0
    with open(path, newline="") as fh:
        for r in csv.reader(fh):
            if len(r) < 27 or r[0] != YEAR or r[1] != ETYPE or r[2] != COUNTY_CODE:
                continue
            n_rows += 1
            off_code, cand_no = r[8], r[10]
            if off_code not in OFFICE_MAP:
                raise ValueError(f"unhandled office code {off_code!r} ({r[22]})")
            cand = CANDIDATES.get(cand_no)
            if cand is None:
                raise ValueError(f"unhandled candidate number {cand_no}")
            if cand[0] != off_code:
                raise ValueError(f"contest mismatch for {cand_no} in {off_code}")
            if cand[1] != r[9]:
                raise ValueError(f"party mismatch for {cand_no}: {r[9]}")
            district = "" if off_code in NO_DISTRICT else str(int(r[5]))
            prec = precinct_name(str(int(r[3])), r[22], r[23], r[24])
            seen_prec.add(prec)
            key = (prec, OFFICE_MAP[off_code], district)
            entry = contests.setdefault(key, OrderedDict())
            entry[(cand[2], cand[1])] = entry.get((cand[2], cand[1]), 0) + int(r[15])
    print(f"prcres: {n_rows} rows -> {len(contests)} (precinct, contest) "
          f"groups, {len(seen_prec)} precincts")
    return contests


# ------------------------------------------------------------ write-in PDFs
def parse_writein_pdf(path: str):
    """Per-precinct write-in votes from a county write-in review report.

    Returns (out, blanks) where
      out = {(precinct, office, district, party): votes} summed over
            the contest's non-BLANK labels (not-assigned + named write-in
            candidates + scattered),
      blanks = {(office, district, party): printed BLANK total}.
    """
    out: dict = {}
    blanks: dict = {}
    contest = None             # (office, district, party) or None (skip mode)
    title_key = None           # title text of the active section, for dedupe
    labels: list = []
    printed: dict = {}         # label -> printed Total on the first segment
    parsed: dict = {}          # label -> sum over all segments' precinct columns
    cur: dict = {}             # (prec,office,dist,party) -> non-BLANK votes
    table_seen: set = set()    # (title text, table content) already parsed

    def flush():
        if contest is None:
            return
        for lab in labels:
            if lab not in printed:
                raise ValueError(f"{contest}: label {lab!r} has no printed Total")
            if parsed.get(lab, 0) != printed[lab]:
                raise ValueError(f"{contest}: label {lab!r} parsed precinct sum "
                                 f"{parsed.get(lab, 0)} != printed Total {printed[lab]}")
        office, district, party = contest
        for k, v in cur.items():
            out[k] = out.get(k, 0) + v
        if printed.get(BLANK_LABEL):
            blanks[(office, district, party)] = blanks.get(
                (office, district, party), 0) + printed[BLANK_LABEL]

    def start(title: str):
        m = re.match(r"^(.*)\s+\((DEM|REP)\)$", title)
        if not m or m.group(1) not in WI_CONTESTS:
            raise ValueError(f"unknown contest title {title!r}")
        office, district = WI_CONTESTS[m.group(1)]
        return (office, district, m.group(2)), [office, district, m.group(2)]

    with pdfplumber.open(path) as pdf:
        for pno, page in enumerate(pdf.pages):
            for table in page.extract_tables() or []:
                if not table:
                    continue
                cells = [[_cell(c) for c in row] for row in table]
                # contest title row? ("Monre County ...", "TITLE (PARTY)")
                new_title = None
                if cells and len(cells[0]) > 1 and re.match(r"Monre County", cells[0][0]):
                    new_title = cells[0][1]
                # the reports repeat some sections verbatim (e.g. the REP
                # State Committee pageset is printed twice); skip exact
                # duplicate segments within the same title section
                key = (new_title if new_title is not None else title_key,
                       tuple(map(tuple, cells)))
                if key in table_seen:
                    continue
                table_seen.add(key)
                title = new_title
                if title is not None:
                    flush()
                    title_key = title
                    m_skip = re.match(r"^(DEMOCRATIC COMMITTEEMAN|"
                                      r"DEMOCRATIC COMMITTEEWOMAN|"
                                      r"MEMBER OF REPUBLICAN COUNTY COMMITTEE)\b",
                                      title)
                    if m_skip:
                        contest, labels, printed, parsed, cur = None, [], {}, {}, {}
                        continue           # committee sections: skip (use workbook)
                    contest, _ = start(title)
                    labels, printed, parsed, cur = [], {}, {}, {}

                if contest is None:
                    continue

                # header row: first row carrying a 'Total' cell (first segment)
                # or a non-all-numeric row (continuation segment)
                hdr = None
                for row in cells:
                    if row[0] and re.match(r"Monre County", row[0]):
                        continue                      # contest title row
                    if "Precinct" in row:
                        continue                      # column-label banner row
                    if "Total" in row:
                        hdr = row
                        break
                    nonempty = [c for c in row if c]
                    if nonempty and not all(
                            re.fullmatch(r"\d{1,4}", c) for c in nonempty):
                        hdr = row
                        break
                if hdr is None:
                    continue
                is_first = "Total" in hdr
                idx_total = hdr.index("Total") if is_first else None

                # collect labels on the contest's first segment
                if is_first:
                    for row in cells:
                        c0 = row[0]
                        if c0.startswith(_NUMROW) or not c0 \
                                or c0 == "Precinct" or re.match(r"Monre County", c0):
                            continue
                        if c0 not in labels:
                            labels.append(c0)
                            printed[c0] = None
                            parsed[c0] = 0
                    if not labels:
                        raise ValueError(f"{contest} page {pno}: no labels found")

                # data rows: first segment rows carry the label text in col 0
                # with the "Total Votes - visual inspection..." string;
                # continuation segments carry bare all-numeric rows in the
                # same label order.
                data = []
                for row in cells:
                    if row[0].startswith(_NUMROW):
                        data.append(row)
                    elif row is not hdr and sum(1 for c in row if c) >= 1 and all(
                            re.fullmatch(r"\d{1,4}", c) for c in row if c):
                        data.append(row)
                if not data:
                    continue
                if len(data) != len(labels):
                    raise ValueError(
                        f"{contest} page {pno}: {len(data)} data rows on a "
                        f"segment vs {len(labels)} contest labels")
                for lab, row in zip(labels, data):
                    for i, name in enumerate(hdr):
                        if not name or name == "Total" or name == "Precinct":
                            continue
                        if i >= len(row):
                            raise ValueError(
                                f"{contest} page {pno}: row shorter than header")
                        v = int(row[i])
                        if not v and not re.fullmatch(r"\d{1,4}", row[i]):
                            raise ValueError("unreachable")
                        prec = resolve_pdf_precinct(name, contest[1])
                        parsed[lab] += v
                        if lab != BLANK_LABEL:
                            k = (prec, contest[0], contest[1], contest[2])
                            cur[k] = cur.get(k, 0) + v
                    if is_first:
                        printed[lab] = int(row[idx_total])
    flush()
    return out, blanks


def parse_writeins(dem_path: str, rep_path: str):
    out, blanks = parse_writein_pdf(dem_path)
    n1 = len(out)
    out2, blanks2 = parse_writein_pdf(rep_path)
    out.update(out2)
    blanks.update(blanks2)
    out = {k: v for k, v in out.items() if v}   # zero write-ins: no row emitted
    print(f"write-in reports: {n1} DEM + {len(out) - n1} REP "
          f"non-zero (precinct, contest) entries")
    return out, blanks


# ------------------------------------------------------- certified workbook
def read_workbook(xlsx_path: str, precinct_names: set):
    """(countywide, committee) from the county certified workbook.

    countywide: {(office, district, party): {"candidates": {out_name: votes},
                                             "writein": v, "total": v}}
    committee: {(office_w_precinct, precinct, party): {out_name: votes}}

    The workbook truncates long contest titles, so committee precinct names
    are matched by unique prefix against the DOS precinct list.
    """
    wb = openpyxl.load_workbook(xlsx_path, data_only=True)
    ws = wb["ElectionData"]
    countywide: dict = {}
    committee: OrderedDict = OrderedDict()

    def match_precinct(raw: str) -> str:
        # truncated titles can end mid-party-suffix: "Stroudsburg First Ward (RE"
        mfrag = re.search(r"\s*\(([^)]*)$", raw)
        if mfrag and re.fullmatch(r"(?i)D|DE|DEM|R|RE|REP", mfrag.group(1)):
            raw = raw[:mfrag.start()].strip()
        if raw == "Ross":
            return "Ross (USC 7)"
        if raw in precinct_names:
            return raw
        hits = [p for p in precinct_names
                if p.startswith(raw) and "(USC" not in p]
        if len(hits) != 1:
            raise ValueError(f"workbook: precinct title fragment {raw!r} "
                             f"matches {hits}")
        return hits[0]

    for row in ws.iter_rows(min_row=2, values_only=True):
        title = row[0]
        if title is None:
            continue
        title = str(title).strip()
        votes = int(row[7])
        m = re.match(r"^(.*)\s+\((DEM|REP)\)$", title)
        party = m.group(2) if m else None
        base = m.group(1) if m else title
        cm = re.match(r"^(.*?) - Precinct (.*)$", base)
        if cm and cm.group(1) in COMMITTEE_CONTESTS:
            stem, cparty = COMMITTEE_CONTESTS[cm.group(1)]
            prec = match_precinct(cm.group(2).strip())
            office = f"{stem} {prec}"
            cand = str(row[6]).strip()
            cand = "Write Ins" if cand.lower().startswith("write-in") else wb_name(cand)
            entry = committee.setdefault((office, prec, cparty), OrderedDict())
            entry[cand] = entry.get(cand, 0) + votes
        else:
            if party is None or base not in WI_CONTESTS:
                raise ValueError(f"workbook: unknown contest {title!r}")
            office, district = WI_CONTESTS[base]
            ck = (office, district, party)
            entry = countywide.setdefault(
                ck, {"candidates": OrderedDict(), "writein": 0, "total": None})
            entry["total"] = int(row[2])
            cand = str(row[6]).strip()
            if cand.lower().startswith("write-in"):
                entry["writein"] += votes
            else:
                name = wb_name(cand)
                entry["candidates"][name] = entry["candidates"].get(name, 0) + votes
    return countywide, committee


# ---------------------------------------------------------------- output
def build_rows(prcres_path, dem_wi, rep_wi, xlsx):
    contests = read_prcres(prcres_path)
    wi, wi_blanks = parse_writeins(dem_wi, rep_wi)
    precinct_names = {prec for (prec, _, _) in contests}
    countywide, committee = read_workbook(xlsx, precinct_names)

    merged: OrderedDict = OrderedDict()  # (prec,office,dist) -> {(name,party): v}
    for (prec, office, district), cands in contests.items():
        merged[(prec, office, district)] = OrderedDict(cands)
    for (prec, office, district, party), v in sorted(
            wi.items(), key=lambda kv: (kv[0][0], kv[0][1], kv[0][2])):
        key = (prec, office, district)
        if key not in merged:
            if office not in ("Member of Democratic State Committee",
                              "Member of Republican State Committee"):
                raise ValueError(f"write-in for contest with no named rows: {key}")
            # State Committee named candidates are countywide-only everywhere;
            # the precinct detail that exists is write-ins from the county
            # write-in review reports.
            merged[key] = OrderedDict()
        merged[key][("Write Ins", party)] = merged[key].get(("Write Ins", party), 0) + v

    rows = []
    for (prec, office, district), cands in merged.items():
        for (name, party), votes in cands.items():
            rows.append({"county": COUNTY, "precinct": prec, "office": office,
                         "district": district, "party": party, "candidate": name,
                         "votes": votes})
    n_committee = 0
    for (office, prec, cparty), cands in committee.items():
        for name, votes in cands.items():
            if name == "Write Ins" and votes == 0:
                continue
            rows.append({"county": COUNTY, "precinct": prec, "office": office,
                         "district": "", "party": cparty, "candidate": name,
                         "votes": votes})
            n_committee += 1
    print(f"rows: {len(rows)} total ({len(rows) - n_committee} contest + "
          f"{n_committee} committee)")
    return rows, contests, wi, wi_blanks, countywide, committee


def write_csv(path: str, rows: list):
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDNAMES)
        w.writeheader()
        for r in rows:
            w.writerow(r)
    print(f"wrote {len(rows)} rows -> {path}")


# ------------------------------------------------------------- verification
ANCHOR_TO_NAME = {
    "Joshua D. Shapiro": "Josh Shapiro",
    "Charles R. Gerow": "Charlie Gerow", "David J. White": "Dave White",
    "Douglas V. Mastriano": "Douglas V. Mastriano",
    "Jacob Doyle Corman III": "Jake Corman", "Joseph Charles Gale": "Joe Gale",
    "Louis Barletta": "Lou Barletta", "Melissa A. Hart": "Melissa Hart",
    "William M. McSwain": "Bill McSwain", "Nche Zama": "Nche Zama",
    "Austin Davis": "Austin Davis", "Brian Sims": "Brian Sims",
    "Raymond L. Sosa": "Ray Sosa",
    "Carrie Delrosso": "Carrie Lewis DelRosso",
    "Christopher C. Frye Jr": "Chris Frye",
    "Clarice D. Schillinger": "Clarice Schillinger",
    "James E. Jones": "James Earl Jones", "Jeffrey H. Coleman": "Jeff Coleman",
    "John A. Brown": "John Brown", "Richard Saccone": "Rick Saccone",
    "Russell H. Diamond": "Russ Diamond", "Theodore Daniels": "Teddy Daniels",
    "Maureen E. Madden": "Maureen Madden",
    "Krista Rose Paolucci": "Krista Paolucci",
    "Hope Christman": "Hope Christman", "Jack B. Rader Jr": "Jack Rader",
    "Tarah Probst": "Tarah Probst", "John Petrizzo": "John Petrizzo",
    "Stephen Walter Ertle": "Steve Ertle",
    "Jennifer Anne Shukaitis": "Jennifer Shukaitis",
    "Rosemary Maula Brown": "Rosemary Brown",
    "Susan Ellis Wild": "Susan Wild",
    "Kevin Willard Dellicker": "Kevin Dellicker",
    "Lisa Jane Scheller": "Lisa Scheller",
    "Matthew A. Cartwright": "Matt Cartwright",
    "James Rocco Bognet": "Jim Bognet",
    "Michael Paul Marsicano": "Michael Marsicano",
    "Alexandria Gloria Khalil": "Alex Khalil", "Conor J. Lamb": "Conor Lamb",
    "John K. Fetterman": "John Fetterman", "Malcolm Kenyatta": "Malcolm Kenyatta",
    "Carla Herd Sands": "Carla Sands", "David H. McCormick": "Dave McCormick",
    "George A. Bochetto": "George Bochetto", "Jeffrey A. Bartos": "Jeff Bartos",
    "Kathy J. Barnette": "Kathy Barnette", "Mehmet C. Oz": "Mehmet Oz",
    "Sean Peter Gale": "Sean Gale",
}


def verify_anchor(rows, contests, wi, wi_blanks, countywide, anchor_path):
    """Verify write-ins against the workbook and named candidates against the
    ENR anchor, then report the (small, expected) workbook deltas."""
    print("\n--- verification ---")

    # 1. write-in report per-precinct sums vs the certified workbook;
    #    the workbook's Write-in rows include the reports' BLANK spaces
    print("write-in report per-precinct sums vs certified workbook write-in rows")
    print("(our rows exclude BLANK = empty write-in spaces):")
    mism = 0
    for ck, entry in countywide.items():
        office, district, party = ck
        wi_sum = sum(v for (prec, off, dist, p), v in wi.items()
                     if off == office and dist == district and p == party)
        blank = wi_blanks.get(ck, 0)
        expect = entry["writein"] - blank
        ok = wi_sum == expect
        mism += 0 if ok else 1
        note = "exact" if ok else "MISMATCH"
        print(f"  {office or '-'} {district or '-'} {party}: ours {wi_sum} "
              f"vs workbook {entry['writein']} - BLANK {blank} = {expect} -> {note}"
              + ("" if ok else f" (delta {wi_sum - expect:+d})"))

    # 2. named candidates vs the ENR anchor (candidate level)
    anchor: dict = {}
    with open(anchor_path, newline="") as fh:
        for r in csv.DictReader(fh):
            if r["county"] != COUNTY:
                continue
            anchor[(r["office"], r["district"], r["party"], r["candidate"])] =                 int(r["votes"])
    ours: collections.defaultdict = collections.defaultdict(int)
    for r in rows:
        if r["candidate"] == "Write Ins" or r["office"].startswith(
                ("Democratic Committeeman", "Democratic Committeewoman",
                 "Member of Republican County Committee")):
            continue
        ours[(r["office"], r["district"], r["party"], r["candidate"])] += r["votes"]
    exact = mismatches = 0
    for k, v in sorted(anchor.items()):
        office, dist, party, cand = k
        if (office, dist, party) not in countywide:
            continue          # State Committee: no per-precinct named rows
        mapped = ANCHOR_TO_NAME.get(cand, cand)
        got = ours.get((office, dist, party, mapped), 0)
        if got == v:
            exact += 1
        else:
            mismatches += 1
            print(f"  ANCHOR MISMATCH {office} {dist} {party} {cand} "
                  f"(as {mapped}): ours {got} vs anchor {v}")
    print(f"ENR anchor candidate-level: {exact} exact, {mismatches} mismatch(es); "
          f"State Committee named candidates are countywide-only and not "
          f"emitted (per-precinct write-ins only), detailed below")
    for (office, district, party), entry in sorted(countywide.items()):
        if office not in ("Member of Democratic State Committee",
                          "Member of Republican State Committee"):
            continue
        a = sum(v for (off, d, p, c), v in anchor.items()
                if off == office and d == district and p == party)
        print(f"  {office} {party}: anchor named-candidate sum {a}, "
              f"certified workbook named {entry['total'] - entry['writein']}, "
              f"workbook write-in row {entry['writein']}, our per-precinct "
              f"write-ins {sum(v for (pp, off, dd, p2), v in wi.items() if off == office)}")

    # 3. per-contest ours vs anchor vs workbook named-candidate sums
    print("contest party sums (ours=anchor expected; workbook is the older tabulation):")
    for (office, district, party), entry in sorted(countywide.items()):
        o = sum(v for (off, d, p, c), v in ours.items()
                if off == office and d == district and p == party)
        a = sum(v for (off, d, p, c), v in anchor.items()
                if off == office and d == district and p == party)
        wb = entry["total"] - entry["writein"]
        flag = "" if o == a or a == 0 else "  <-- MISMATCH vs anchor"
        print(f"  {office or '-'} {district or '-'} {party}: ours {o}, anchor {a}, "
              f"workbook {wb}{flag}")
    return mism


def main(argv):
    if len(argv) < 6:
        print(__doc__)
        return 2
    prcres, dem_wi, rep_wi, xlsx, outfile = argv[1:6]
    rows, contests, wi, wi_blanks, countywide, committee = build_rows(
        prcres, dem_wi, rep_wi, xlsx)
    write_csv(outfile, rows)
    if len(argv) > 6:
        # write-in residuals vs the workbook are documented source-level
        # discrepancies, not parse errors -> informational only
        verify_anchor(rows, contests, wi, wi_blanks, countywide, argv[6])
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))