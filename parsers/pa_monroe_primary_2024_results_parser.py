#!/usr/bin/env python3
"""Parse Monroe County 2024 primary election precinct results.

Sources (in parse order):

1. PA Department of State bulk precinct-returns file for the 2024 Primary
   (ElectionReturns_2024_Primary_PrecinctReturns.txt from
   https://www.pa.gov/agencies/dos/resources/voting-and-elections-resources/
   voting-and-election-statistics/election-data, county code 45 = Monroe).
   The county's own "Monroe PA 2024 Primary Summary Results.xlsx" is
   countywide-only (contest-level Candidate Votes, no per-precinct detail),
   so the DOS certified bulk file is the precinct-level source, exactly as
   for the Monroe 2022 primary.  The DOS rows carry every named candidate
   for all 26 Monroe contests (55 precincts), and their county sums match
   the summary workbook's Candidate Votes / Total Votes exactly (verified
   below).  Monroe ran no Supreme/Superior/Commonwealth Court or party-
   committee contests on its primary ballot, and the source carries no
   Registered Voters / Ballots Cast rows and no election-day/mail/
   provisional breakdowns, so output is a 7-column file.

2. "Monroe PA 2024 Primary Official Write-In Results by Precinct.pdf"
   (county Board of Elections write-in review report, one table per
   contest, 54 precinct columns + Grand Total per row; the county's table
   omits a Stroudsburg First Ward column, which is all-zero in every
   contest).  Per contest the report prints one row per category: the
   named write-in candidates that received reviewed votes (cross-party
   names included, e.g. JOSEPH R BIDEN JR under PRESIDENT (REP),
   DAVE MCCORMICK under US SENATOR (DEM), JACK RADER under the 176th
   District (DEM)), then "Scattered", then "Invalid" (write-in spaces left
   blank / on-ballot-candidate names / not-a-name).  For each reported
   category the parsed per-precinct column sum is verified to equal the
   row's printed Grand Total, and the merged per-precinct Write-In Totals
   are verified to equal the summary workbook's Write-in rows for all 26
   contests.

   The county's certified "Write-in" count for a contest INCLUDES the
   Invalid rows (USP (DEM): named 81+57, Scattered 229, Invalid 6,
   workbook Write-in 373 = named + Scattered + Invalid), and the contest
   Total Votes equal named ballots + that write-in count.  Per repo
   convention a single "Write-In Totals" row is therefore emitted per
   (precinct, contest) = named + Scattered + Invalid, which makes every
   contest's precinct-row sum equal the workbook Total Votes.  The row
   granularity detail (per-precinct Write-In Totals) only exists in this
   PDF; rows are emitted only where the value is non-zero.

   PDF parsing quirk: the report pages are rotated 90 degrees, so this
   parser rebuilds the landscape table from pdfplumber character
   coordinates (view row = H - cy, view column = cx), clusters the digit
   characters into cells against the 55 header-column x positions
   (54 precinct columns + Grand Total), and assigns each label row to the
   contest title above it in reading order.  Reading order differs
   between the two landscape pages (page 1 lists its contests top-down
   with labels below their title; page 2's trailing label rows belong to
   the first title of the report), so the parser assigns each label row
   to the next title title-vx-wise (owner = smallest title position >
   label position on the page, falling back to the document's first
   title); every contest is verified against the workbook, so a
   misassignment would fail loudly.  The report's footer "Grand Total"
   row is ignored.

   Precinct naming: the report's own column headers (Ross One, Ross Two,
   Tobyhanna Eastern, Middle Smithfield One-Four, ...) are used, matching
   the county's precinct-level 2024 general results file and the 2022
   primary data.  DOS 2024 splits Ross Township by congressional district
   into "ROSS (USC 7)" and "ROSS (USC 8)"; the county's two precincts are
   Ross One / Ross Two, and the parser maps DOS code 235 (USC 7) -> Ross
   One, 240 (USC 8) -> Ross Two by ascending precinct code (the county's
   numbered precinct order); with the county's 2024 general results the
   mapping is consistent (the larger precinct, Ross Two, carries the
   larger vote in both sources).

Usage:
  python pa_monroe_primary_2024_results_parser.py <prcres.txt> <writein.pdf> \
      <out.csv> [summary.xlsx]
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
COUNTY_CODE = 45
YEAR = 2024
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
    "MIDDLE SMITHFIELD": "Middle Smithfield", "MT. POCONO": "Mount Pocono",
    "PARADISE": "Paradise", "POCONO": "Pocono", "POLK": "Polk", "PRICE": "Price",
    "SMITHFIELD": "Smithfield", "STROUD": "Stroud", "STROUDSBURG": "Stroudsburg",
    "TOBYHANNA": "Tobyhanna", "TUNKHANNOCK": "Tunkhannock",
}


def precinct_name(prec_code: int, muni: str, b1c: str, b1n: str) -> str:
    """Output precinct name from the DOS full precinct description."""
    muni = " ".join(str(muni).split())
    if muni == "ROSS (USC 7)":
        return "Ross One" if prec_code == 235 else _ross_error(prec_code, muni)
    if muni == "ROSS (USC 8)":
        return "Ross Two" if prec_code == 240 else _ross_error(prec_code, muni)
    core, xpart = (muni.split(" X ", 1) + [""])[:2]
    xpart = xpart.strip()
    if xpart and not (b1n or "").strip():
        # description suffix carries the breakdown itself (HAMILTON X NORTH)
        b1c, b1n = "X", xpart
    core = core.strip()
    m = re.match(r"^(.*\S)\s+[DW]\s+([0-9]+)(?:\s+X\s+([0-9]+)(?:ST|ND|RD|TH)?)?"
                 r"(?:\s+CONGRESSIONAL)?$", core + ((" X " + xpart) if xpart else ""),
                 re.IGNORECASE)
    if m:
        base = MUNI_NAMES.get(m.group(1).strip().upper())
        if base is None:
            raise ValueError(f"unknown Monroe municipality description: {muni!r}")
        n = int(m.group(2))
        if base == "Stroudsburg":
            return f"{base} {WARDORDINALS[n]} Ward"
        if base == "East Stroudsburg":
            return f"{base} {WARDORDINALS[n]} Ward"
        return f"{base} {ORDINALS[n]}"
    base = MUNI_NAMES.get(core.upper())
    if base is None:
        raise ValueError(f"unknown Monroe municipality description: {muni!r}")
    if b1c == "D" and b1n:           # e.g. Middle Smithfield's plain districts
        n = int(b1n)
        return f"{base} {ORDINALS[n]}"
    # Hamilton/Jackson N/S, Middle Smithfield districts, Tobyhanna East/West,
    # Tunkhannock East/West
    b = b1n.strip().upper()
    if not b and not (b1c or "").strip():
        return base
    if b in ("NORTH", "SOUTH") and base == "Jackson":
        return f"{base} {b.title()}"
    if b in ("NORTH", "SOUTH") and base == "Hamilton":
        return f"{base} {'Northern' if b == 'NORTH' else 'Southern'}"
    if base == "Tobyhanna" and b in ("EAST", "WEST"):
        return f"{base} Eastern" if b == "EAST" else f"{base} Western"
    if base == "Tunkhannock" and b in ("EAST", "WEST"):
        return f"{base} {b.title()}"
    if b.replace(".0", "").isdigit():    # Middle Smithfield X 1-4 ('1.0')
        return f"{base} {ORDINALS[int(float(b))]}"
    raise ValueError(f"unmapped breakdown {b1c}/{b1n} for {muni!r}")


def _ross_error(prec_code, muni):
    raise ValueError(f" Ross mapping for code {prec_code} ({muni})")


# ---------------------------------------------------------------- contests
OFFICE_CODES = {
    "USP": "President",
    "USS": "U.S. Senate",
    "ATT": "Attorney General",
    "AUD": "Auditor General",
    "TRE": "State Treasurer",
    "USC": "U.S. House",
    "STH": "State House",
    "DED": "Delegate to the Democratic National Convention",
    "DER": "Delegate to the Republican National Convention",
    "ADR": "Alternate Delegate to the Republican National Convention",
    "DSC": "Member of Democratic State Committee",
    "RSC": "Member of Republican State Committee",
}
# offices whose rows are districted (district taken from the DOS row)
DISTRICTED = {"USC", "STH", "DED", "DER", "ADR", "STS"}

# DOS candidate number -> output name
CANDIDATES = {
    "2024C0558": "Joseph R. Biden, Jr.",
    "2024C0754": "Dean Phillips",
    "2024C0517": "Nikki Haley",
    "2024C0271": "Donald J. Trump",
    "2024C0545": "Robert P. Casey, Jr.",
    "2024C0018": "Dave McCormick",
    "2024C0378": "Jack Stollsteimer",
    "2024C0277": "Eugene Depasquale",
    "2024C0267": "Joe Khan",
    "2024C0026": "Keir Bradford-Grey",
    "2024C0721": "Jared Solomon",
    "2024C0238": "Dave Sunday",
    "2024C0791": "Craig Williams",
    "2024C0008": "Malcolm Kenyatta",
    "2024C0286": "Mark Pinsley",
    "2024C0276": "Timothy DeFoor",
    "2024C0036": "Ryan Bizzarro",
    "2024C0748": "Erin McClelland",
    "2024C0072": "Stacy Garrity",
    "2024C0405": "Susan Wild",
    "2024C0013": "Ryan Mackenzie",
    "2024C0083": "Kevin Dellicker",
    "2024C0393": "Maria Montero",
    "2024C0455": "Matt Cartwright",
    "2024C0219": "Rob Bresnahan Jr.",
    "2024C0544": "Maureen Madden",
    "2024C0317": "Matt Long",
    "2024C0783": "Hope Christman",
    "2024C0435": "Jack Rader",
    "2024C0220": "Tarah Probst",
    "2024C0607": "Lisa VanWhy",
    "2024C0645": "Aidan Levinson",
    "2024C0651": "Anne Wakabayashi",
    "2024C0648": "Lori McFarland",
    "2024C0662": "Leslie Altieri",
    "2024C0652": "Carmen Bell",
    "2024C0644": "Baron Vanderburg",
    "2024C0654": "Julian Guridy",
    "2024C0664": "John Hellman",
    "2024C0659": "Margaux Bonin",
    "2024C0656": "Grace Kramer",
    "2024C0658": "Matt McGloin",
    "2024C0663": "Andrew Cutillo",
    "2024C0279": "Patrick Cubbage",
    "2024C0306": "Tom Carroll",
    "2024C0065": "Rochelle Pasquariello",
    "2024C0087": "Jacqueline Rivera",
    "2024C0765": "Eve Cubbage",
    "2024C0086": "Robert E. Smith Jr.",
    "2024C0803": "Angelo LaRocca",
    "2024C0590": "James Paul Hutchison",
    "2024C0562": "T. Lynette Villano",
    "2024C0561": "Michael Giannetta",
    "2024C0332": "Matthew Contreras",
    "2024C0264": "Peter Quaglia",
    "2024C0502": "Dolores Strish",
    "2024C0373": "Thomas Whitehead",
    "2024C0261": "Robin Medeiros",
    "2024C0588": "James Paul Hutchison",
    "2024C0372": "Thomas Whitehead",
    "2024C0498": "Paul DeFabo",
    "2024C0513": "Hector Ramirez",
    "2024C0804": "Angelo LaRocca",
    "2024C0262": "Robin Medeiros",
    "2024C0265": "Peter Quaglia",
}

# contest title in the write-in PDF -> (office code, district); party parsed
WI_TITLES = {
    "PRESIDENT OF THE UNITED STATES": ("USP", ""),
    "US SENATOR": ("USS", ""),
    "ATTORNEY GENERAL": ("ATT", ""),
    "AUDITOR GENERAL": ("AUD", ""),
    "STATE TREASURER": ("TRE", ""),
    "REPRESENTATIVE IN CONGRESS 7TH DISTRICT": ("USC", "7"),
    "REPRESENTATIVE IN CONGRESS 8TH DISTRICT": ("USC", "8"),
    "REPRESENTATIVE IN THE GENERAL ASSEMBLY 115TH DISTRICT": ("STH", "115"),
    "REPRESENTATIVE IN THE GENERAL ASSEMBLY 176TH DISTRICT": ("STH", "176"),
    "REPRESENTATIVE IN THE GENERAL ASSEMBLY 189TH DISTRICT": ("STH", "189"),
    "DELEGATE TO THE DEMOCRATIC NATIONAL CONVENTION 7TH CONG": ("DED", "7"),
    "DELEGATE TO THE DEMOCRATIC NATIONAL CONVENTION 8TH CONG": ("DED", "8"),
    "DELEGATE TO THE REPUBLICAN NATIONAL CONVENTION 7TH CONG": ("DER", "7"),
    "DELEGATE TO THE REPUBLICAN NATIONAL CONVENTION 8TH CONG": ("DER", "8"),
    "ALTERNATE DELEGATE TO THE REPUBLICAN NATIONAL CONV 7TH": ("ADR", "7"),
    "ALTERNATE DELEGATE TO THE REPUBLICAN NATIONAL CONV 8TH": ("ADR", "8"),
}

# write-in PDF report column headers (read from the page-1 header band by the
# caller's verification path); positions were measured from the source PDF.
EXPECTED_WI_COLUMNS = [
    ("Barrett One", 355.9), ("Barrett Two", 371.2), ("Chestnuthill Four", 386.6),
    ("Chestnuthill One", 401.9), ("Chestnuthill Three", 417.3),
    ("Chestnuthill Two", 432.7), ("Coolbaugh Four", 448.0), ("Coolbaugh One", 463.4),
    ("Coolbaugh Three", 478.7), ("Coolbaugh Two", 494.1),
    ("Delaware Water Gap", 509.5), ("East Stroudsburg Fifth Ward", 524.8),
    ("East Stroudsburg First Ward", 540.2), ("East Stroudsburg Fourth Ward", 555.5),
    ("East Stroudsburg Second Ward", 570.9), ("East Stroudsburg Sixth Ward", 586.2),
    ("East Stroudsburg Third Ward", 601.6), ("Eldred", 617.0),
    ("Hamilton Northern", 632.3), ("Hamilton Southern", 647.7),
    ("Jackson North", 663.0), ("Jackson South", 678.4),
    ("Middle Smithfield Four", 693.8), ("Middle Smithfield One", 709.1),
    ("Middle Smithfield Three", 724.5), ("Middle Smithfield Two", 739.8),
    ("Mount Pocono", 755.2), ("Paradise", 770.5), ("Pocono Four", 785.9),
    ("Pocono One", 801.3), ("Pocono Three", 816.6), ("Pocono Two", 832.0),
    ("Polk", 849.3), ("Price", 864.6), ("Ross One", 880.0), ("Ross Two", 895.3),
    ("Smithfield One", 910.7), ("Smithfield Three", 926.1), ("Smithfield Two", 941.4),
    ("Stroud Five", 956.8), ("Stroud Four", 972.1), ("Stroud One", 987.5),
    ("Stroud Seven", 1002.8), ("Stroud Six", 1018.2), ("Stroud Three", 1033.6),
    ("Stroud Two", 1048.9), ("Stroudsburg Fifth Ward", 1064.3),
    ("Stroudsburg Fourth Ward", 1079.6), ("Stroudsburg Second Ward", 1095.0),
    ("Stroudsburg Third Ward", 1110.3), ("Tobyhanna Eastern", 1125.7),
    ("Tobyhanna Western", 1143.0), ("Tunkhannock East", 1158.3),
    ("Tunkhannock West", 1173.7), ("__TOTAL__", 1195.3),
]


# ------------------------------------------------------------ prcres reader
def read_prcres(path: str):
    """Read Monroe 2024 primary rows from the DOS bulk precinct returns file
    (the 2024 file is float-formatted and carries the 2022 schema plus trailing
    breakdown/previous-precinct fields)."""
    contests: "OrderedDict[tuple, OrderedDict]" = OrderedDict()
    seen_prec: "OrderedDict[int, str]" = OrderedDict()
    n_rows = 0
    with open(path, newline="") as fh:
        for r in csv.reader(fh):
            if len(r) < 25:
                continue
            if r[1] != ETYPE or int(float(r[2])) != COUNTY_CODE or \
                    int(float(r[0])) != YEAR:
                continue
            n_rows += 1
            off_code, cand_no = r[8], r[10]
            if off_code not in OFFICE_CODES:
                raise ValueError(f"unhandled office code {off_code!r} ({r[22]})")
            name = CANDIDATES.get(cand_no)
            if name is None:
                raise ValueError(f"unhandled candidate number {cand_no} "
                                 f"({r[11]}, {r[12]})")
            party = r[9] if r[9] in ("DEM", "REP") else r[9]
            district = "" if off_code not in DISTRICTED else str(int(float(r[5])))
            if off_code in ("USP", "USS", "ATT", "AUD", "TRE", "DSC", "RSC") and \
                    int(float(r[5])) not in (0,):
                raise ValueError(f"unexpected district on {off_code} row: {r}")
            if off_code in DISTRICTED and district == "":
                raise ValueError(f"missing district on {off_code} row: {r}")
            prec_code = int(float(r[3]))
            prec = precinct_name(prec_code, r[22], r[23], r[24])
            seen_prec.setdefault(prec_code, prec)
            key = (prec, OFFICE_CODES[off_code], district, party)
            entry = contests.setdefault(key, OrderedDict())
            entry[name] = entry.get(name, 0) + int(float(r[15]))
    print(f"prcres: {n_rows} rows -> {len(contests)} (precinct, contest) groups, "
          f"{len(seen_prec)} precincts")
    return contests, seen_prec


# ------------------------------------------------------------ write-in PDF
def _page_groups(page):
    """Character groups of a rotated page in reading order.

    The report pages are rotated 90 degrees; view row = H - center_y,
    view column = center_x.  Chars are grouped by view-row position
    (tolerance 3pt) and ordered ascending, which for these pages is the
    group order the assignment logic expects.
    """
    h = page.height
    chars = sorted((h - (c["top"] + c["bottom"]) / 2,
                    (c["x0"] + c["x1"]) / 2, c["text"]) for c in page.chars)
    groups = []
    for vx, vy, t in chars:
        if groups and vx - groups[-1][-1][0] <= 3:
            groups[-1].append((vx, vy, t))
        else:
            groups.append([(vx, vy, t)])
    return groups


def _column_names(page):
    """Assemble the 55 report column headers from the header band."""
    h = page.height
    lets = sorted(((c["x0"] + c["x1"]) / 2, h - (c["top"] + c["bottom"]) / 2,
                   c["text"]) for c in page.chars if not re.fullmatch(r"\d", c["text"]))
    band = [(vy, vx, t) for vy, vx, t in lets if 600 <= vx <= 760 and vy > 330]
    band.sort()
    clusters = []
    for vy, vx, t in band:
        if clusters and vy - clusters[-1][-1][0] < 6:
            clusters[-1].append((vy, vx, t))
        else:
            clusters.append([(vy, vx, t)])
    names = []
    for c in clusters:
        txt = "".join(t for _, _, t in sorted(c, key=lambda z: (z[1], z[0])))
        names.append((" ".join(txt.replace("\xa0", " ").split()),
                      (c[0][0] + c[-1][0]) / 2))
    return names


def _row_cells(group, col_by_pos, tol=7.0, gap=9.0):
    """Cluster the group's digit characters into report cells."""
    centers = sorted(col_by_pos)
    dig = sorted(((vy, t) for _, vy, t in group if re.fullmatch(r"\d+", t)),
                 key=lambda z: z[0])
    out = {}
    stray = 0
    i = 0
    while i < len(dig):
        j = i
        while j + 1 < len(dig) and dig[j + 1][0] - dig[j][0] < gap:
            j += 1
        value = int("".join(t for _, t in dig[i:j + 1]))
        mid = (dig[i][0] + dig[j][0]) / 2
        best = min(centers, key=lambda c: abs(c - mid))
        if abs(best - mid) <= tol:
            out[col_by_pos[best]] = out.get(col_by_pos[best], 0) + value
        else:
            stray += value
        i = j + 1
    return out, stray


