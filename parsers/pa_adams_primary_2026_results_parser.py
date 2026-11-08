#!/usr/bin/env python3
"""
Parser for Adams County, PA 2026 General Primary (2026-05-19) results,
producing BOTH output files:

  * 2026/counties/20260519__pa__primary__adams__precinct.csv
  * 2026/counties/20260519__pa__primary__adams__county.csv

Source composition (documented — this county's own per-precinct source is
deficient for the regular statewide/district races, so a DOS fallback is
used):

  1. ``Adams County Precinct Summary Results 2026 Primary.pdf``
     (Electionware "Summary Results Report", JasperReports, generated
     05/22/2026, UNOFFICIAL, 53 pages = 53 precincts) is the county's only
     per-precinct source, but it covers ONLY the 53 poll-scoped
     ``DEM Democratic Committee Member <Precinct>`` contests (one per
     precinct, each Vote For 2/3) plus each precinct's DEMOCRATIC
     statistics block (Registered Voters - DEMOCRATIC, Ballots Cast -
     DEMOCRATIC with ED/mail/provisional breakdown, and a turnout
     percentage; the report prints NO Total/Republican statistics block
     and no per-precinct Ballots Cast - Blank).  Those committee contests
     and the DEM RV/BC rows are parsed from this PDF.  Write-in naming
     follows the 2026 primary convention: the source prints a single
     "Write-In Totals" row per contest, so the candidate is ``Write-In
     Totals`` (the county's 2022 file used ``Write Ins``).
  2. ``Adams County Summary Results 2026 Primary.pdf`` (same vendor
     family, 23 pages, countywide candidate tables per contest) is the
     source for the countywide CSV: Governor, Lieutenant Governor,
     U.S. House 13, State House 91/193 and Member of
     Democratic/Republican State Committee, DEM + REP, with
     election_day / mail / provisional breakdowns and "Write-In Totals"
     rows as printed.  Overvote/Undervote/Total Votes Cast/Contest Totals
     detail lines are skipped (no 2026 *__county.csv carries them).  The
     poll-scoped Democratic Committee Member contests are NOT carried
     into the countywide CSV (all 40 existing files in
     2026/counties/*__county.csv carry contest-level office tables only).
  3. ``Adams County Precinct Summary Results`` has NO per-precinct rows
     for any regular race, so the per-precinct Governor / Lieutenant
     Governor / U.S. House / State House figures are filled from the PA
     DOS certified precinct-returns bulk file (DOS FALLBACK — used
     because the county's own per-precinct source covers committee
     contests only; documented per the snyder 2022 write-up style):
       ``PA DOS 2026 General Primary Certified Precinct Returns
       Statewide.txt`` (county code 1; 594 rows = 53 precincts x the
       ballot's named-candidate rows: GOV DEM/REP, LTG DEM/REP,
       USC DEM/REP 13, STH REP 193, STH DEM/REP 91).  A verbatim slice is
       kept at ``work2026p/dos/adams__dos_precinctreturns.txt``
       (md5 3c4088c4998fd8380720e693f451f7e8, byte-identical to the grep
       of the county-code-1 rows of the statewide bulk file).
     DOS bulk-file limitations (2022/2024/2026 precedent), none
     recoverable from the county sources: no write-in rows, no RV/BC
     rows, no vote breakdowns (the DOS-sourced rows leave
     election_day/mail/provisional empty), no State Committee.
     Candidate names are mapped to the county summary's printed names
     (title case), e.g. DOS "SHAPIRO JOSHUA D" -> "Josh Shapiro",
     "FARNHAM ELIZABETH RHOADS" -> "Beth Farnham",
     "NUNEZ-GUZMAN JOSE ANTONIO" -> "Tony Nunez-Guzman" (the anchor's
     "TONY NUNEZ-GUZMAN").
  4. Write-in detail reports (both generated 05/28/2026, a later vintage
     than the 05/22 summary PDFs — see caveats):
       - ``Adams County 193rd District Representative Write-in Votes
         2026 Primary.pdf``: countywide HD-193 DEM write-in resolution —
         Todd Crawley 551.  Added to the COUNTY csv as a named candidate
         row next to the summary's "Write-In Totals" 651 (York county's
         2026 precedent: State House 92 REP "Write-in (total)" alongside
         named write-in rows, where named rows are contained in the
         aggregate).  The aggregate is the printed summary row; the named
         row is additional, not a replacement — rows of such contests must
         not be summed as mutually exclusive.
       - ``Adams County Democratic Committee Write-in Votes 2026
         Primary.pdf``: per-precinct DEM committee-member write-in
         resolutions — Carroll Valley #1 Dan Mangan 23, Menallen Joyce
         Zeppa-Sadler 11.  Added to the PRECINCT csv for those precincts,
         alongside the same precinct's printed "Write-In Totals"
         aggregates of 32 and 29 that contain them (same convention).
     Only totals are printed in both reports (no breakdown) -> those rows
     leave the breakdown columns empty.

Contests with no source at the level required (omitted, not invented):
  * State Committee contests (DEM Roger Lund/Helen A Cook, REP
    Minton/Martin/Mathews) have no per-precinct source anywhere (the
    county precinct report has none and the DOS bulk file carries
    none — Clarion 2026 / Adams 2022 precedent); they appear only in the
    countywide CSV.
  * State House 193 DEM write-in votes have no per-precinct source; they
    appear only (as Write-In Totals + the Todd Crawley detail row) in
    the countywide CSV, so the DEM 193 contest has no precinct rows.
  * Republican and party-total RV/BC per-precinct rows are not printed by
    the county precinct report (DEM statistics block only).
  * No State Senate / U.S. Senate / statewide row office is on Adams'
    2026 primary ballot structure (gubernatorial-year primary: 6 regular
    contests + 53 poll-scoped committee contests).

Usage:
    uv run python parsers/pa_adams_primary_2026_results_parser.py

  One pass over all sources (default paths below), writes both CSVs and
  runs verification inline (use --no-verify to skip).  Every source path
  may be overridden; all --*-src options also accept the original PDF
  (pdftotext -layout is applied via subprocess when the suffix is .pdf).
"""

