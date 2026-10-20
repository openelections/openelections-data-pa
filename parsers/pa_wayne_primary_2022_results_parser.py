#!/usr/bin/env python3
"""
Wayne County, PA 2022 General Primary parser (precinct-level).

Source: "Wayne PA 2022 General Primary Results by Precinct.pdf" -- a Dominion
"Statement of Votes Cast by Geography" report (same report family as the 2022
general, whose parser pa_wayne_general_2022_results_parser.py established this
OCR-consumption approach). The 2022 primary PDF is a SCAN with no text layer,
so the text is first produced with the hosted PaddleOCR API (or /tmp/paddleenv):

    /tmp/paddleenv/bin/python work2023p/ocr_api.py \
        "Wayne PA 2022 General Primary Results by Precinct.pdf" \
        work2022p/wayne/wayne2022p_union.txt

Pages 51-60 of the scan (hosted-OCR failures) are transcribed from page
images in work2022p/wayne/wayne2022p_manual.txt, and the scanner's misfed
pages 61-63 are re-ordered by their printed page labels (build_union.py
assembles both into the union file). The county's print is a 5/31
pre-certification SOVC ("Recount Completed 5-31-22"): its named-candidate
values run 1-16 below the DOS certified county file (which matches the ENR
anchor exactly) in most contests, so named-candidate votes for the six
state-contest families come from the DOS bulk precinct file
(work2022p/wayne/wayne_dos_precinct.json, extracted from
ElectionReturns_2022_Primary_PrecinctReturns.txt county code 64), with the
PDF ED/MI/PR breakdowns kept only when per-row consistent; write-ins,
State Committee rows and RV/BC are 5/31-print values.

This parser consumes the "===== PAGE N =====" markdown/HTML output. The
report prints each primary contest with its ballot party on the contest line
("UNITED STATES SENATOR - DEM (DEMOCR) (Vote for 1)"); party comes from the
contest header and is applied to every candidate row beneath it.

Contest identification is primarily by CANDIDATE NAME (each Wayne contest has
a distinct candidate set on the printed report), with the fixed
within-precinct contest order (CONTEST_ORDER) as fallback for write-in-only
or OCR-damaged blocks. Per-precinct municipal Democratic committee positions
("BER 1 DEM COM FEMALE (DEMOCR)", etc.) are SKIPPED, matching the county's
own 2024/2026 primary parses; the Democratic/Republican STATE Committee
offices are kept, as "Member of Democratic State Committee" /
"Member of Republican State Committee".

Each contest's candidate rows are validated against the report's own printed
"Total" row (votes and the ED/MI/PR breakdown columns); blocks that fail are
listed at the end of the run with their page numbers so they can be
transcribed from page images into CORRECTIONS below.

Precinct names are normalized to the county's established full forms (as used
by the county's 2024-primary and general Wayne precinct files).

Usage:
    python parsers/pa_wayne_primary_2022_results_parser.py <ocr_text_file> \
        <output_csv> work2022p/wayne/wayne_dos_precinct.json
"""

import csv
import html
import re
import sys

COUNTY = 'Wayne'

# ---------------------------------------------------------------- OCR text

PAGE_RE = re.compile(r'^===== PAGE (\d+) =====$', re.M)
TABLE_RE = re.compile(r'<table.*?</table>', re.S)
TR_RE = re.compile(r'<tr.*?</tr>', re.S)
TD_RE = re.compile(r'<td[^>]*>(.*?)</td>', re.S)
TAG_RE = re.compile(r'<[^>]+>')

# "recinct BERLIN TWP 1" -- the leading P of "Precinct" is sometimes lost by
# the OCR, but the token is always followed by a municipality name.
PRECINCT_RE = re.compile(r'^\s*(?:#+\s*)?[.>]*\s*[Pp]?recinct\s+(.+?)\s*$')
CONTEST_RE = re.compile(
    r'^\s*(?:#+\s*)?\s*(.+?)\s*\((DEMOCR|REPUBL|NONPARTISAN)\)\s*'
    r'\(Vote for\s*(\d+)\)(.*)$', re.I)
REGISTERED_RE = re.compile(r'([\d,]+)\s*registered')
BALLOTS_RE = re.compile(r'(\d[\d,]*)\s*ballots')
PAGE_HEADER_SKIPS = (
    'Statement of Votes Cast by Geography',
    'WAYNE COUNTY',
    'All Precincts, All Districts',
    'Total Ballots Cast:',
    'precincts reported out of',
    'Page: ',
)


def is_meta_name(name):
    n = name.lower()
    if n in ('choice', 'total'):
        return True
    return n.startswith('over') or n.startswith('under')


# The report truncates municipality names. Full forms match the county's
# established Wayne precinct files.
PRECINCT_FIXES = {
    'BERLIN TWP 1': 'BERLIN TOWNSHIP #1',
    'BERLIN TWP 2': 'BERLIN TOWNSHIP #2',
    'BETH BORO': 'BETHANY BOROUGH',
    'BETHANY BOR': 'BETHANY BOROUGH',
    'BUCK TWP': 'BUCKINGHAM TOWNSHIP',
    'BUCKINGHAM TWP': 'BUCKINGHAM TOWNSHIP',
    'CANAAN TWP': 'CANAAN TOWNSHIP',
    'CHE RDG TWP': 'CHERRY RIDGE TOWNSHIP',
    'CH RDG TWP': 'CHERRY RIDGE TOWNSHIP',
    'CHERRY RIDGE TWP': 'CHERRY RIDGE TOWNSHIP',
    'CLIN TWP 1': 'CLINTON TOWNSHIP #1',
    'CLIN TWP 2': 'CLINTON TOWNSHIP #2',
    'CLINTON TWP 1': 'CLINTON TOWNSHIP #1',
    'CLINTON TWP 2': 'CLINTON TOWNSHIP #2',
    'DAM TWP 1': 'DAMASCUS TOWNSHIP #1',
    'DAM TWP 2': 'DAMASCUS TOWNSHIP #2',
    'DAMASCUS TWP 1': 'DAMASCUS TOWNSHIP #1',
    'DAMASCUS TWP 2': 'DAMASCUS TOWNSHIP #2',
    'DREH TWP': 'DREHER TOWNSHIP',
    'DREHER TWP': 'DREHER TOWNSHIP',
    'DYB TWP': 'DYBERRY TOWNSHIP',
    'DYBERRY TWP': 'DYBERRY TOWNSHIP',
    'HAW BORO': 'HAWLEY BOROUGH',
    'HAWLEY BOR': 'HAWLEY BOROUGH',
    'HONES BORO 1': 'HONESDALE BOROUGH #1',
    'HONES BORO 2': 'HONESDALE BOROUGH #2',
    'HONES BORO 3': 'HONESDALE BOROUGH #3',
    'HONESDALE BOR 1': 'HONESDALE BOROUGH #1',
    'HONESDALE BOR 2': 'HONESDALE BOROUGH #2',
    'HONESDALE BOR 3': 'HONESDALE BOROUGH #3',
    'LAKE TWP': 'LAKE TOWNSHIP',
    'LEB TWP': 'LEBANON TOWNSHIP',
    'LEBANON TWP': 'LEBANON TOWNSHIP',
    'LEH TWP': 'LEHIGH TOWNSHIP',
    'LEHIGH TWP': 'LEHIGH TOWNSHIP',
    'MANCH TWP': 'MANCHESTER TOWNSHIP',
    'MANCHESTER TWP': 'MANCHESTER TOWNSHIP',
    'MOUNT PLEASANT TWP': 'MOUNT PLEASANT TOWNSHIP',
    'MT PL TWP': 'MOUNT PLEASANT TOWNSHIP',
    'MTPL TWP': 'MOUNT PLEASANT TOWNSHIP',
    'MOUNT PL TWP': 'MOUNT PLEASANT TOWNSHIP',
    'MT PLE TWP': 'MOUNT PLEASANT TOWNSHIP',
    'ORE TWP': 'OREGON TOWNSHIP',
    'OREGON TWP': 'OREGON TOWNSHIP',
    'PALM TWP': 'PALMYRA TOWNSHIP',
    'PALMYRA TWP': 'PALMYRA TOWNSHIP',
    'PAUP TWP': 'PAUPACK TOWNSHIP',
    'PAUPACK TWP': 'PAUPACK TOWNSHIP',
    'PREST TWP': 'PRESTON TOWNSHIP',
    'PRESTON TWP': 'PRESTON TOWNSHIP',
    'PROM BORO': 'PROMPTON BOROUGH',
    'PROMPTON BOR': 'PROMPTON BOROUGH',
    'SALEM TWP': 'SALEM TOWNSHIP',
    'SCOTT TWP': 'SCOTT TOWNSHIP',
    'SOU CANAAN TWP': 'SOUTH CANAAN TOWNSHIP',
    'SOCAN TWP': 'SOUTH CANAAN TOWNSHIP',
    'SOCAM TWP': 'SOUTH CANAAN TOWNSHIP',
    'SOUTH CANAAN TWP': 'SOUTH CANAAN TOWNSHIP',
    'STARR BORO': 'STARRUCCA BOROUGH',
    'STARRUCCA BOR': 'STARRUCCA BOROUGH',
    'STER TWP': 'STERLING TOWNSHIP',
    'STERLING TWP': 'STERLING TOWNSHIP',
    'TEX TWP 1': 'TEXAS TOWNSHIP #1',
    'TEX TWP 2': 'TEXAS TOWNSHIP #2',
    'TEX TWP 3': 'TEXAS TOWNSHIP #3',
    'TEXAS TWP 1': 'TEXAS TOWNSHIP #1',
    'TEXAS TWP 2': 'TEXAS TOWNSHIP #2',
    'TEXAS TWP 3': 'TEXAS TOWNSHIP #3',
    'WAY BORO': 'WAYMART BOROUGH',
    'WAYMART BOR': 'WAYMART BOROUGH',
}