def parse_writein_pdf(path: str, expected_columns):
    """Per-precinct write-in votes from the county write-in review report.

    Returns {(office_code, district, party): {precinct: total}} where total
    is the county's certified write-in count for that (precinct, contest) =
    the contest's named write-in candidates + Scattered + Invalid.
    """
    with pdfplumber.open(path) as pdf:
        # verify this PDF's column headers against the measured layout
        page1_names = [(name, round(pos, 1)) for name, pos
                       in _column_names(pdf.pages[1])]
        got = {(n, p) for n, p in page1_names if n != "Grand Total"}
        expected = {(n, round(p, 1)) for n, p in expected_columns
                    if n != "__TOTAL__"}
        if got != expected:
            raise ValueError(f"report columns unexpected:\n got {got}\n"
                             f" expected {expected}")
        col_by_pos = {round(pos, 1): name for name, pos in expected_columns}

        # pass 1: collect contest titles and label rows
        titles = []                   # (page_index, first_char_pos, key)
        labels = []                   # (page_index, pos, label_text, cells)
        for pi, page in enumerate(pdf.pages[1:], start=1):
            for g in _page_groups(page):
                text = " ".join("".join(t for _, vy, t in
                                        sorted((x for x in g if x[1] < 300),
                                               key=lambda z: z[1]))
                                .replace("\xa0", " ").split())
                # titles can be split so '(Vote for N)' falls into another
                # group; a contest title is any row whose left-column text
                # contains one of the known contest bases
                is_title = any(base in text for base in WI_TITLES)
                if is_title:
                    titles.append((pi, g[0][0], _contest_key(text)))
                    continue
                cells, stray = _row_cells(g, col_by_pos)
                gt = cells.pop("__TOTAL__", None)
                if len(cells) >= 50:            # a write-in label row
                    if stray or gt is None or sum(cells.values()) != gt:
                        raise ValueError(f"{pi} {text!r}: cells {len(cells)} "
                                         f"sum {sum(cells.values())} vs Grand "
                                         f"Total {gt}, stray {stray}")
                    label = re.sub(r"^\s*\[Write[^]]*\]\s*", "", text)
                    labels.append((pi, g[0][0], label, cells))
        writeins = {}
        for key in {t[2] for t in titles}:
            writeins[key] = {}
        doc_first = min(titles, key=lambda t: (t[0], t[1]))[2]
        for pi, lpos, label, cells in labels:
            own = [(tp, key) for (p2, tp, key) in titles
                   if p2 == pi and tp > lpos]
            if own:
                _, key = min(own)
            else:
                # trailing rows on the last page belong to the report's
                # first title (ATTORNEY GENERAL (REP))
                key = doc_first
            per_prec = writeins[key]
            for col, v in cells.items():
                if col == "__TOTAL__":
                    continue
                if v:
                    per_prec[col] = per_prec.get(col, 0) + v
        return writeins