from __future__ import annotations

import argparse
import collections
import csv
import re
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = REPO_ROOT.parent / "openelections-sources-pa" / "2026" / "primary"
OUT_DIR = REPO_ROOT / "2026" / "counties"

COUNTY = "Adams"

PRECINCT_FIELDNAMES = [
    "county", "precinct", "office", "district", "party", "candidate",
    "votes", "election_day", "mail", "provisional",
]
COUNTY_FIELDNAMES = [
    "county", "office", "district", "party", "candidate",
    "votes", "election_day", "mail", "provisional",
]

DEFAULTS = {
    "precinct_src": SRC_DIR / "Adams County Precinct Summary Results 2026 Primary.pdf",
    "county_src": SRC_DIR / "Adams County Summary Results 2026 Primary.pdf",
    "hd193_wi_src": SRC_DIR / "Adams County 193rd District Representative Write-in Votes 2026 Primary.pdf",
    "comm_wi_src": SRC_DIR / "Adams County Democratic Committee Write-in Votes 2026 Primary.pdf",
    "dos_src": REPO_ROOT / "work2026p" / "dos" / "adams__dos_precinctreturns.txt",
    "precinct_out": OUT_DIR / "20260519__pa__primary__adams__precinct.csv",
    "county_out": OUT_DIR / "20260519__pa__primary__adams__county.csv",
}

US_HOUSE_DISTRICT = "13"  # Adams == 13th CD (Rep. Joyce), per the county anchor

JUNK_SUBSTR = (
    "Summary Results Report",
    "UNOFFICIAL RESULTS",
    "OFFICIAL RESULTS",
    "Report generated with Electionware",
    "Precinct Summary - ",
    "Election Summary - ",
)
FOOTER_RE = re.compile(r"\bPage \d+ of \d+\b")
PAGE_HEADER_RE = re.compile(
    r"(?i)^General Primary$|^May 19, 2026\b|ADAMS COUNTY, PENNSYLVANIA")
COLUMN_HEADER_RE = re.compile(
    r"(?i)^(TOTAL\b|.*\b(?:VOTE %|Mail Votes|Provisional Votes|Election Day)\b)")
STAT_MARKER_RE = re.compile(r"^STATISTICS\b", re.IGNORECASE)
VOTE_FOR_RE = re.compile(r"Vote For (\d+)", re.IGNORECASE)
PARTY_PREFIX_RE = re.compile(r"^(DEM|REP)\s+(\S.*)$")
NUM_TOKEN_RE = re.compile(r"^\d[\d,]*(?:\.\d+)?%?$")

SKIP_ROW_RE = re.compile(
    r"^(Total Votes Cast|Contest Totals|Overvotes|Undervotes|Not Assigned)$",
    re.IGNORECASE)
WRITEIN_ROW_RE = re.compile(r"^(Write-in Totals|Write In|Write-In:)", re.IGNORECASE)

GOVERNOR_RE = re.compile(r"^Governor$", re.IGNORECASE)
LT_GOVERNOR_RE = re.compile(r"^Lieutenant Governor$", re.IGNORECASE)
CONGRESS_RE = re.compile(r"^Representative in Congress$", re.IGNORECASE)
ASSEMBLY_RE = re.compile(
    r"^Representative in the General Assembly\s+(\d+)(?:st|nd|rd|th)?\s+District$",
    re.IGNORECASE,
)


def normalize_office(title: str):
    """(office title minus DEM/REP prefix) -> (office, district)."""
    title = re.sub(r"\s+", " ", title).strip()
    if GOVERNOR_RE.match(title):
        return ("Governor", "")
    if LT_GOVERNOR_RE.match(title):
        return ("Lieutenant Governor", "")
    if CONGRESS_RE.match(title):
        return ("U.S. House", US_HOUSE_DISTRICT)
    m = ASSEMBLY_RE.match(title)
    if m:
        return ("State House", m.group(1))
    # "Member of Democratic/Republican State Committee" and the poll-scoped
    # local contests ("Democratic Committee Member Abbottstown") keep as
    # printed (2026 repo convention: State Committee district left blank,
    # as in the Berks/Elk countywide CSVs).
    return (title, "")


def is_junk(line: str) -> bool:
    return any(sub in line for sub in JUNK_SUBSTR) or FOOTER_RE.search(line)


def is_noise(line: str) -> bool:
    """Headers/footers/column labels that are never data."""
    return is_junk(line) or PAGE_HEADER_RE.match(line) or COLUMN_HEADER_RE.match(line)