# candidate name (lowercased) -> (office, district, party). District '8' for
# U.S. House: Wayne is entirely within PA-8 (county 2024/2026 precedent).
CANDIDATES = {
    'alexandria gloria khalil': ('U.S. Senate', '', 'DEM'),
    'alex khalil': ('U.S. Senate', '', 'DEM'),
    'alex khaili': ('U.S. Senate', '', 'DEM'),
    'conor j. lamb': ('U.S. Senate', '', 'DEM'),
    'conor lamb': ('U.S. Senate', '', 'DEM'),
    'malcolm kenyatta': ('U.S. Senate', '', 'DEM'),
    'john k. fetterman': ('U.S. Senate', '', 'DEM'),
    'john fetterman': ('U.S. Senate', '', 'DEM'),
    'joshua d. shapiro': ('Governor', '', 'DEM'),
    'josh shapiro': ('Governor', '', 'DEM'),
    'austin davis': ('Lieutenant Governor', '', 'DEM'),
    'raymond l. sosa': ('Lieutenant Governor', '', 'DEM'),
    'ray sosa': ('Lieutenant Governor', '', 'DEM'),
    'brian l. sims': ('Lieutenant Governor', '', 'DEM'),
    'brian sims': ('Lieutenant Governor', '', 'DEM'),
    'matthew a. cartwright': ('U.S. House', '8', 'DEM'),
    'matt cartwright': ('U.S. House', '8', 'DEM'),
    'jaclyn e. baker': ('State Senate', '20', 'DEM'),
    'jackie baker': ('State Senate', '20', 'DEM'),
    'jennifer anne shukaitis': ('State Senate', '40', 'DEM'),
    'jennifer shukaitis': ('State Senate', '40', 'DEM'),
    'meghan rosenfeld': ('State House', '139', 'DEM'),
    'meg rosenfeld': ('State House', '139', 'DEM'),
    'marian keegan': ('State House', '139', 'DEM'),
    'robin sampson': ('Member of Democratic State Committee', '', 'DEM'),
    'kathy j. barnette': ('U.S. Senate', '', 'REP'),
    'kathy barnette': ('U.S. Senate', '', 'REP'),
    'mehmet c. oz': ('U.S. Senate', '', 'REP'),
    'mehmet oz': ('U.S. Senate', '', 'REP'),
    'george a. bochetto': ('U.S. Senate', '', 'REP'),
    'george bochetto': ('U.S. Senate', '', 'REP'),
    'jeffrey a. bartos': ('U.S. Senate', '', 'REP'),
    'jeff bartos': ('U.S. Senate', '', 'REP'),
    'david h. mccormick': ('U.S. Senate', '', 'REP'),
    'david mccormick': ('U.S. Senate', '', 'REP'),
    'sean peter gale': ('U.S. Senate', '', 'REP'),
    'sean gale': ('U.S. Senate', '', 'REP'),
    'carla herd sands': ('U.S. Senate', '', 'REP'),
    'carla sands': ('U.S. Senate', '', 'REP'),
    'louis barletta': ('Governor', '', 'REP'),
    'lou barletta': ('Governor', '', 'REP'),
    'douglas v. mastriano': ('Governor', '', 'REP'),
    'nche zama': ('Governor', '', 'REP'),
    'david j. white': ('Governor', '', 'REP'),
    'dave white': ('Governor', '', 'REP'),
    'melissa a. hart': ('Governor', '', 'REP'),
    'melissa hart': ('Governor', '', 'REP'),
    'william m. mcswain': ('Governor', '', 'REP'),
    'bill mcswain': ('Governor', '', 'REP'),
    'charles r. gerow': ('Governor', '', 'REP'),
    'charlie gerow': ('Governor', '', 'REP'),
    'joseph charles gale': ('Governor', '', 'REP'),
    'joe gale': ('Governor', '', 'REP'),
    'jacob doyle corman iii': ('Governor', '', 'REP'),
    'jake corman': ('Governor', '', 'REP'),
    'clarice d. schillinger': ('Lieutenant Governor', '', 'REP'),
    'clarice schillinger': ('Lieutenant Governor', '', 'REP'),
    'james e. jones': ('Lieutenant Governor', '', 'REP'),
    'james earl jones': ('Lieutenant Governor', '', 'REP'),
    'richard saccone': ('Lieutenant Governor', '', 'REP'),
    'rick saccone': ('Lieutenant Governor', '', 'REP'),
    'john a. brown': ('Lieutenant Governor', '', 'REP'),
    'john brown': ('Lieutenant Governor', '', 'REP'),
    'christopher c. frye jr': ('Lieutenant Governor', '', 'REP'),
    'chris frye': ('Lieutenant Governor', '', 'REP'),
    'jeffrey h. coleman': ('Lieutenant Governor', '', 'REP'),
    'jeff coleman': ('Lieutenant Governor', '', 'REP'),
    'russell h. diamond': ('Lieutenant Governor', '', 'REP'),
    'russ diamond': ('Lieutenant Governor', '', 'REP'),
    'carrie lewis delrosso': ('Lieutenant Governor', '', 'REP'),
    'carrie delrosso': ('Lieutenant Governor', '', 'REP'),
    'theodore daniels': ('Lieutenant Governor', '', 'REP'),
    'teddy daniels': ('Lieutenant Governor', '', 'REP'),
    'james rocco bognet': ('U.S. House', '8', 'REP'),
    'jim bognet': ('U.S. House', '8', 'REP'),
    'michael paul marsicano': ('U.S. House', '8', 'REP'),
    'mike marsicano': ('U.S. House', '8', 'REP'),
    'elisabeth j. baker': ('State Senate', '20', 'REP'),
    'lisa baker': ('State Senate', '20', 'REP'),
    'rosemary maula brown': ('State Senate', '40', 'REP'),
    'rosemary brown': ('State Senate', '40', 'REP'),
    'joseph w. adams': ('State House', '139', 'REP'),
    'elefterie g. balu': ('State House', '139', 'REP'),
    'theo balu': ('State House', '139', 'REP'),
    'robert beierle': ('State House', '139', 'REP'),
    'jonathan a. fritz': ('State House', '111', 'REP'),
    'jonathan fritz': ('State House', '111', 'REP'),
    'thomas j. shepstone': ('Member of Republican State Committee', '', 'REP'),
    'tonette mcandrew': ('Member of Republican State Committee', '', 'REP'),
    'toni mcandrew': ('Member of Republican State Committee', '', 'REP'),
}