def _contest_key(title_text: str):
    """'PRESIDENT OF THE UNITED STATES (REP) (Vote for 1)' -> (USP,'',REP).

    The assembled title text can carry stray characters from neighboring
    column headers (e.g. 'ODELEGATE ...', '... (Vote for 3)I'), so the
    contest base is matched by containment and the party by its
    parenthesised marker instead of anchoring the whole string.
    """
    m_party = re.search(r"\((DEM|REP)(?:\)|OCRATIC|UBLICAN)?", title_text)
    if not m_party:
        raise ValueError(f"unparseable contest title {title_text!r}")
    party = m_party.group(1)
    base = " ".join(title_text.replace("\xa0", " ").split())
    for candidate_base, key in WI_TITLES.items():
        if candidate_base in base:
            return (key[0], key[1], party)
    raise ValueError(f"unknown contest title {base!r}")


# ---------------------------------------------------------------- output
def build_rows(prcres_path, wi_pdf_path, summary_path=None):
    contests, seen_prec = read_prcres(prcres_path)
    wi = parse_writein_pdf(wi_pdf_path, EXPECTED_WI_COLUMNS)
    wi = {k: {p: v for p, v in d.items() if v} for k, d in wi.items()}

    # cross-check: the report's per-contest totals must equal the workbook's
    # Write-in rows, and the DOS named sums must equal the workbook candidates
    wb = None
    if summary_path:
        wb = _read_summary(summary_path)
        bad = 0
        for key, per_prec in wi.items():
            got = sum(per_prec.values())
            want = wb["writein"].get(key)
            if want is None or got != want:
                bad += 1
                print(f"  WRITE-IN MISMATCH {key}: report {got} vs workbook "
                      f"{want}")
        print(f"write-ins: {len(wi)} contests, per-contest sums vs summary "
              f"workbook: {len(wi) - bad} exact, {bad} mismatch")

    merged: "OrderedDict[tuple, OrderedDict]" = OrderedDict()
    for (prec, office, district, party), cands in contests.items():
        merged[(prec, office, district, party)] = OrderedDict(
            (name, v) for name, v in cands.items())
    for (off, dist, party), per_prec in sorted(wi.items(),
                                               key=lambda kv: (kv[0][0], kv[0][2])):
        for prec, v in per_prec.items():
            key = (prec, OFFICE_CODES[off], dist, party)
            if key not in merged:
                raise ValueError(f"write-in for unknown contest {key}")
            merged[key]["Write-In Totals"] = merged[key].get("Write-In Totals", 0) + v

    rows = []
    for (prec, office, district, party), cands in merged.items():
        for name, votes in cands.items():
            rows.append({"county": COUNTY, "precinct": prec, "office": office,
                         "district": district, "party": party,
                         "candidate": name, "votes": votes})
    n_wi = sum(1 for r in rows if r["candidate"] == "Write-In Totals")
    print(f"rows: {len(rows)} total ({len(rows) - n_wi} named candidates + "
          f"{n_wi} Write-In Totals rows)")
    return rows, contests, wi, wb