def read_txt(path: Path) -> str:
    if path.suffix.lower() == ".pdf":
        with tempfile.NamedTemporaryFile(suffix=".txt", delete=False) as tf:
            tmp = Path(tf.name)
        subprocess.run(["pdftotext", "-layout", str(path), str(tmp)], check=True)
        return tmp.read_text(encoding="utf-8", errors="replace")
    return path.read_text(encoding="utf-8", errors="replace")


def title_case(name: str) -> str:
    return re.sub(r"\s+", " ", name).strip().title()


def trailing_numbers(line: str):
    tokens = line.split()
    out = []
    for tok in reversed(tokens):
        if NUM_TOKEN_RE.match(tok):
            out.append(tok)
        else:
            break
    out.reverse()
    return out


def clean_num(tok: str) -> str:
    return tok.replace(",", "")


def is_numbers_only(line: str) -> bool:
    tokens = line.split()
    return bool(tokens) and all(NUM_TOKEN_RE.match(t) for t in tokens)


def is_writein_row(head: str) -> bool:
    return bool(WRITEIN_ROW_RE.match(head) or re.search(r"(?i)write-in totals", head))


# ---------------------------------------------------------------- DOS slice

DOS_COUNTY_CODE = "1"
DOS_YEAR = "2026"
DOS_ELECTION_TYPE = "P"
# DOS office code -> (repo office, fixed district or None to take the row's
# own district field 5)
DOS_OFFICES = {
    "GOV": ("Governor", ""),
    "LTG": ("Lieutenant Governor", ""),
    "USC": ("U.S. House", US_HOUSE_DISTRICT),
    "STH": ("State House", None),
}
DOS_OFFICE_ORDER = ["Governor", "Lieutenant Governor", "U.S. House", "State House"]

# DOS (last, first, middle) -> candidate name as the county summary prints
# it (title case).  Everything on Adams' 2026 primary ballot is in here;
# the parser fails loudly if the DOS slice grows a 15th candidate.
DOS_NAMES = {
    ("SHAPIRO", "JOSHUA", "D"): "Josh Shapiro",
    ("GARRITY", "STACY", "LORRAINE"): "Stacy Garrity",
    ("DAVIS", "AUSTIN", ""): "Austin Davis",
    ("VENTRE", "JOHN", ""): "John Ventre",
    ("RICHEY", "JASON", "L"): "Jason Richey",
    ("FARNHAM", "ELIZABETH", "RHOADS"): "Beth Farnham",
    ("JOYCE", "JOHN", ""): "John Joyce",
    ("PRATT", "KATHLEEN", "ADAMS"): "Kathleen Pratt",
    ("NUNEZ-GUZMAN", "JOSE", "ANTONIO"): "Tony Nunez-Guzman",
    ("SUNDBERG", "DARIAN", ""): "Darian Sundberg",
    ("MOUL", "DANIEL", "P"): "Dan Moul",
    ("LOVELL", "NICKOLAS", "JOSEPH DAVID"): "Nick Lovell",
    ("KRUG", "LINDSAY", ""): "Lindsay Krug",
    ("WALLEN", "CATHERINE", "I"): "Catherine Wallen",
}

# DOS precinct code -> precinct name; the county's own convention (2022
# primary/general precedent: "Carroll Valley 1", "Mt Joy 1", McSherrystown
# as one word, qualifier/split -> plain " N").
DOS_PRECINCT_NAMES = {
    "10": "Abbottstown",
    "20": "Arendtsville",
    "30": "Bendersville",
    "40": "Berwick",
    "50": "Biglerville",
    "60": "Bonneauville",
    "70": "Butler",
    "80": "Carroll Valley 1",
    "85": "Carroll Valley 2",
    "90": "Conewago 1",
    "100": "Conewago 2",
    "105": "Conewago 3",
    "110": "Cumberland 1",
    "120": "Cumberland 2",
    "130": "Cumberland 3",
    "140": "Cumberland 4",
    "150": "East Berlin",
    "160": "Fairfield",
    "170": "Franklin 1",
    "180": "Franklin 2",
    "190": "Freedom",
    "200": "Germany",
    "210": "Gettysburg 1",
    "230": "Gettysburg 2",
    "240": "Gettysburg 3",
    "260": "Hamilton",
    "270": "Hamiltonban",
    "280": "Highland",
    "290": "Huntington",
    "310": "Latimore",
    "320": "Liberty",
    "330": "Littlestown 1",
    "340": "Littlestown 2",
    "350": "McSherrystown 1",
    "360": "McSherrystown 2",
    "370": "Menallen",
    "380": "Mt Joy 1",
    "390": "Mt Joy 2",
    "400": "Mt Pleasant 1",
    "410": "Mt Pleasant 2",
    "420": "New Oxford",
    "425": "Oxford 1",
    "435": "Oxford 2",
    "440": "Reading 1",
    "450": "Reading 2",
    "455": "Reading 3",
    "460": "Straban 1",
    "470": "Straban 2",
    "473": "Straban 3",
    "476": "Straban 4",
    "480": "Tyrone",
    "500": "Union",
    "510": "York Springs",
}