# Display name used when the OCR prints a variant spelling (all keys map to
# the same office above).
NAME_FIX = {
    'alex khaili': 'Alex Khalil',
    'toni mcandrew': 'Toni McAndrew',
}

# Per-precinct State House district (from the DOS county-code-64 precinct
# file; used to place write-in-only State House blocks whose header the OCR
# dropped).
# Per-precinct State House district (DOS county-code-64 file); used to place write-in-only State House blocks whose header the OCR dropped.
PRECINCT_HOUSE = {
    'BERLIN TOWNSHIP #1': '111',
    'BERLIN TOWNSHIP #2': '111',
    'BETHANY BOROUGH': '111',
    'BUCKINGHAM TOWNSHIP': '111',
    'CANAAN TOWNSHIP': '111',
    'CHERRY RIDGE TOWNSHIP': '139',
    'CLINTON TOWNSHIP #1': '111',
    'CLINTON TOWNSHIP #2': '111',
    'DAMASCUS TOWNSHIP #1': '111',
    'DAMASCUS TOWNSHIP #2': '111',
    'DREHER TOWNSHIP': '139',
    'DYBERRY TOWNSHIP': '111',
    'HAWLEY BOROUGH': '139',
    'HONESDALE BOROUGH #1': '111',
    'HONESDALE BOROUGH #2': '111',
    'HONESDALE BOROUGH #3': '111',
    'LAKE TOWNSHIP': '139',
    'LEBANON TOWNSHIP': '111',
    'LEHIGH TOWNSHIP': '139',
    'MANCHESTER TOWNSHIP': '111',
    'MOUNT PLEASANT TOWNSHIP': '111',
    'OREGON TOWNSHIP': '111',
    'PALMYRA TOWNSHIP': '139',
    'PAUPACK TOWNSHIP': '139',
    'PRESTON TOWNSHIP': '111',
    'PROMPTON BOROUGH': '111',
    'SALEM TOWNSHIP': '139',
    'SCOTT TOWNSHIP': '111',
    'SOUTH CANAAN TOWNSHIP': '139',
    'STARRUCCA BOROUGH': '111',
    'STERLING TOWNSHIP': '139',
    'TEXAS TOWNSHIP #1': '111',
    'TEXAS TOWNSHIP #2': '111',
    'TEXAS TOWNSHIP #3': '111',
    'WAYMART BOROUGH': '111',
}

# Each precinct's State Senate district (same source). SD-20 towns list
# Jackie/Lisa Baker; SD-40 towns list Shukaitis/Rosemary Brown.
# Each precinct State Senate district (DOS county-code-64 file): SD-20 towns list the Bakers; SD-40 towns Shukaitis/Rosemary Brown.
# Precincts whose per-party ballots/registered line the OCR lost entirely
# (transcribed from the page images; values = the report's own meta lines).
BALLOTS_FIX = {
    'MANCHESTER TOWNSHIP': {'DEM': (40, 114), 'REP': (162, 371)},
}

PRECINCT_SENATE = {
    'BERLIN TOWNSHIP #1': '20',
    'BERLIN TOWNSHIP #2': '20',
    'BETHANY BOROUGH': '20',
    'BUCKINGHAM TOWNSHIP': '20',
    'CANAAN TOWNSHIP': '40',
    'CHERRY RIDGE TOWNSHIP': '40',
    'CLINTON TOWNSHIP #1': '20',
    'CLINTON TOWNSHIP #2': '20',
    'DAMASCUS TOWNSHIP #1': '20',
    'DAMASCUS TOWNSHIP #2': '20',
    'DREHER TOWNSHIP': '40',
    'DYBERRY TOWNSHIP': '20',
    'HAWLEY BOROUGH': '20',
    'HONESDALE BOROUGH #1': '40',
    'HONESDALE BOROUGH #2': '40',
    'HONESDALE BOROUGH #3': '40',
    'LAKE TOWNSHIP': '40',
    'LEBANON TOWNSHIP': '20',
    'LEHIGH TOWNSHIP': '40',
    'MANCHESTER TOWNSHIP': '20',
    'MOUNT PLEASANT TOWNSHIP': '20',
    'OREGON TOWNSHIP': '20',
    'PALMYRA TOWNSHIP': '20',
    'PAUPACK TOWNSHIP': '20',
    'PRESTON TOWNSHIP': '20',
    'PROMPTON BOROUGH': '40',
    'SALEM TOWNSHIP': '40',
    'SCOTT TOWNSHIP': '20',
    'SOUTH CANAAN TOWNSHIP': '40',
    'STARRUCCA BOROUGH': '20',
    'STERLING TOWNSHIP': '40',
    'TEXAS TOWNSHIP #1': '40',
    'TEXAS TOWNSHIP #2': '40',
    'TEXAS TOWNSHIP #3': '40',
    'WAYMART BOROUGH': '40',
}

# Fixed contest order within each precinct (fallback when a block's header is
# lost and it only carries write-ins). Full slot list; the per-precinct
# district slots are resolved through PRECINCT_HOUSE/PRECINCT_SENATE.
CONTEST_ORDER = [
    ('U.S. Senate', '', 'DEM'),
    ('U.S. Senate', '', 'REP'),
    ('Governor', '', 'DEM'),
    ('Governor', '', 'REP'),
    ('Lieutenant Governor', '', 'DEM'),
    ('Lieutenant Governor', '', 'REP'),
    ('U.S. House', '8', 'DEM'),
    ('U.S. House', '8', 'REP'),
    ('State Senate', '20', 'DEM'),
    ('State Senate', '20', 'REP'),
    ('State Senate', '40', 'DEM'),
    ('State Senate', '40', 'REP'),
    ('State House', '139', 'DEM'),
    ('State House', '139', 'REP'),
    ('State House', '111', 'DEM'),
    ('State House', '111', 'REP'),
    ('Member of Democratic State Committee', '', 'DEM'),
    ('Member of Republican State Committee', '', 'REP'),
]

# (precinct, office, district, party) -> {'rows': [...], 'total': int},
# transcribed from page images where PaddleOCR scrambled/merged the table
# (values verified against the printed Total row). Blocks present only here
# (not parsed at all) are emitted as-is.
def C(precinct, office, district, party, total, *rows):
    def row(candidate, votes, ed, mi, pr):
        return {
            'county': COUNTY, 'precinct': precinct, 'office': office,
            'district': district, 'party': party, 'candidate': candidate,
            'votes': str(votes), 'election_day': str(ed),
            'mail': str(mi), 'provisional': str(pr),
        }
    CORRECTIONS[(precinct, office, district, party)] = {
        'rows': [row(*r) for r in rows], 'total': total,
    }


CORRECTIONS = {}

# Blocks transcribed from the page images where the OCR lost rows/totals or
# mislabeled them (values verified against the printed Total rows and the
# DOS certified county file).
C('HONESDALE BOROUGH #2', 'State House', '111', 'DEM', 18,
  ('Write Ins', 18, 15, 3, 0))