def write_csv(path: str, rows: list):
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDNAMES)
        w.writeheader()
        for r in rows:
            w.writerow(r)
    print(f"wrote {len(rows)} rows -> {path}")


def _read_summary(xlsx_path: str):
    """Contest/candidate totals from the county summary workbook."""
    wb = openpyxl.load_workbook(xlsx_path, data_only=True)
    ws = wb["ElectionData"]
    cands: dict = {}
    writeins: dict = {}
    totals: dict = {}
    for row in ws.iter_rows(min_row=2, values_only=True):
        title, _, total, _, _, party, cand, votes = row
        m = re.match(r"^(.*?)\s*\((DEM|REP)\)\s*$", str(title).strip())
        cand = " ".join(str(cand).split())
        if cand.lower().startswith("write"):
            writeins[_key_from_title(m.group(1), m.group(2))] = int(votes)
        else:
            key = _key_from_title(m.group(1), m.group(2))
            cands.setdefault(key, {})[cand] = int(votes)
            totals[key] = int(total)
    return {"candidates": cands, "writein": writeins, "totals": totals}


# workbook title fragment -> (office code, district)
_WB_TITLES = {
    "PRESIDENT OF THE UNITED STATES": ("USP", ""),
    "US SENATOR": ("USS", ""),
    "ATTORNEY GENERAL": ("ATT", ""),
    "AUDITOR GENERAL": ("AUD", ""),
    "STATE TREASURER": ("TRE", ""),
    "REPRESENTATIVE IN CONGRESS 7TH DISTRICT": ("USC", "7"),
    "REPRESENTATIVE IN CONGRESS 8TH DISTRICT": ("USC", "8"),
    "REPRESENTATIVE IN THE GENERAL ASSEMBLY 115TH DISTRICT": ("STH", "115"),
    "REPRESENTATIVE IN THE GENERAL ASSEMBLY 176TH DISTRICT": ("STH", "176"),
    "REPRESENTATIVE IN THE GENERAL ASSEMBLY 189TH DISTRICT": ("STH", "189"),
    "DELEGATE TO THE DEMOCRATIC NATIONAL CONVENTION 7TH CONGRESSIONAL DISTRICT":
        ("DED", "7"),
    "DELEGATE TO THE DEMOCRATIC NATIONAL CONVENTION 8TH CONGRESSIONAL DISTRICT":
        ("DED", "8"),
    "DELEGATE TO THE REPUBLICAN NATIONAL CONVENTION 7TH CONGRESSIONAL DISTRICT":
        ("DER", "7"),
    "DELEGATE TO THE REPUBLICAN NATIONAL CONVENTION 8TH CONGRESSIONAL DISTRICT":
        ("DER", "8"),
    "ALTERNATE DELEGATE TO THE REPUBLICAN NATIONAL CONVENTION "
        "7TH CONGRESSIONAL DISTRICT": ("ADR", "7"),
    "ALTERNATE DELEGATE TO THE REPUBLICAN NATIONAL CONVENTION "
        "8TH CONGRESSIONAL DISTRICT": ("ADR", "8"),
}