# Row order for DOS-sourced contest rows per precinct, derived from the
# slice's own file order (office rank, party rank, ballot position).
DOS_ROW_ORDER = {}


def load_dos_rows(path):
    """Read the Adams (county code 1) primary slice of the PA DOS certified
    precinct-returns bulk file.  Fields used (0-indexed): 2=county code,
    3=precinct code, 5=contest district, 8=office code, 9=party,
    11/12/13=candidate name parts, 15=votes."""
    rows = []
    with Path(path).open(newline="", encoding="latin-1") as fh:
        for idx, r in enumerate(csv.reader(fh)):
            if len(r) < 26 or r[:2] != [DOS_YEAR, DOS_ELECTION_TYPE]:
                continue
            if r[2] != DOS_COUNTY_CODE:
                continue
            if r[8] not in DOS_OFFICES:
                raise SystemExit(f"unexpected DOS office code {r[8]!r} in {path}")
            name_key = (r[11], r[12], r[13].strip())
            if name_key not in DOS_NAMES:
                raise SystemExit(f"unmapped DOS candidate {name_key!r} in {path}")
            rows.append({
                "idx": idx,
                "code": r[3],
                "office_code": r[8],
                "party": r[9],
                "candidate": DOS_NAMES[name_key],
                "district": r[5].strip(),
                "votes": int(clean_num(r[15])),
            })
    return rows


def dos_output_row(r):
    office, fixed = DOS_OFFICES[r["office_code"]]
    district = fixed if fixed is not None else r["district"]
    return [
        COUNTY, DOS_PRECINCT_NAMES[r["code"]], office, district,
        r["party"], r["candidate"], str(r["votes"]), "", "", "",
    ]


# ------------------------------------------------- write-in detail reports

# "REPRESENTATIVE IN THE GENERAL   TODD CRAWLEY   551" — in the layout-
# extracted text the (wrapped) office text and the name share a line with
# the trailing vote count, and wrapped office words continue on the next
# line.  Match an all-caps name (letters, spaces, apostrophes, hyphens)
# followed by the count, then strip the office words off the name.
# Office words seen in these two reports:
WI_OFFICE_WORDS = (
    "REPRESENTATIVE IN THE GENERAL ASSEMBLY",
    "REPRESENTATIVE IN THE GENERAL",
    "ASSEMBLY",
    "DEMOCRATIC COMMITTEE MEMBER",
    "REPUBLICAN COMMITTEE MEMBER",
    "COMMITTEE MEMBER",
)
WI_ROW_RE = re.compile(r"([A-Z][A-Z \-']*[A-Z])\s+(\d[\d,]*)\s*$")


def _wi_name_votes(line):
    m = WI_ROW_RE.search(line.rstrip())
    if not m:
        return None
    name = m.group(1).strip()
    for word in WI_OFFICE_WORDS:
        name = re.sub(re.escape(word), " ", name, flags=re.IGNORECASE)
    name = re.sub(r"\s+", " ", name).strip()
    if not name:
        return None
    return (name, int(clean_num(m.group(2))))


def parse_hd193_writeins(path):
    """Countywide HD-193 DEM write-in resolutions -> [(name, votes)]."""
    out = []
    for line in read_txt(path).split("\n"):
        nv = _wi_name_votes(line)
        if nv and nv[0].upper() not in ("OFFICE OF", "CANDIDATE'S NAME"):
            out.append((title_case(nv[0]), nv[1]))
    if not out:
        raise SystemExit(f"HD-193 write-in report unparsed: {path}")
    return out


def parse_committee_writeins(path):
    """Per-precinct DEM committee write-in resolutions, keyed by the
    precinct name in the report header ("CARROLL VALLEY #1" ->
    "Carroll Valley 1")."""
    out = []
    loc = None
    for line in read_txt(path).split("\n"):
        s = line.strip()
        if not s or is_noise(s):
            continue
        if re.match(r"(?i)^(OFFICE OF|CANDIDATE'S NAME|VOTES|WRITE IN VOTES)$", s) or \
                re.match(r"(?i)^MAY 19, 2026 GENERAL PRIMARY ELECTION$", s) or \
                re.match(r"(?i)^(DEMOCRATIC|REPUBLICAN)$", s) or \
                re.match(r"(?i)^\d+(ST|ND|RD|TH) DISTRICT", s):
            continue
        nv = _wi_name_votes(s)
        if nv:
            if loc is None:
                raise SystemExit("committee write-in row without location: %r" % line)
            out.append((loc, title_case(nv[0]), nv[1]))
            loc = None
            continue
        if re.match(r"^[A-Z0-9 #]+$", s):
            # Location header, e.g. "CARROLL VALLEY #1", "MENALLEN" —
            # normalized with the county's "#N" -> " N" convention.
            loc = re.sub(r"\s*#\s*(\d+)\s*$", r" \1", s).title()
    return out


# --------------------------------------------------- county summary parser

# Office band order for the countywide csv (matches existing 2026
# counties' files: Governor, LG, U.S. House, State Senate, State House,
# State Committee).
COUNTY_OFFICE_ORDER = ["Governor", "Lieutenant Governor", "U.S. House",
                       "State Senate", "State House",
                       "Member of Democratic State Committee",
                       "Member of Republican State Committee"]