C('SALEM TOWNSHIP', 'Member of Democratic State Committee', '', 'DEM', 259,
  ('Robin Sampson', 259, 134, 125, 0),
  ('Write Ins', 0, 0, 0, 0))
C('SALEM TOWNSHIP', 'Lieutenant Governor', '', 'REP', 711,
  ('Clarice Schillinger', 49, 46, 3, 0),
  ('James Earl Jones', 30, 27, 3, 0),
  ('Rick Saccone', 20, 15, 5, 0),
  ('John Brown', 15, 14, 1, 0),
  ('Chris Frye', 10, 8, 2, 0),
  ('Jeff Coleman', 26, 19, 7, 0),
  ('Russ Diamond', 11, 8, 3, 0),
  ('Carrie Lewis DelRosso', 152, 124, 28, 0),
  ('Teddy Daniels', 396, 361, 34, 1),
  ('Write Ins', 2, 2, 0, 0))
C('CLINTON TOWNSHIP #2', 'U.S. Senate', '', 'DEM', 80,
  ('Alex Khalil', 8, 8, 0, 0),
  ('Conor Lamb', 12, 10, 2, 0),
  ('Malcolm Kenyatta', 6, 6, 0, 0),
  ('John Fetterman', 54, 35, 19, 0),
  ('Write Ins', 0, 0, 0, 0))
C('TEXAS TOWNSHIP #2', 'State Senate', '40', 'REP', 101,
  ('Rosemary Brown', 99, 78, 21, 0),
  ('Write Ins', 2, 2, 0, 0))
C('WAYMART BOROUGH', 'Lieutenant Governor', '', 'REP', 211,
  ('Clarice Schillinger', 20, 19, 1, 0),
  ('James Earl Jones', 6, 4, 2, 0),
  ('Rick Saccone', 6, 6, 0, 0),
  ('John Brown', 5, 5, 0, 0),
  ('Chris Frye', 5, 5, 0, 0),
  ('Jeff Coleman', 6, 5, 1, 0),
  ('Russ Diamond', 6, 6, 0, 0),
  ('Carrie Lewis DelRosso', 65, 62, 3, 0),
  ('Teddy Daniels', 90, 83, 7, 0),
  ('Write Ins', 2, 2, 0, 0))


# The 35 precincts of Wayne County (canonical names); when the last one is
# reached, everything after it in the report is the county-level recap
# section (same totals as the companion summary) and must be ignored.
ALL_PRECINCTS = [
    'BERLIN TOWNSHIP #1', 'BERLIN TOWNSHIP #2', 'BETHANY BOROUGH',
    'BUCKINGHAM TOWNSHIP', 'CANAAN TOWNSHIP', 'CHERRY RIDGE TOWNSHIP',
    'CLINTON TOWNSHIP #1', 'CLINTON TOWNSHIP #2', 'DAMASCUS TOWNSHIP #1',
    'DAMASCUS TOWNSHIP #2', 'DREHER TOWNSHIP', 'DYBERRY TOWNSHIP',
    'HAWLEY BOROUGH', 'HONESDALE BOROUGH #1', 'HONESDALE BOROUGH #2',
    'HONESDALE BOROUGH #3', 'LAKE TOWNSHIP', 'LEBANON TOWNSHIP',
    'LEHIGH TOWNSHIP', 'MANCHESTER TOWNSHIP', 'MOUNT PLEASANT TOWNSHIP',
    'OREGON TOWNSHIP', 'PALMYRA TOWNSHIP', 'PAUPACK TOWNSHIP',
    'PRESTON TOWNSHIP', 'PROMPTON BOROUGH', 'SALEM TOWNSHIP',
    'SCOTT TOWNSHIP', 'SOUTH CANAAN TOWNSHIP', 'STARRUCCA BOROUGH',
    'STERLING TOWNSHIP', 'TEXAS TOWNSHIP #1', 'TEXAS TOWNSHIP #2',
    'TEXAS TOWNSHIP #3', 'WAYMART BOROUGH',
]


def strip_tags(s):
    return html.unescape(TAG_RE.sub('', s)).strip()


# OCR renders merged cells as literal "\\n" (or a real newline).
SEP_CHARS = ('\n', r'\n')


def _has_sep(s):
    return '\n' in s or '\\n' in s


def _split_sep(s):
    """Split a cell on OCR merged-row separators (real or literal \\n)."""
    return [b for b in re.split(r'\n|\\n', s.strip()) if b.strip()]


def page_lines(text):
    """Yield (page_no, kind, content) events from the OCR markdown.

    kind: 'line' for plain/markdown lines, 'row' for a list of table cells.
    """
    for m in PAGE_RE.finditer(text):
        page = int(m.group(1))
        start = m.end()
        nxt = PAGE_RE.search(text, start)
        block = text[start:nxt.start() if nxt else len(text)]
        pos = 0
        for t in TABLE_RE.finditer(block):
            for line in block[pos:t.start()].split('\n'):
                yield page, 'line', line
            for tr in TR_RE.findall(t.group(0)):
                cells = [strip_tags(c) for c in TD_RE.findall(tr)]
                yield page, 'row', cells
            pos = t.end()
        for line in block[pos:].split('\n'):
            yield page, 'line', line


def normalize_header(text, party_raw):
    """Contest header text (+ party abbr) -> (office, district, party, skip)."""
    t = text.upper()
    p = party_raw.upper()
    party = 'DEM' if p.startswith('DEM') else \
        'REP' if p.startswith('REP') else 'NON'
    # Strip the "- DEM"/"-DEM" ballot-party suffix if present.
    t = re.sub(r'\s*-\s*\b(DEMOCR|REPUBL|DEMO?|REPUB|R)\b\.?",?\s*$', '', t)
    t = re.sub(r'\b(DEM|REP)\b\s*$', '', t).strip(' -')
    if 'UNITED STATES SENATOR' in t or re.search(r'\bU\.?S\.? SENATOR', t):
        return ('U.S. Senate', '', party, False)
    if 'LIEUTENANT GOVERNOR' in t or 'LT. GOVERNOR' in t or 'LT GOVERNOR' in t:
        return ('Lieutenant Governor', '', party, False)
    if 'GOVERNOR' in t:
        return ('Governor', '', party, False)
    if 'CONGRESS' in t:
        return ('U.S. House', '8', party, False)
    dm = re.search(r'\b(\d+)\s*(?:ST|ND|RD|TH)\b', t)
    if 'SENATOR IN THE GENERAL ASSEMBLY' in t:
        return ('State Senate', dm.group(1) if dm else '', party, False)
    if 'REPRESENTATIVE IN THE GENERAL ASSEMBLY' in t:
        return ('State House', dm.group(1) if dm else '', party, False)
    if 'DEMOCRATIC STATE COMMITTEE' in t:
        return ('Member of Democratic State Committee', '', 'DEM', False)
    if 'REPUBLICAN STATE COMMITTEE' in t:
        return ('Member of Republican State Committee', '', 'REP', False)
    # Municipal committee seats (e.g. "BER 1 DEM COM FEMALE") -- skipped.
    if re.search(r'\bCOM\b', t):
        return (text.strip(), '', party, True)
    return (text.strip(), '', party, False)