def _key_from_title(base: str, party: str):
    if base not in _WB_TITLES:
        raise ValueError(f"unknown workbook contest title {base!r}")
    off, dist = _WB_TITLES[base]
    return (off, dist, party)


# workbook candidate name -> output candidate name
WB_TO_NAME = {
    "BIDEN JR, JOSEPH R": "Joseph R. Biden, Jr.",
    "PHILLIPS, DEAN": "Dean Phillips",
    "HALEY, NIKKI R": "Nikki Haley",
    "TRUMP, DONALD J": "Donald J. Trump",
    "CASEY JR, ROBERT P": "Robert P. Casey, Jr.",
    "MCCORMICK, DAVE": "Dave McCormick",
    "STOLLSTEIMER, JACK": "Jack Stollsteimer",
    "DEPASQUALE, EUGENE": "Eugene Depasquale",
    "KHAN, JOE": "Joe Khan",
    "BRADFORD-GREY, KEIR": "Keir Bradford-Grey",
    "SOLOMON, JARED": "Jared Solomon",
    "SUNDAY, DAVE": "Dave Sunday",
    "WILLIAMS, CRAIG": "Craig Williams",
    "KENYATTA, MALCOLM": "Malcolm Kenyatta",
    "PINSLEY, MARK": "Mark Pinsley",
    "DEFOOR, TIM": "Timothy DeFoor",
    "BIZZARRO, RYAN": "Ryan Bizzarro",
    "MCCLELLAND, ERIN": "Erin McClelland",
    "GARRITY, STACY": "Stacy Garrity",
    "WILD, SUSAN": "Susan Wild",
    "MACKENZIE, RYAN E": "Ryan Mackenzie",
    "DELLICKER, KEVIN": "Kevin Dellicker",
    "MONTERO, MARIA": "Maria Montero",
    "CARTWRIGHT, MATT": "Matt Cartwright",
    "BRESNAHAN JR, ROB": "Rob Bresnahan Jr.",
    "MADDEN, MAUREEN E": "Maureen Madden",
    "LONG, MATT": "Matt Long",
    "CHRISTMAN, HOPE": "Hope Christman",
    "RADER, JACK": "Jack Rader",
    "PROBST, TARAH": "Tarah Probst",
    "VANWHY, LISA": "Lisa VanWhy",
    "LEVINSON, AIDAN": "Aidan Levinson",
    "WAKABAYASHI, ANNE": "Anne Wakabayashi",
    "MCFARLAND, LORI": "Lori McFarland",
    "ALTIERI, LESLIE M": "Leslie Altieri",
    "BELL, CARMEN A": "Carmen Bell",
    "VANDERBURG, BARON": "Baron Vanderburg",
    "GURIDY, JULIAN": "Julian Guridy",
    "HELLMAN, JOHN": "John Hellman",
    "BONIN, MARGAUX O'NEILL": "Margaux Bonin",
    "KRAMER, GRACE MCGREGOR": "Grace Kramer",
    "MCGLOIN, MATT": "Matt McGloin",
    "CUTILLO, ANDREW": "Andrew Cutillo",
    "CUBBAGE, PATRICK": "Patrick Cubbage",
    "CARROLL, TOM": "Tom Carroll",
    "PASQUARIELLO, ROCHELLE": "Rochelle Pasquariello",
    "RIVERA, JACQUELINE": "Jacqueline Rivera",
    "CUBBAGE, EVE": "Eve Cubbage",
    "SMITH JR, ROBERT E": "Robert E. Smith Jr.",
    "LAROCCA, ANGELO": "Angelo LaRocca",
    "HUTCHISON, JAMES PAUL": "James Paul Hutchison",
    "VILLANO, T LYNETTE": "T. Lynette Villano",
    "GIANNETTA, MICHAEL A": "Michael Giannetta",
    "CONTRERAS, MATTHEW": "Matthew Contreras",
    "QUAGLIA, PETER V": "Peter Quaglia",
    "STRISH, DOLORES": "Dolores Strish",
    "WHITEHEAD, THOMAS": "Thomas Whitehead",
    "MEDEIROS, ROBIN L": "Robin Medeiros",
    "RAMIREZ, HECTOR": "Hector Ramirez",
    "DEFABO, PAUL": "Paul DeFabo",
}