def parse_county_summary(lines):
    """Countywide summary -> (office_rows, committee_totals, stat_totals).

    office_rows: [office, district, party, candidate, votes, ed, mail, prov]
    for the regular ballot contests (poll-scoped committee contests are
    collected separately, only for cross-checking the precinct report).
    committee_totals: {contest_title: {candidate: [votes as strings]}}.
    stat_totals: {(office, PARTY): votes} of the countywide STATISTICS block
    (e.g. ('Ballots Cast', 'DEMOCRATIC') -> 6396).
    """
    lines = [ln.strip() for ln in lines]
    n = len(lines)
    # Office header indices: line whose next non-empty line is "Vote For N".
    office_header_idx = {}
    for i, ln in enumerate(lines):
        if not ln:
            continue
        for j in range(i + 1, n):
            if not lines[j]:
                continue
            if VOTE_FOR_RE.match(lines[j]):
                office_header_idx[i] = j
            break

    office_rows = []
    committee_totals = collections.OrderedDict()
    stat_totals = {}
    contest = None          # ("COMMITTEE" | office, district, party)
    committee_title = None  # printed title of the current committee contest
    buf = None

    for idx, raw in enumerate(lines):
        line = raw
        if not line:
            continue
        if idx in office_header_idx:
            buf = None
            pm = PARTY_PREFIX_RE.match(line)
            if not pm:
                raise SystemExit(f"contest header without party prefix: {line!r}")
            party, title = pm.group(1), pm.group(2)
            is_poll_scoped = bool(re.search(r"Committee Member", title)) and \
                "State Committee" not in title
            if is_poll_scoped:
                contest = ("COMMITTEE", "", party)
                committee_title = title
                committee_totals.setdefault(title, collections.OrderedDict())
            else:
                office, district = normalize_office(title)
                contest = (office, district, party)
                committee_title = None
            continue
        if VOTE_FOR_RE.match(line):
            buf = None
            continue
        if is_noise(line):
            continue
        if STAT_MARKER_RE.match(line):
            contest, committee_title, buf = None, None, None
            continue

        # STATISTICS block labels (countywide totals, cross-check only).
        m = re.match(r"^(Registered Voters|Ballots Cast)\s*-\s*(\w+)", line)
        if m:
            nums = [clean_num(t) for t in trailing_numbers(line)
                    if not t.endswith("%")]
            if not nums and buf:
                nums = buf
            if len(nums) == 1:
                office = "Ballots Cast - Blank" \
                    if m.group(2).upper() == "BLANK" else m.group(1).title()
                stat_totals[(office, m.group(2).upper())] = int(nums[0])
            elif len(nums) > 1:
                # e.g. "Ballots Cast - DEMOCRATIC      6,396  2,750  3,638  8"
                office = "Ballots Cast - Blank" \
                    if m.group(2).upper() == "BLANK" else m.group(1).title()
                stat_totals[(office, m.group(2).upper())] = int(nums[0])
            else:
                raise SystemExit(f"unparsed STATISTICS label: {line!r}")
            buf = None
            continue
        if re.match(r"^(Voter Turnout|Precincts|Election Day Precincts"
                    r"|Absentee/?\s*(?:Early\s+)?Precincts"
                    r")\b.*Reporting", line) or \
                re.match(r"^(Voter Turnout|Precincts\b)", line):
            buf = None
            continue

        if is_numbers_only(line):
            raw_tail = trailing_numbers(line)
            if not (len(raw_tail) == 1 and raw_tail[0].endswith("%")):
                buf = [clean_num(t) for t in raw_tail]
            continue

        # Candidate / aggregate rows: "<name> <total> [ed mail prov]".
        raw_tail = trailing_numbers(line)
        head = " ".join(line.split()[: -len(raw_tail)]) if raw_tail else line
        nums = [clean_num(t) for t in raw_tail if not t.endswith("%")]
        if not nums:
            continue
        buf = None
        if SKIP_ROW_RE.match(head):
            continue
        if contest is None:
            raise SystemExit(f"row with no contest: {line!r}")
        kind, district, party = contest
        if is_writein_row(head):
            head = "Write-In Totals"
        else:
            head = title_case(head)
        total = clean_num(nums[0])
        # `contest` is already normalized (normalize_office ran at header
        # time); re-normalizing `kind` here would discard the district
        # (e.g. "State House" again -> "") and merge HD-91/HD-193.
        if kind == "COMMITTEE":
            committee_totals[committee_title][head] = total
        else:
            office = kind
            ed = clean_num(nums[1]) if len(nums) > 1 else ""
            mail = clean_num(nums[2]) if len(nums) > 2 else ""
            prov = clean_num(nums[3]) if len(nums) > 3 else ""
            office_rows.append([office, district, party, head,
                                total, ed, mail, prov])

    return office_rows, committee_totals, stat_totals


# ------------------------------------------------ precinct summary parser

RV_PARTY = {"TOTAL": "", "DEMOCRATIC": "DEM", "REPUBLICAN": "REP",
            "NONPARTISAN": "NONPARTISAN"}
BC_PARTY = dict(RV_PARTY)