class Parser:
    def __init__(self, county=COUNTY):
        self.county = county
        self.results = []
        self.problems = []
        self.blocks = {}      # (precinct, office, district, party) -> dict
        self.block_order = []
        self.precinct = None
        self.page = 0
        self.seen_rv = {}     # precinct -> {party: (ballots, registered)}
        self.contest_idx = {}  # precinct -> next unassigned CONTEST_ORDER idx
        self.pending_rows = []
        self.pending_total = None
        self.pending_pages = set()
        self.pending_skip = False
        self.pending_rv_key = None   # party whose ballots/registered line is next
        self.pending_over = None
        self.countywide = False
        self.seen_precincts = set()

    # -------------------------------------------------- helpers
    def next_expected(self):
        if self.precinct is None:
            return None
        # Write-in-only / unidentified blocks: choose among unfilled slots
        # that come after every already-filled slot in the within-precinct
        # order, EXCLUDING slots for districts this precinct doesn't have.
        filled = [CONTEST_ORDER.index((k[1], k[2], k[3]))
                  for k in self.block_order if k[0] == self.precinct]
        start = (max(filled) + 1) if filled else 0
        house = PRECINCT_HOUSE.get(self.precinct)
        senate = PRECINCT_SENATE.get(self.precinct)
        cand = []
        for i, (office, dist, party) in enumerate(CONTEST_ORDER):
            if i < start or (self.precinct, office, dist, party) \
                    in self.blocks:
                continue
            if office == 'State Senate':
                if dist != senate:
                    continue   # report lists only the precinct's own SD
            if office == 'State House':
                if dist != house:
                    continue   # report lists only the precinct's own SHD
            cand.append((i, (office, dist, party)))
        return cand[0][1] if cand else None

    def new_block(self, key):
        if key not in self.blocks:
            self.blocks[key] = {'rows': [], 'total': None, 'total_row': None,
                                'pages': set()}
            self.block_order.append(key)
        return self.blocks[key]

    def add_rv_bc(self, ballots, reg, party):
        if self.precinct is None or ballots is None or reg is None:
            return
        if party not in ('DEM', 'REP'):
            return
        d = self.seen_rv.setdefault(self.precinct, {})
        if party in d:
            old = d[party]
            if old != (ballots, reg):
                self.problems.append(
                    (self.page, self.precinct,
                     f'RV/BC {party} mismatch: {old} vs {(ballots, reg)}',
                     None))
            return
        d[party] = (ballots, reg)

    def handle_precinct(self, name):
        name = name.upper().strip().rstrip('.')
        self.precinct = PRECINCT_FIXES.get(name, name)
        self.countywide = False
        self.seen_precincts.add(self.precinct)
        self.contest_idx[self.precinct] = 0
        self.pending_rows = []
        self.pending_total = None
        self.pending_skip = False

    def flush_table(self):
        if not self.pending_rows and self.pending_total is None:
            return
        if self.pending_over is not None and not self.pending_rows:
            # stray over-total fragment; attach if some block lacks a total
            pass
        if self.pending_skip:
            self.pending_rows = []
            self.pending_total = None
            self.pending_pages = set()
            return
        # Total-only table (page-break continuation): attach to the most
        # recent block for this precinct that still lacks a total.
        if not self.pending_rows:
            for key in reversed(self.block_order):
                if key[0] == self.precinct:
                    blk = self.blocks[key]
                    if blk['total'] is None:
                        blk['total'] = self.pending_total['votes']
                        blk['total_row'] = self.pending_total
                    break
            self.pending_total = None
            return
        # identify contest (office, district, party)
        offices = set()
        districts = set()
        parties = set()
        for r in self.pending_rows:
            hit = CANDIDATES.get(r['candidate'].lower())
            if hit:
                offices.add(hit[0])
                districts.add(hit[1])
                parties.add(hit[2])
        if len(offices) == 1:
            office = offices.pop()
            district = districts.pop() if len(districts) == 1 else ''
            party = parties.pop() if len(parties) == 1 else ''
        elif not offices:
            # Try to complete the most recent block of this precinct that
            # still lacks a total: page-break fragments often carry only the
            # trailing Write-in + Total rows of the previous contest.
            done = False
            if self.pending_total is not None:
                sp = sum(int(r['votes']) for r in self.pending_rows)
                for key2 in reversed(self.block_order):
                    if key2[0] != self.precinct:
                        continue
                    blk2 = self.blocks[key2]
                    if blk2['total'] is not None:
                        continue
                    s2 = 0
                    for r in blk2['rows']:
                        try:
                            s2 += int(r['votes'])
                        except ValueError:
                            break
                    try:
                        sp2 = sp + s2
                    except ValueError:
                        continue
                    if sp2 is not None and sp2 == self.pending_total['votes']                             and len(blk2['rows']) >= 1:
                        for r in self.pending_rows:
                            r['office'], r['district'], r['party'] = key2[1:]
                            blk2['rows'].append(r)
                        blk2['total'] = self.pending_total['votes']
                        blk2['total_row'] = self.pending_total
                        if self.pending_over is not None:
                            blk2['over_row'] = self.pending_over
                            self.pending_over = None
                        self.problems.append(
                            (self.page, self.precinct,
                             'page-break fragment merged into previous '
                             'block (total matches)', None))
                        self.pending_rows = []
                        self.pending_total = None
                        self.pending_pages = set()
                        done = True
                        break
            if done:
                return
            key = self.next_expected()
            if key is None:
                self.problems.append(
                    (self.page, self.precinct,
                     'write-ins only, no slot in CONTEST_ORDER',
                     self.pending_rows))
                self.pending_rows = []
                self.pending_total = None
                self.pending_pages = set()
                return
            office, district, party = key
        else:
            # OCR merged two contests' rows into one table: split into
            # contiguous identified runs, keep write-ins with their run
            runs = []
            for r in self.pending_rows:
                hit = CANDIDATES.get(r['candidate'].lower())
                k = hit if hit else None
                if k is not None and runs and runs[-1][0] == (k[0], k[1], k[2]):
                    runs[-1][1].append(r)
                elif k is not None:
                    runs.append([(k[0], k[1], k[2]), [r]])
                else:
                    if runs:
                        runs[-1][1].append(r)   # write-ins join last run
                    else:
                        self.problems.append(
                            (self.page, self.precinct,
                             'mixed table, orphan row dropped',
                             r))
            if len(runs) <= 1:
                self.problems.append((self.page, self.precinct,
                                      'mixed offices',
                                      self.pending_rows))
                self.pending_rows = []
                self.pending_total = None
                self.pending_pages = set()
                return
            self.problems.append((self.page, self.precinct,
                                  'merged table split into '
                                  f'{len(runs)} runs '
                                  f'({[r[0][0] for r in runs]})', None))
            # attach the total to the run whose candidate rows (incl. any
            # write-ins already split) sum to it, else the last run lacking
            # a total
            total_row = self.pending_total
            chosen = None
            if total_row is not None:
                for i, (k2, rws) in enumerate(runs):
                    s = sum(int(r['votes']) for r in rws)
                    if s == total_row['votes']:
                        chosen = i
                        break
            if chosen is None:
                for i in range(len(runs) - 1, -1, -1):
                    k2 = runs[i][0]
                    if (self.precinct, k2[0], k2[1], k2[2]) not in self.blocks:
                        chosen = i
                        break
            for i, (k2, rws) in enumerate(runs):
                office, district, party = k2
                if i != chosen and total_row is not None:
                    pass
                self._emit_run(k2, rws,
                               total_row if i == chosen else None,
                               self.pending_pages)
            self.pending_rows = []
            self.pending_total = None
            self.pending_pages = set()
            self.pending_over = None
            return
        if office in ('State Senate', 'State House') and self.precinct \
                is not None:
            want = (PRECINCT_SENATE if office == 'State Senate'
                    else PRECINCT_HOUSE).get(self.precinct)
            if want is not None and district != want and district != '':
                # county-level recap rows for a district this precinct
                # doesn't vote in (or mis-OCR'd header)
                self.problems.append(
                    (self.page, self.precinct,
                     f'dropped {office} {district} {party} rows (not this '
                     'precinct district)', None))
                self.pending_rows = []
                self.pending_total = None
                self.pending_pages = set()
                self.pending_over = None
                return
        key = (self.precinct, office, district, party)
        in_recap = len(self.seen_precincts) >= len(ALL_PRECINCTS)
        if in_recap and key in self.blocks and self.blocks[key]['rows']:
            # second listing of a contest this precinct already has = the
            # county-level recap section; drop it
            self.problems.append(
                (self.page, self.precinct,
                 f'dropped duplicate/recap rows for {office}', None))
            self.pending_rows = []
            self.pending_total = None
            self.pending_pages = set()
            self.pending_over = None
            return
        if self.precinct is not None and (office, district, party) \
                in CONTEST_ORDER:
            idx = CONTEST_ORDER.index((office, district, party))
            if idx >= self.contest_idx.get(self.precinct, 0):
                self.contest_idx[self.precinct] = idx + 1
        blk = self.new_block(key)
        blk['pages'] |= self.pending_pages
        if self.pending_total is not None:
            blk['total'] = self.pending_total['votes']
            blk['total_row'] = self.pending_total
        if self.pending_over is not None:
            blk['over_row'] = self.pending_over
            self.pending_over = None
        for r in self.pending_rows:
            hit = CANDIDATES.get(r['candidate'].lower())
            if hit:
                r['office'], r['district'], r['party'] = hit
            elif r['candidate'] == 'Write Ins':
                r['office'], r['district'], r['party'] = \
                    office, district, party
            else:
                self.problems.append(
                    (self.page, self.precinct, 'dropped unknown candidate',
                     r))
                continue
            blk['rows'].append(r)
        self.pending_rows = []
        self.pending_total = None
        self.pending_pages = set()

    def _emit_run(self, k, rows, total_row, pages, over_row=None):
        """Emit a contiguous identified run of rows as its own block."""
        office, district, party = k
        if self.precinct is not None and (office, district, party) \
                in CONTEST_ORDER:
            idx = CONTEST_ORDER.index((office, district, party))
            if idx >= self.contest_idx.get(self.precinct, 0):
                self.contest_idx[self.precinct] = idx + 1
        blk = self.new_block((self.precinct, office, district, party))
        blk['pages'] |= pages
        if total_row is not None:
            blk['total'] = total_row['votes']
            blk['total_row'] = total_row
        if over_row is not None:
            blk['over_row'] = over_row
        for r in rows:
            hit = CANDIDATES.get(r['candidate'].lower())
            if hit:
                r['office'], r['district'], r['party'] = hit
            elif r['candidate'] == 'Write Ins':
                r['office'], r['district'], r['party'] = \
                    office, district, party
            else:
                self.problems.append(
                    (self.page, self.precinct,
                     'dropped unknown candidate', r))
                continue
            blk['rows'].append(r)
        return blk

    # -------------------------------------------------- main loop
    def feed(self, text):
        for page, kind, content in page_lines(text):
            self.page = page
            if kind == 'line':
                line = strip_tags(content)
                if line:
                    self.handle_line(line)
                elif self.pending_rows:
                    self.flush_table()
            else:
                self.handle_row(content)
        self.flush_table()

    def handle_header(self, text, party_raw):
        office, district, party, skip = normalize_header(text, party_raw)
        self.flush_table()
        if skip:
            self.pending_skip = True
            self.pending_rv_key = None
            return
        self.pending_skip = False
        self.pending_rv_key = party
        if self.precinct and (office, district, party) in CONTEST_ORDER:
            self.contest_idx[self.precinct] = \
                CONTEST_ORDER.index((office, district, party)) + 1

    def handle_line(self, line):
        if any(s in line for s in PAGE_HEADER_SKIPS):
            return
        if line.strip().rstrip('# ') == 'All Precincts':
            # countywide section at the end of the report -- not this
            # precinct's rows
            self.flush_table()
            self.countywide = True
            self.precinct = None
            return
        pm = PRECINCT_RE.match(line)
        if pm:
            self.flush_table()
            self.handle_precinct(pm.group(1))
            return
        if self.countywide:
            return
        cm = CONTEST_RE.match(line)
        if cm:
            self.handle_header(cm.group(1), cm.group(2))
            return
        if ('registered' in line or 'ballots' in line) \
                and 'vote for' not in line.lower():
            self.handle_meta(line)

    def handle_meta(self, line):
        rm = REGISTERED_RE.search(line)
        bm = BALLOTS_RE.search(line)
        if rm and self.precinct is not None:
            reg = int(rm.group(1).replace(',', ''))
            ballots = None
            if bm:
                ballots = int(bm.group(1).replace(',', ''))
            party = self.pending_rv_key
            self.add_rv_bc(ballots, reg, party)

    def handle_row(self, cells):
        if not cells:
            return
        joined = ' | '.join(cells)
        if any(s in joined for s in PAGE_HEADER_SKIPS):
            return
        flat = strip_tags(joined)
        pm = PRECINCT_RE.match(flat)
        if pm:
            self.flush_table()
            self.handle_precinct(pm.group(1))
            return
        if self.countywide:
            return
        cm = CONTEST_RE.match(flat)
        if cm:
            self.handle_header(cm.group(1), cm.group(2))
            return
        if 'registered' in flat.lower() and 'choice' not in flat.lower():
            self.handle_meta(flat)
            return
        if self.precinct is None:
            return
        if self.pending_skip:
            return
        if len(cells) not in (5, 6, 7):
            return
        if len(cells) == 7:
            # occasional stray trailing cell from the OCR
            cells = cells[:6]
        name = cells[0].replace('\n', ' ').strip()
        if is_meta_name(name):
            if name.lower() == 'total':
                self.pending_total = {
                    'votes': self._int(cells[1]),
                    'election_day': self._int(cells[3]) if len(cells) > 3 else None,
                    'mail': self._int(cells[4]) if len(cells) > 4 else None,
                    'provisional': self._int(cells[5]) if len(cells) > 5 else None,
                }
            elif name.lower().startswith('over') and len(cells) >= 6 \
                    and self._int(cells[1]) is not None \
                    and cells[3].strip().isdigit():
                # an "Overvotes" row that carries real values is the true
                # Total row after the OCR shifted labels in merged tables
                self.pending_over = {
                    'votes': self._int(cells[1]),
                    'election_day': self._int(cells[3]),
                    'mail': self._int(cells[4]),
                    'provisional': self._int(cells[5]),
                }
            return
        # merged candidate rows inside one cell ("Josh Shapiro\nWrite-in",
        # with matching \n in the value cells)
        if any(_has_sep(strip_tags(c)) for c in cells):
            if self._split_merged_row(cells):
                return
        self._ingest_row(cells)

    def _split_merged_row(self, cells):
        """Rows merged into one <tr>: split on the separator if every cell
        splits into the same number of fields (values apportioned evenly, as
        in "Josh Shapiro\\nWrite-in | 3116\\n48 | ...").
        Fallback: merged NAME cell only ("Robin Sampson\\nWrite-in" with a
        single value set): give the values to the FIRST candidate and zeros
        to the remaining (write-in) ones -- only when the printed Total row
        matches those values."""
        parts = [_split_sep(strip_tags(c)) for c in cells]
        if any(len(p) <= 1 for p in parts):
            k = _split_sep(strip_tags(cells[0]))
            # uneven case: name cell merged, value cells single
            if len(parts[0]) <= 1 or len(parts[0]) != len(k):
                return False
            j = len(parts[0])
            singles = [len(p) == 1 for p in parts[1:]]
            if not all(singles) or len(parts) not in (5, 6):
                return False
            self.problems.append(
                (self.page, self.precinct,
                 f'merged (uneven) row split: {j} candidates', None))
            for i in range(j):
                if len(parts) == 6:
                    row_cells = [parts[0][i], parts[1][0] if i == 0 else '0',
                                 parts[2][0], parts[3][0] if i == 0 else '0',
                                 parts[4][0] if i == 0 else '0',
                                 parts[5][0] if i == 0 else '0']
                else:
                    row_cells = [parts[0][i], parts[1][0] if i == 0 else '0',
                                 parts[2][0], parts[3][0] if i == 0 else '0',
                                 parts[4][0] if i == 0 else '0', '0']
                self._ingest_row(row_cells)
            return True
        k = len(parts[0])
        if len({len(p) for p in parts}) != 1 or len(parts) not in (5, 6):
            return False
        # votes and the ED/MI/PR cells must be numeric
        for i in (1, 3, 4, 5) if len(parts) == 6 else (1, 3, 4):
            if not all(b.isdigit() for b in parts[i]):
                return False
        if len(parts) == 5:
            parts.append(['0'] * k)   # PR lost at the page edge
        self.problems.append(
            (self.page, self.precinct, f'merged row split into {k}', None))
        for i in range(k):
            self._ingest_row([p[i] for p in parts])
        return True

    def _ingest_row(self, cells):
        name = cells[0].strip().replace('\n', ' ').strip()
        low = name.lower()
        # OCR noise fragments (numeric/empty first cells) are not candidates
        if not low or not re.search(r'[A-Za-z]', low):
            self.problems.append(
                (self.page, self.precinct, 'numeric/empty name row', cells))
            return
        if low.startswith('write-in') or low.startswith('write in') \
                or low.startswith('write ins') or low.startswith('writeins'):
            candidate = 'Write Ins'
        else:
            candidate = NAME_FIX.get(low, name)
        cells = list(cells)
        if len(cells) == 5:
            # PR column lost at the page edge in the OCR -- padded with 0
            self.problems.append(
                (self.page, self.precinct, '5-cell row, PR padded 0', cells))
            cells.append('0')
        self.pending_pages.add(self.page)
        self.pending_rows.append({
            'county': self.county, 'precinct': self.precinct,
            'office': '', 'district': '', 'party': '',
            'candidate': candidate,
            'votes': cells[1].strip().replace(',', ''),
            'election_day': cells[3].strip().replace(',', ''),
            'mail': cells[4].strip().replace(',', ''),
            'provisional': cells[5].strip().replace(',', ''),
        })

    @staticmethod
    def _int(v):
        try:
            return int(v.replace(',', ''))
        except (ValueError, AttributeError):
            return None

    # -------------------------------------------------- output
    def build(self, dos):
        """Assemble final rows.

        Named-candidate state-contest votes come from the DOS certified
        per-precinct file (matches the ENR anchor exactly); ED/MI/PR
        breakdowns from the county PDF where per-row consistent. Write-ins,
        State Committee rows and per-party Registered Voters / Ballots Cast
        come from the county PDF as printed; write-in values are re-derived
        from each contest's printed Total row where the OCR lost rows.
        """
        state_offices = {'U.S. Senate', 'Governor', 'Lieutenant Governor',
                         'U.S. House', 'State Senate', 'State House'}
        failures = []
        named_out = {}
        other_out = []
        self.emitted = set()
        for key in self.block_order:
            self.emitted.add(key)
            prec, office, dist, party = key
            blk = self.blocks[key]
            corr = CORRECTIONS.get(key)
            rows = corr['rows'] if corr else blk['rows']
            trow = ((corr.get('total_row') if corr else None)
                    or blk.get('total_row'))
            total = (corr['total'] if corr and corr.get('total') is not None
                     else blk['total'])
            # OCR-shifted merged tables: an "Overvotes"-labeled row carries
            # the true Total values
            if not corr and blk.get('over_row'):
                s = sum(self._int(r['votes']) or 0 for r in rows)
                ov = blk['over_row']
                # the shifted-rows pattern: the OCR mislabels rows so the
                # printed Total lands on the "Overvotes" name; it can equal
                # the parsed rows (missing write-in row = 0) or exceed them
                # by 1-9 lost write-in votes
                if total is not None and total != s and \
                        0 <= ov['votes'] - s <= 9:
                    trow = {'votes': ov['votes'],
                            'election_day': ov['election_day'],
                            'mail': ov['mail'],
                            'provisional': ov['provisional']}
                    total = ov['votes']
                    self.problems.append(
                        (0, prec, 'garbage Total replaced by Overvotes row '
                         f'({s})', None))
            pages = sorted(blk['pages'])
            if not rows:
                failures.append((key, pages, 'no rows', None, total))
                continue
            # split rows; per-row breakdown gate
            present = {}
            wi_rows = []
            for r in rows:
                if r['candidate'] == 'Write Ins':
                    wi_rows.append(r)
                    continue
                try:
                    bd_ok = (int(r['election_day'] or 0) +
                             int(r['mail'] or 0) +
                             int(r['provisional'] or 0)) == int(r['votes'])
                except ValueError:
                    bd_ok = False
                dkey = f'{office}|{dist}|{party}|{r["candidate"]}'
                present[dkey] = {
                    'votes': int(r['votes']),
                    'bd': (r['election_day'], r['mail'], r['provisional'])
                    if bd_ok else None,
                }
            dos_map = {k.split('|')[3]: v for k, v in dos.get(prec, {}).items()
                       if k.startswith(f'{office}|{dist}|{party}|')}
            dos_total = sum(dos_map.values())
            dos_missing = {c: v for c, v in dos_map.items()
                           if f'{office}|{dist}|{party}|{c}' not in present}
            s_named_present = sum(v['votes'] for v in present.values())
            s_wi = sum(int(r['votes']) or 0 for r in wi_rows)
            s = s_named_present + s_wi
            if office in state_offices and not dos_map:
                self.problems.append((0, prec,
                                      f'{office}|{dist}|{party}: no DOS '
                                      'rows (write-in-only contest?)', None))
            # named rows: DOS votes (fall back to PDF where DOS lacks them)
            for dkey, pv in present.items():
                cand = dkey.split('|')[3]
                votes_dos = dos_map.get(cand)
                bd = pv['bd']
                if votes_dos is None:
                    votes = pv['votes']
                    if office in state_offices:
                        self.problems.append(
                            (0, prec, f'{cand} not in DOS file: {votes} '
                             'kept from PDF', None))
                else:
                    votes = votes_dos
                    if bd is not None and pv['votes'] != votes_dos:
                        bd = None   # stale PDF breakdown for this row
                _o, _d, _p = dkey.split('|')[:3]
                row = {'county': COUNTY, 'precinct': prec, 'office': _o,
                       'district': _d, 'party': _p, 'candidate': cand,
                       'votes': str(votes), 'election_day': '', 'mail': '',
                       'provisional': ''}
                if bd:
                    row['election_day'], row['mail'], row['provisional'] = bd
                named_out[(prec, _o, _d, _p, cand)] = row
            # restore named rows lost by the OCR (the DOS file lists
            # candidates this block is missing); breakdowns derived when
            # derivable
            print_named = sum(v['votes'] for v in present.values())
            if dos_missing:
                tbd = None
                if trow is not None and all(
                        x is not None for x in (trow['election_day'],
                                                trow['mail'],
                                                trow['provisional'])):
                    tbd = (trow['election_day'], trow['mail'],
                           trow['provisional'])
                known_bd = (all(v['bd'] is not None
                                for v in present.values())
                            and all(r['election_day'].isdigit()
                                    for r in wi_rows))
                for cand, dv in sorted(dos_missing.items()):
                    bd = None
                    # single missing row: derive its breakdown from the
                    # printed Total and the other rows
                    if tbd is not None and known_bd and \
                            len(dos_missing) == 1:
                        try:
                            ed = tbd[0] - sum(
                                int(v['bd'][0]) for v in present.values()
                                if v['bd']) - sum(
                                    int(r['election_day'] or 0)
                                    for r in wi_rows)
                            mi = tbd[1] - sum(
                                int(v['bd'][1]) for v in present.values()
                                if v['bd']) - sum(
                                    int(r['mail'] or 0) for r in wi_rows)
                            pr = tbd[2] - sum(
                                int(v['bd'][2]) for v in present.values()
                                if v['bd']) - sum(
                                    int(r['provisional'] or 0)
                                    for r in wi_rows)
                        except ValueError:
                            ed = mi = pr = -1
                        if ed + mi + pr == dv and min(ed, mi, pr) >= 0:
                            bd = (str(ed), str(mi), str(pr))
                    row = {'county': COUNTY, 'precinct': prec,
                           'office': office, 'district': dist,
                           'party': party, 'candidate': cand,
                           'votes': str(dv),
                           'election_day': bd[0] if bd else '',
                           'mail': bd[1] if bd else '',
                           'provisional': bd[2] if bd else ''}
                    named_out[(prec, office, dist, party, cand)] = row
                    self.problems.append(
                        (self.page, prec,
                         f'row restored from DOS: {cand} {dv} bd={bd}',
                         None))
                    present[f'{office}|{dist}|{party}|{cand}'] = {
                        'votes': dv, 'bd': bd}
                print_named += sum(dos_missing.values())
                dos_missing = {}
            if trow is None:
                pass   # keep parsed rows (no total check possible)
            else:
                # write-in votes = printed Total - printed named rows
                want_wi = total - print_named
                if want_wi >= 0:
                    votes = str(want_wi)
                    try:
                        want_bd = (trow['election_day'], trow['mail'],
                                   trow['provisional'])
                    except TypeError:
                        want_bd = (None, None, None)
                    if all(x is not None for x in want_bd) and \
                            all(v['bd'] is not None for v in
                                present.values()):
                        try:
                            bd_want = tuple(
                                want_bd[i] - sum(
                                    int(v['bd'][i]) for v in
                                    present.values())
                                for i in range(3))
                            if sum(bd_want) == want_wi and min(bd_want) >= 0:
                                bd = tuple(str(x) for x in bd_want)
                            else:
                                bd = None
                        except ValueError:
                            bd = None
                    else:
                        bd = None
                    if wi_rows:
                        if int(wi_rows[0]['votes']) != want_wi:
                            self.problems.append(
                                (self.page, prec,
                                 f'write-in set to printed-total derived '
                                 f'{want_wi} (OCR row had '
                                 f"{wi_rows[0]['votes']})", None))
                        for r in wi_rows[1:]:
                            pass
                        wi_rows = [{
                            'county': COUNTY, 'precinct': prec,
                            'office': office, 'district': dist,
                            'party': party, 'candidate': 'Write Ins',
                            'votes': votes,
                            'election_day': bd[0] if bd else '',
                            'mail': bd[1] if bd else '',
                            'provisional': bd[2] if bd else ''}]
                    elif want_wi > 0:
                        self.problems.append(
                            (self.page, prec,
                             f'write-in derived as {want_wi} '
                             '(missing row; no breakdown)', None))
                        wi_rows = [{'county': COUNTY, 'precinct': prec,
                                    'office': office, 'district': dist,
                                    'party': party, 'candidate': 'Write Ins',
                                    'votes': str(want_wi), 'election_day':
                                    '', 'mail': '', 'provisional': ''}]
                elif want_wi == 0 and all(
                        v['bd'] is not None for v in present.values()) and \
                        not dos_missing:
                    # rows complete and matching the printed total; the
                    # OCR'd write-in row is off
                    if s_wi != 0:
                        self.problems.append(
                            (self.page, prec,
                             f'write-in set to 0 (OCR row had {s_wi})',
                             None))
                        wi_rows = [dict(wi_rows[0], votes='0',
                                        election_day='', mail='',
                                        provisional='')]
                else:
                    failures.append((key, pages,
                                     f'garbage Total: rows '
                                     f'{print_named + s_wi} vs printed total '
                                     f'{total} (rows kept)', rows, total))
            # merged write-in rows (Vote-for-2 contests print two lines)
            if wi_rows:
                votes = sum(int(r['votes']) or 0 for r in wi_rows)
                try:
                    bd = (str(sum(int(r['election_day'] or 0)
                                  for r in wi_rows)),
                          str(sum(int(r['mail'] or 0) for r in wi_rows)),
                          str(sum(int(r['provisional'] or 0)
                                  for r in wi_rows)))
                except ValueError:
                    bd = ('', '', '')
                other_out.append({
                    'county': COUNTY, 'precinct': prec, 'office': office,
                    'district': dist, 'party': party,
                    'candidate': 'Write Ins', 'votes': str(votes),
                    'election_day': bd[0], 'mail': bd[1],
                    'provisional': bd[2]})
        # corrections for blocks that never parsed at all
        for key, corr in CORRECTIONS.items():
            if key in self.emitted:
                continue
            for r in corr['rows']:
                if r['candidate'] == 'Write Ins':
                    other_out.append(r)
                else:
                    _k = (r['precinct'], r['office'], r['district'],
                          r['party'], r['candidate'])
                    named_out.setdefault(_k, r)
        # RV/BC per party (DEM first), then contest rows in ballot order
        out = []
        order_of = {k: i for i, k in enumerate(CONTEST_ORDER)}
        per_prec = {}
        for row in sorted(named_out.values(), key=lambda r: (
                r['precinct'],
                order_of.get((r['office'], r['district'], r['party']), 99),
                r['candidate'])):
            per_prec.setdefault(row['precinct'], []).append(
                (order_of.get((row['office'], row['district'],
                               row['party']), 99), row['candidate'], row))
        for row in other_out:
            per_prec.setdefault(row['precinct'], []).append(
                (order_of.get((row['office'], row['district'],
                               row['party']), 99), row['candidate'], row))
        for prec in sorted(per_prec):
            d = dict(self.seen_rv.get(prec, {}))
            for party, vals in BALLOTS_FIX.get(prec, {}).items():
                if party not in d:
                    d[party] = vals
            for party in ('DEM', 'REP'):
                if party in d:
                    ballots, reg = d[party]
                    for office_rv, votes in (('Registered Voters', reg),
                                             ('Ballots Cast', ballots)):
                        out.append({
                            'county': self.county, 'precinct': prec,
                            'office': office_rv, 'district': '',
                            'party': party, 'candidate': '',
                            'votes': str(votes), 'election_day': '',
                            'mail': '', 'provisional': '',
                        })
            for oi, cand, row in sorted(per_prec[prec]):
                out.append(row)
        return out, failures, self.problems


def main():
    if len(sys.argv) != 4:
        print(__doc__)
        sys.exit(1)
    import json
    ocr_path, out_path, dos_path = sys.argv[1], sys.argv[2], sys.argv[3]
    with open(ocr_path, encoding='utf-8') as f:
        text = f.read()
    with open(dos_path) as f:
        dos = json.load(f)
    p = Parser(COUNTY)
    p.feed(text)
    final, failures, problems = p.build(dos)
    fieldnames = ['county', 'precinct', 'office', 'district', 'party',
                  'candidate', 'votes', 'election_day', 'mail', 'provisional']
    with open(out_path, 'w', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(final)
    print(f'wrote {len(final)} rows to {out_path}')
    print(f'validation failures: {len(failures)}')
    for key, pages, msg, rows, total in failures:
        print(f'  {key} pages={pages}: {msg}')
    from collections import Counter
    c = Counter((pr[2] if len(pr) > 2 and isinstance(pr[2], str) else 'other')
                for pr in problems)
    print(f'problem rows: {len(problems)}')
    for msg, n in c.most_common():
        print(f'  {n}x {msg}')


if __name__ == '__main__':
    main()