def verify_summary(rows, contests, wi, wb):
    """Cross-check our per-contest sums against the summary workbook."""
    print("\n--- verification vs summary workbook ---")
    ours: collections.defaultdict = collections.defaultdict(int)
    for r in rows:
        ours[(r["office"], r["district"], r["party"], r["candidate"])] += r["votes"]
    exact = miss = 0
    for key, cands in sorted(wb["candidates"].items()):
        off, dist, party = key
        for raw, want in cands.items():
            name = WB_TO_NAME.get(raw, raw)
            got = ours.get((office_name(off), dist, party, name), 0)
            if got == want:
                exact += 1
            else:
                miss += 1
                print(f"  CANDIDATE MISMATCH {key} {raw} -> {name}: "
                      f"ours {got} vs workbook {want}")
    print(f"named candidates: {exact} exact, {miss} mismatch(es)")
    wi_exact = 0
    for key in sorted(wb["totals"]):
        off, dist, party = key
        got = sum(v for (o, d, p, c), v in ours.items()
                  if o == office_name(off) and d == dist and p == party)
        want = wb["totals"][key]
        wi_exact += 0 if got == want else 1
        if got != want:
            print(f"  CONTEST TOTAL MISMATCH {off} {dist} {party}: "
                  f"ours {got} vs workbook total {want}")
    print(f"contest totals (incl. write-ins): {len(wb['totals']) - wi_exact} "
          f"exact of {len(wb['totals'])}")
    return miss + wi_exact


def office_name(off_code: str) -> str:
    return OFFICE_CODES[off_code]


def main(argv):
    if len(argv) < 4:
        print(__doc__)
        return 2
    prcres, wi_pdf, outfile = argv[1:4]
    summary = argv[4] if len(argv) > 4 else None
    rows, contests, wi, wb = build_rows(prcres, wi_pdf, summary)
    write_csv(outfile, rows)
    if wb is not None:
        n_bad = verify_summary(rows, contests, wi, wb)
        if n_bad:
            print(f"WARNING: {n_bad} verification mismatch(es)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