def parse_precinct_segments(lines):
    """Yield (precinct_name_raw, segment_lines) per STATISTICS marker.

    Segments are truncated at the page footer ("Precinct Summary - ...
    Page N of 53"): everything past it (form feed + next page's header
    block, ending with the NEXT precinct's name line, e.g. "Carroll
    Valley 2") belongs to the following page, and a trailing name line
    ending in a digit would otherwise parse as a junk candidate row.
    """
    markers = [i for i, ln in enumerate(lines) if STAT_MARKER_RE.match(ln.strip())]
    if not markers:
        raise RuntimeError("No STATISTICS markers found; wrong file format?")
    bounds = markers + [len(lines)]
    for k, start in enumerate(markers):
        seg = lines[start:bounds[k + 1]]
        footers = [i for i, ln in enumerate(seg) if FOOTER_RE.search(ln)]
        if footers:
            seg = seg[:footers[-1]]
        name = None
        # Scan up from the marker: the last line before the STATISTICS
        # block that is not header/junk is the precinct name.
        for i in range(start - 1, -1, -1):
            s = lines[i].strip()
            if not s or STAT_MARKER_RE.match(s):
                continue
            if is_noise(s):
                continue
            name = s
            break
        yield name, seg


def parse_precinct_segment(name, seg, warnings):
    seg = [ln.strip() for ln in seg]
    n = len(seg)

    # Office headers: lines whose next non-empty line is "Vote For N".
    office_header_idx = set()
    for i, ln in enumerate(seg):
        if not ln:
            continue
        for j in range(i + 1, n):
            if not seg[j]:
                continue
            if VOTE_FOR_RE.match(seg[j]):
                office_header_idx.add(i)
            break

    rows = []
    contest = None
    buf = None

    def emit(office, district, party, candidate, nums):
        if len(nums) == 4:
            total, ed, mail, prov = tuple(clean_num(x) for x in nums)
        else:
            total, ed, mail, prov = clean_num(nums[0]), "", "", ""
        rows.append([COUNTY, name, office, district, party, candidate,
                     total, ed, mail, prov])

    for idx, raw in enumerate(seg):
        line = raw
        if not line:
            continue
        if STAT_MARKER_RE.match(line):
            contest, buf = None, None
            continue
        if idx in office_header_idx:
            buf = None
            pm = PARTY_PREFIX_RE.match(line)
            if pm:
                party, title = pm.group(1), pm.group(2)
            else:
                warnings.append(f"{name}: contest header without party prefix: {line!r}")
                party, title = "", line
            office, district = normalize_office(title)
            contest = (office, district, party)
            continue
        if VOTE_FOR_RE.match(line):
            buf = None
            continue
        if is_noise(line):
            continue
        if line == name.strip():
            # Repeated page header (continuation page of this precinct).
            continue

        # Statistics labels.
        m = re.match(r"^Registered Voters\s*-\s*(\w+)", line)
        if m:
            nums = [clean_num(t) for t in trailing_numbers(line)
                    if not t.endswith("%")]
            if len(nums) == 1:
                emit("Registered Voters", "",
                     RV_PARTY.get(m.group(1).upper(), ""), "", nums)
            else:
                warnings.append(f"{name}: RV unparsed: {line!r}")
            buf = None
            continue
        m = re.match(r"^Ballots Cast\s*-\s*(\w+)", line)
        if m:
            nums = [clean_num(t) for t in trailing_numbers(line)
                    if not t.endswith("%")]
            if not nums and buf and len(buf) == 4:
                nums = buf
            if len(nums) == 4:
                office = "Ballots Cast - Blank" \
                    if m.group(1).upper() == "BLANK" else "Ballots Cast"
                emit(office, "", BC_PARTY.get(m.group(1).upper(), ""), "", nums)
            else:
                warnings.append(f"{name}: Ballots Cast unparsed: {line!r} buf={buf}")
            buf = None
            continue
        if re.match(r"^Voter Turnout", line):
            buf = None
            continue

        # Numbers-only lines: values for the statistics label that follows.
        if is_numbers_only(line):
            raw_tail = trailing_numbers(line)
            if not (len(raw_tail) == 1 and raw_tail[0].endswith("%")):
                buf = [clean_num(t) for t in raw_tail]
            continue

        # Candidate / aggregate rows.
        raw_tail = trailing_numbers(line)
        head = " ".join(line.split()[: -len(raw_tail)]) if raw_tail else line
        nums = [clean_num(t) for t in raw_tail if not t.endswith("%")]
        if not nums:
            continue
        buf = None
        if SKIP_ROW_RE.match(head):
            continue
        if contest is None:
            warnings.append(f"{name}: row with no contest: {line!r}")
            continue
        office, district, party = contest
        if is_writein_row(head):
            emit(office, district, party, "Write-In Totals", nums)
        else:
            emit(office, district, party, title_case(head), nums)
    return rows


# ------------------------------------------------------------- verification

# 2026/20260519__pa__primary__county.csv anchor values for every named
# candidate the DOS fallback covers: (office, district, party,
# last-name key) -> total.
ANCHOR_TOTALS = {
    ("Governor", "", "DEM", "shapiro"): 6223,
    ("Governor", "", "REP", "garrity"): 8936,
    ("Lieutenant Governor", "", "DEM", "davis"): 6103,
    ("Lieutenant Governor", "", "REP", "ventre"): 3058,
    ("Lieutenant Governor", "", "REP", "richey"): 6086,
    ("U.S. House", "13", "DEM", "farnham"): 6104,
    ("U.S. House", "13", "REP", "joyce"): 9388,
    ("State House", "91", "DEM", "pratt"): 2676,
    ("State House", "91", "DEM", "nunez-guzman"): 1196,
    ("State House", "91", "DEM", "sundberg"): 555,
    ("State House", "91", "REP", "moul"): 4180,
    ("State House", "91", "REP", "lovell"): 2159,
    ("State House", "91", "REP", "krug"): 439,
    ("State House", "193", "REP", "wallen"): 3233,
}


def last_name_key(candidate: str) -> str:
    return re.split(r"\s+", candidate.strip())[-1].lower()


def build_precinct_rows(precinct_src, dos_rows, comm_wi_rows, warnings):
    """County precinct PDF rows first (RV/BC + committee contests), then the
    DOS fallback rows for the regular races, then the per-precinct write-in
    detail rows.  Final order: RV, BC, DOS contests (slice file order),
    committee candidates (source order), Write-In Totals, write-in detail
    rows.

    Returns (rows, per_precinct_pdf_rows, per_precinct_counts)."""
    pdf_rows = []
    counts = collections.Counter()
    for raw_name, seg in parse_precinct_segments(
            read_txt(Path(precinct_src)).split("\n")):
        if raw_name is None:
            warnings.append("unnamed statistics segment skipped")
            continue
        if raw_name not in DOS_PRECINCT_NAMES.values():
            raise SystemExit("precinct name %r not in the DOS->county name "
                             "map" % raw_name)
        rows = parse_precinct_segment(raw_name, seg, warnings)
        pdf_rows.extend(rows)
        counts[raw_name] += 1

    code_rank = {name: i for i, name in enumerate(
        [DOS_PRECINCT_NAMES[c] for c in
         sorted(DOS_PRECINCT_NAMES, key=int)])}

    # Candidate band inside a committee contest:
    #   0 named candidate (source order via its index below), 1 Write-In
    #   Totals aggregate, 2 write-in-detail name rows (appended at the end,
    #   ordered by the detail report).
    pdf_order_index = {}
    for i, r in enumerate(pdf_rows):
        key = (r[1], r[5])
        if r[5] == "Write-In Totals":
            # the aggregate row of its contest
            pdf_order_index.setdefault((r[1], r[2], "__WI__"), len(pdf_order_index) + 10000)
        else:
            pdf_order_index[key] = i

    def sub_band(r):
        if r[5] == "Write-In Totals":
            return 1
        if r[0] == "WRITEIN_DETAIL":
            return 2
        return 0

    merged = []
    for row in pdf_rows:
        merged.append(list(row))
    merged.extend(dos_output_row(r) for r in dos_rows)
    merged.extend(comm_wi_rows)

    def key(r):
        _detail, name, office, district, party, cand = r[:6]
        band = ("stats_rv", 0) if office == "Registered Voters" else \
            ("stats_bc", 1) if office == "Ballots Cast" else \
            ("dos", DOS_OFFICE_ORDER.index(office)) if office in DOS_OFFICE_ORDER \
            else ("committee", 0)
        if band[0] == "dos":
            sub = DOS_ROW_ORDER[(name, party, cand)]
        elif band[0] == "committee":
            sub = (sub_band(r), pdf_order_index.get((name, cand), -1))
        else:
            sub = ""
        return (code_rank[name], band, sub, cand)

    rows = sorted(merged, key=key)
    for r in rows:
        r[0] = COUNTY  # drop the internal "WRITEIN_DETAIL" band marker
    return rows, counts


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--precinct-src", default=str(DEFAULTS["precinct_src"]))
    ap.add_argument("--county-src", default=str(DEFAULTS["county_src"]))
    ap.add_argument("--hd193-wi-src", default=str(DEFAULTS["hd193_wi_src"]))
    ap.add_argument("--comm-wi-src", default=str(DEFAULTS["comm_wi_src"]))
    ap.add_argument("--dos-src", default=str(DEFAULTS["dos_src"]))
    ap.add_argument("--precinct-out", default=str(DEFAULTS["precinct_out"]))
    ap.add_argument("--county-out", default=str(DEFAULTS["county_out"]))
    ap.add_argument("--no-verify", action="store_true")
    args = ap.parse_args(argv)

    # 1. Countywide CSV from the county summary.
    county_office_rows, committee_totals, stat_totals = parse_county_summary(
        read_txt(Path(args.county_src)).split("\n"))

    county_csv_rows = [
        [COUNTY, office, district, party, candidate, votes, ed, mail, prov]
        for office, district, party, candidate, votes, ed, mail, prov
        in county_office_rows
    ]
    # HD-193 DEM write-in resolutions (countywide detail report).
    for name, votes in parse_hd193_writeins(Path(args.hd193_wi_src)):
        county_csv_rows.append([COUNTY, "State House", "193", "DEM", name,
                                str(votes), "", "", ""])

    def county_key(r):
        office = r[1]
        band = COUNTY_OFFICE_ORDER.index(office) if office in COUNTY_OFFICE_ORDER else 99
        return (band, int(r[2]) if r[2] else 0, r[3],
                1 if r[4] == "Write-In Totals" else 0, r[4])

    county_csv_rows.sort(key=county_key)

    # 2. Per-precinct CSV.
    warnings = []
    dos_rows = load_dos_rows(Path(args.dos_src))
    seen_codes = {r["code"] for r in dos_rows}
    missing = set(DOS_PRECINCT_NAMES) - seen_codes
    if missing:
        raise SystemExit(f"DOS slice missing precinct codes: {sorted(missing)}")
    for r in dos_rows:
        DOS_ROW_ORDER[(DOS_PRECINCT_NAMES[r["code"]], r["party"], r["candidate"])] \
            = r["idx"]

    comm_wi_rows = []
    for loc, cand, votes in parse_committee_writeins(Path(args.comm_wi_src)):
        if loc not in DOS_PRECINCT_NAMES.values():
            raise SystemExit("committee write-in location %r not mapped" % loc)
        comm_wi_rows.append(["WRITEIN_DETAIL", loc,
                             f"Democratic Committee Member {loc}", "", "DEM",
                             cand, str(votes), "", "", ""])

    precinct_rows, counts = build_precinct_rows(
        args.precinct_src, dos_rows, comm_wi_rows, warnings)
    for name, c in sorted(counts.items()):
        if c != 1:
            print(f"NOTE: {name}: {c} segments")

    # 3. Verification (unless disabled).
    if not args.no_verify:
        failures = []
        # 3a. DOS-sourced named candidates: sums vs anchor and vs slice.
        sums = collections.Counter()
        for r in precinct_rows:
            if r[2] in DOS_OFFICE_ORDER:
                sums[(r[2], r[3], r[4].upper(), last_name_key(r[5]))] += int(r[6])
        for (office, district, party, last), total in ANCHOR_TOTALS.items():
            mine = sums.get((office, district, party, last), 0)
            if mine != total:
                failures.append(f"anchor mismatch {office} {district} "
                                f"[{party}] {last}: precinct sum {mine} != "
                                f"anchor {total}")
        dos_sums = collections.Counter()
        for r in dos_rows:
            office, fixed = DOS_OFFICES[r["office_code"]]
            district = fixed if fixed is not None else r["district"]
            dos_sums[(office, district, r["party"],
                      last_name_key(r["candidate"]))] += r["votes"]
        for key, total in dos_sums.items():
            if sums.get(key, 0) != total:
                failures.append("DOS slice mismatch %s: precinct rows %d != "
                                "slice %d" % (key, sums.get(key, 0), total))

        # 3b. Poll-scoped committee contests: per-precinct sums vs the
        # county summary countywide rows.  The named write-in detail rows
        # are contained in the printed Write-In Totals, so the aggregate
        # comparison uses the aggregate row itself, not the candidate sum.
        csv_agg = {}
        for r in precinct_rows:
            if r[2].startswith("Democratic Committee Member"):
                csv_agg.setdefault(r[2], {})[r[5]] = int(r[6])
        n_committee_rows = 0
        for title, cands in committee_totals.items():
            mine = csv_agg.get(title)
            if mine is None:
                failures.append(f"committee contest missing from precinct csv: {title}")
                continue
            for cand, total in cands.items():
                n_committee_rows += 1
                value = mine.get(cand)
                if value is None:
                    failures.append(f"committee mismatch {title}: {cand}: "
                                    f"row missing from precinct csv")
                elif cand == "Write-In Totals":
                    pass  # the named detail rows are contained in it
                elif value != int(total):
                    failures.append(f"committee mismatch {title}: {cand}: "
                                    f"precinct sum {value} != county summary "
                                    f"{total}")

        # 3c. DEM Registered Voters / Ballots Cast sums vs the countywide
        # STATISTICS block.
        stats_checks = []
        for office, label in (("Registered Voters", "DEMOCRATIC"),
                              ("Ballots Cast", "DEMOCRATIC")):
            expected = stat_totals.get((office, label))
            mine = sum(int(r[6]) for r in precinct_rows
                       if r[2] == office and r[4] == "DEM" and r[5] == "")
            stats_checks.append((office, label, mine, expected))
            if expected is None:
                failures.append(f"county summary STATISTICS lacks {office} "
                                f"{label}")
            elif mine != expected:
                failures.append(f"stats mismatch {office} {label}: precinct "
                                f"sum {mine} != county summary {expected}")

        n_committee = sum(len(c) for c in committee_totals.values())
        print("verify: %d DOS-fallback named-candidate sums vs county anchor "
              "(also vs the DOS slice), %d committee rows vs county summary, "
              "%d DEM stats sums" % (len(ANCHOR_TOTALS), n_committee,
                                     len(stats_checks)))
        if failures:
            for f in failures:
                print("  MISMATCH " + f)
            sys.exit(f"{len(failures)} verification failure(s)")
        print("verify: all checks passed")

    # 4. Write.
    for path, fieldnames, rows in (
            (Path(args.precinct_out), PRECINCT_FIELDNAMES, precinct_rows),
            (Path(args.county_out), COUNTY_FIELDNAMES, county_csv_rows)):
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(fieldnames)
            w.writerows(rows)
        print(f"wrote {len(rows)} data rows -> {path}")

    if warnings:
        print(f"WARNING: {len(warnings)} unmatched lines:")
        for w in warnings[:40]:
            print("  " + w)
        if len(warnings) > 40:
            print("  ...")
    return 0


if __name__ == "__main__":
    sys.exit(main())
