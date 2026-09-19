#!/usr/bin/env python3
"""
Wayne County, PA 2022 General Election parser (precinct-level).

Source: "Wayne PA Nov2022GenElection Final by Precinct.pdf" -- a Dominion
"Statement of Votes Cast by Geography" report (same family as the 2023/2025
Wayne generals, two-line contest headers). The 2022 PDF is a SCAN with no
text layer, so the text must first be produced with the hosted PaddleOCR API:

    /tmp/paddleenv/bin/python work2023p/ocr_api.py \
        "<source>.pdf" work2022g/wayne2022_ocr.txt

This parser consumes that "===== PAGE N =====" markdown/HTML output, not the
PDF itself. PaddleOCR-VL renders the report's tables as HTML <table> blocks;
occasional OCR scrambling (dropped/merged name cells) is caught by an internal
validation pass comparing each contest's candidate sum against the report's
own printed "Total" row.

Contest identification is done by CANDIDATE NAME (each 2022 Wayne contest has
a distinct candidate set), not by the office header -- PaddleOCR sometimes
drops the header line entirely. Headers are still used to seed the fixed
within-precinct contest order (CONTEST_ORDER) as a fallback, and for the
registered-voters/ballots-cast rows.

Precinct names are normalized to full municipality names where the Dominion
report itself truncates them (the report prints "CHE RIDGE TOWNSHIP",
"MOU PLE TOWNSHIP", "SOU CANAAN TOWNSHIP"); the normalized forms match the
county's 2023 precinct file.

Tables that fail validation are listed at the end of the run with their page
number so they can be transcribed from page images into CORRECTIONS below.

Usage:
    python pa_wayne_general_2022_results_parser.py <ocr_text_file> <output_csv>
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

PRECINCT_RE = re.compile(r'^\s*(?:#+\s*)?\.?\s*Precinct\s+(.+?)\s*$')
COUNTYWIDE_RE = re.compile(r'^\s*(?:#+\s*)?All Precincts\s*$')
CONTEST_RE = re.compile(r'^\s*(?:#+\s*)?(.{2,120}?)\s*\(Vote for (\d+)\)\s*$')
REGISTERED_RE = re.compile(r'([\d,]+)\s*registered')
BALLOTS_RE = re.compile(r'(\d[\d,]*)\s*ballots')
PAGE_HEADER_SKIPS = (
    'Statement of Votes Cast by Geography',
    'Wayne County, NOVEMBER',
    'All Precincts, All Districts',
    'Total Ballots Cast:',
    'precincts reported out of',
)
def is_meta_name(name):
    n = name.lower()
    if n in ('choice', 'total'):
        return True
    return n.startswith('over') or n.startswith('under')

# Dominion report truncates some precinct names; full names (as used in the
# county's 2023 precinct file) replace them.
PRECINCT_FIXES = {
    'CHE RIDGE TOWNSHIP': 'CHERRY RIDGE TOWNSHIP',
    'MOU PLE TOWNSHIP': 'MOUNT PLEASANT TOWNSHIP',
    'SOU CANAAN TOWNSHIP': 'SOUTH CANAAN TOWNSHIP',
}

# candidate name (as printed, lowercased) -> (office, district, party)
CANDIDATES = {
    'john fetterman': ('U.S. Senate', '', 'DEM'),
    'mehmet oz': ('U.S. Senate', '', 'REP'),
    'erik gerhardt': ('U.S. Senate', '', 'LIB'),
    'richard l. weiss': ('U.S. Senate', '', 'GRN'),
    'daniel wassmer': ('U.S. Senate', '', 'KEY'),
    'josh shapiro': ('Governor', '', 'DEM'),
    'douglas v. mastriano': ('Governor', '', 'REP'),
    'matt hackenburg': ('Governor', '', 'LIB'),
    'christina digiulio': ('Governor', '', 'GRN'),
    'joe soloski': ('Governor', '', 'KEY'),
    'matt cartwright': ('U.S. House', '8', 'DEM'),
    'jim bognet': ('U.S. House', '8', 'REP'),
    'jackie baker': ('State Senate', '20', 'DEM'),
    'lisa baker': ('State Senate', '20', 'REP'),
    'jennifer shukaitis': ('State Senate', '40', 'DEM'),
    'rosemary brown': ('State Senate', '40', 'REP'),
    'rosemary maula brown': ('State Senate', '40', 'REP'),
    'jonathan fritz': ('State House', '111', 'REP'),
    'meg rosenfeld': ('State House', '139', 'DEM'),
    'joseph w. adams': ('State House', '139', 'REP'),
    # OCR misspellings observed in the scan
    'erlk gerhardt': ('U.S. Senate', '', 'LIB'),
    'joe sotoski': ('Governor', '', 'KEY'),
    'christina diglulio': ('Governor', '', 'GRN'),
    'jennifer shukaltis': ('State Senate', '40', 'DEM'),
}

# Precincts whose "N ballots (...), M registered voters" line the OCR garbled
# or dropped entirely; (ballots cast, registered voters) read from the page
# images.
BALLOTS_FIX = {
    'BERLIN TOWNSHIP #1': (591, 908),
    'DYBERRY TOWNSHIP': (746, 1049),
    'CHERRY RIDGE TOWNSHIP': (960, 1361),
    'PRESTON TOWNSHIP': (501, 765),
    'PAUPACK TOWNSHIP': (1900, 3001),
}

# OCR-garbled candidate spellings -> correct name (as printed for that
# candidate elsewhere in the report)
NAME_FIX = {
    'erlk gerhardt': 'Erik Gerhardt',
    'joe sotoski': 'Joe Soloski',
    'christina diglulio': 'Christina DiGiulio',
    'jennifer shukaltis': 'Jennifer Shukaitis',
}

# Fixed contest order within each precinct (fallback when a table's candidate
# rows carry no recognizable name).
CONTEST_ORDER = ['U.S. Senate', 'Governor', 'U.S. House',
                 'State Senate', 'State House']

# (precinct, office) -> {'rows': [...], 'total': int}, transcribed from page
# images where PaddleOCR scrambled/merged the table (values verified against
# the printed Total row and the "N ballots (...)" line). Blocks present only
# here (not parsed at all) are emitted as-is.
CORRECTIONS = {}


def C(precinct, office, district, total, *rows):
    def row(candidate, party, votes, ed, mi, pr):
        return {
            'county': COUNTY, 'precinct': precinct, 'office': office,
            'district': district, 'party': party, 'candidate': candidate,
            'votes': str(votes), 'election_day': str(ed),
            'early_voting': str(mi), 'provisional': str(pr),
        }
    CORRECTIONS[(precinct, office)] = {
        'rows': [row(*r) for r in rows], 'total': total,
    }


# BERLIN TOWNSHIP #2, State Senate 20 -- p.2 (OCR swapped name/value cells)
C('BERLIN TOWNSHIP #2', 'State Senate', '20', 627,
  ('Jackie Baker', 'DEM', 100, 64, 36, 0),
  ('Lisa Baker', 'REP', 526, 450, 76, 0),
  ('Write Ins', '', 1, 1, 0, 0))
# CANAAN TOWNSHIP, U.S. House 8 -- p.4 (OCR merged all rows into one)
C('CANAAN TOWNSHIP', 'U.S. House', '8', 431,
  ('Matt Cartwright', 'DEM', 131, 70, 60, 1),
  ('Jim Bognet', 'REP', 298, 283, 15, 0),
  ('Write Ins', '', 2, 2, 0, 0))
# CLINTON TOWNSHIP #1, U.S. Senate -- p.6 (votes column garbled)
C('CLINTON TOWNSHIP #1', 'U.S. Senate', '', 647,
  ('John Fetterman', 'DEM', 208, 130, 78, 0),
  ('Mehmet Oz', 'REP', 424, 388, 36, 0),
  ('Erik Gerhardt', 'LIB', 8, 7, 1, 0),
  ('Richard L. Weiss', 'GRN', 2, 2, 0, 0),
  ('Daniel Wassmer', 'KEY', 4, 2, 2, 0),
  ('Write Ins', '', 1, 1, 0, 0))
# CLINTON TOWNSHIP #1, State House 111 -- p.6 (rows merged)
C('CLINTON TOWNSHIP #1', 'State House', '111', 571,
  ('Jonathan Fritz', 'REP', 551, 476, 75, 0),
  ('Write Ins', '', 20, 12, 8, 0))
# CLINTON TOWNSHIP #2, U.S. Senate -- p.6-7 (Fetterman/Oz rows lost)
C('CLINTON TOWNSHIP #2', 'U.S. Senate', '', 389,
  ('John Fetterman', 'DEM', 150, 111, 39, 0),
  ('Mehmet Oz', 'REP', 230, 207, 23, 0),
  ('Erik Gerhardt', 'LIB', 3, 1, 2, 0),
  ('Richard L. Weiss', 'GRN', 3, 2, 1, 0),
  ('Daniel Wassmer', 'KEY', 2, 2, 0, 0),
  ('Write Ins', '', 1, 1, 0, 0))
# HAWLEY BOROUGH, State Senate 20 -- p.11 (rows merged)
C('HAWLEY BOROUGH', 'State Senate', '20', 468,
  ('Jackie Baker', 'DEM', 178, 110, 68, 0),
  ('Lisa Baker', 'REP', 290, 259, 30, 1),
  ('Write Ins', '', 0, 0, 0, 0))
# HAWLEY BOROUGH, State House 139 -- p.11-12 (rows merged w/ senate table)
C('HAWLEY BOROUGH', 'State House', '139', 464,
  ('Meg Rosenfeld', 'DEM', 190, 120, 70, 0),
  ('Joseph W. Adams', 'REP', 238, 211, 26, 1),
  ('Write Ins', '', 36, 36, 0, 0))
# HONESDALE BOROUGH #1, State House 111 -- p.12 (rows merged)
C('HONESDALE BOROUGH #1', 'State House', '111', 398,
  ('Jonathan Fritz', 'REP', 372, 308, 63, 1),
  ('Write Ins', '', 26, 14, 12, 0))
# HONESDALE BOROUGH #2, U.S. Senate -- p.12-13 (Fetterman/Oz rows lost)
C('HONESDALE BOROUGH #2', 'U.S. Senate', '', 630,
  ('John Fetterman', 'DEM', 308, 187, 120, 1),
  ('Mehmet Oz', 'REP', 293, 266, 27, 0),
  ('Erik Gerhardt', 'LIB', 17, 12, 5, 0),
  ('Richard L. Weiss', 'GRN', 4, 3, 1, 0),
  ('Daniel Wassmer', 'KEY', 6, 6, 0, 0),
  ('Write Ins', '', 2, 1, 1, 0))
# MOUNT PLEASANT TOWNSHIP, State House 111 -- p.18 (rows merged)
C('MOUNT PLEASANT TOWNSHIP', 'State House', '111', 640,
  ('Jonathan Fritz', 'REP', 626, 519, 106, 1),
  ('Write Ins', '', 14, 7, 7, 0))
# SCOTT TOWNSHIP, State House 111 -- p.24 (rows merged)
C('SCOTT TOWNSHIP', 'State House', '111', 175,
  ('Jonathan Fritz', 'REP', 170, 151, 19, 0),
  ('Write Ins', '', 5, 2, 3, 0))
# SOUTH CANAAN TOWNSHIP, U.S. Senate -- p.24-25 (Fetterman/Oz rows lost)
C('SOUTH CANAAN TOWNSHIP', 'U.S. Senate', '', 840,
  ('John Fetterman', 'DEM', 234, 142, 91, 1),
  ('Mehmet Oz', 'REP', 577, 540, 37, 0),
  ('Erik Gerhardt', 'LIB', 17, 16, 1, 0),
  ('Richard L. Weiss', 'GRN', 5, 4, 1, 0),
  ('Daniel Wassmer', 'KEY', 6, 5, 1, 0),
  ('Write Ins', '', 1, 1, 0, 0))
# CANAAN TOWNSHIP, State Senate 40 -- p.4-5 (House total row misfiled onto
# this block by the parser; printed total is 429)
C('CANAAN TOWNSHIP', 'State Senate', '40', 429,
  ('Jennifer Shukaitis', 'DEM', 112, 63, 48, 1),
  ('Rosemary Brown', 'REP', 317, 288, 29, 0),
  ('Write Ins', '', 0, 0, 0, 0))
# TEXAS TOWNSHIP #2, State Senate 40 -- p.28 (PR column lost at page edge)
C('TEXAS TOWNSHIP #2', 'State Senate', '40', 361,
  ('Jennifer Shukaitis', 'DEM', 115, 77, 37, 1),
  ('Rosemary Brown', 'REP', 245, 214, 31, 0),
  ('Write Ins', '', 1, 1, 0, 0))
# PALMYRA TOWNSHIP, Governor -- p.20 (PR column lost at page edge; Shapiro
# PR is 1, all others 0; verified against printed Total 604, 487/116/1)
C('PALMYRA TOWNSHIP', 'Governor', '', 604,
  ('Josh Shapiro', 'DEM', 198, 117, 80, 1),
  ('Douglas V. Mastriano', 'REP', 394, 359, 35, 0),
  ('Matt Hackenburg', 'LIB', 8, 7, 1, 0),
  ('Christina DiGiulio', 'GRN', 2, 2, 0, 0),
  ('Joe Soloski', 'KEY', 2, 2, 0, 0),
  ('Write Ins', '', 0, 0, 0, 0))
# PROMPTON BOROUGH, State Senate 40 -- p.22 (PR column lost at page edge; all
# PR values are 0, verified against the page image -- no correction needed)
# TEXAS TOWNSHIP #2, U.S. House 8 -- p.28 (rows merged)
C('TEXAS TOWNSHIP #2', 'U.S. House', '8', 365,
  ('Matt Cartwright', 'DEM', 142, 98, 43, 1),
  ('Jim Bognet', 'REP', 217, 192, 25, 0),
  ('Write Ins', '', 6, 6, 0, 0))
# WAYMART BOROUGH, State House 111 -- p.30 (rows merged)
C('WAYMART BOROUGH', 'State House', '111', 443,
  ('Jonathan Fritz', 'REP', 433, 395, 38, 0),
  ('Write Ins', '', 10, 8, 2, 0))
# BUCKINGHAM TOWNSHIP, U.S. House 8 -- p.3-4 (Write-in row separated onto the
# next page by the page break and misfiled by the parser; verified against the
# printed Total 256, 198/58/0)
C('BUCKINGHAM TOWNSHIP', 'U.S. House', '8', 256,
  ('Matt Cartwright', 'DEM', 103, 64, 39, 0),
  ('Jim Bognet', 'REP', 153, 134, 19, 0),
  ('Write Ins', '', 0, 0, 0, 0))
# BUCKINGHAM TOWNSHIP, State Senate 20 -- p.4 (the misfiled page-4 Write-in row
# lands in this block; corrected rows verified against printed Total 254,
# 198/56/0)
C('BUCKINGHAM TOWNSHIP', 'State Senate', '20', 254,
  ('Jackie Baker', 'DEM', 79, 44, 35, 0),
  ('Lisa Baker', 'REP', 175, 154, 21, 0),
  ('Write Ins', '', 0, 0, 0, 0))
# CHERRY RIDGE TOWNSHIP, State House 139 -- p.5-6 (candidate table OCR'd as a
# name-only stub, rows lost; verified against printed Total 940, 756/182/2)
C('CHERRY RIDGE TOWNSHIP', 'State House', '139', 940,
  ('Meg Rosenfeld', 'DEM', 289, 171, 116, 2),
  ('Joseph W. Adams', 'REP', 597, 531, 66, 0),
  ('Write Ins', '', 54, 54, 0, 0))
# HONESDALE BOROUGH #2, State Senate 40 -- p.13 (header dropped by OCR, rows
# lost; verified against printed Total 626, 473/152/1)
C('HONESDALE BOROUGH #2', 'State Senate', '40', 626,
  ('Jennifer Shukaitis', 'DEM', 291, 177, 113, 1),
  ('Rosemary Brown', 'REP', 334, 295, 39, 0),
  ('Write Ins', '', 1, 1, 0, 0))
# SALEM TOWNSHIP, State Senate 40 -- p.23 (header rendered as a div and rows
# carry an extra empty cell, so the table was dropped; verified against printed
# Total 1888, 1469/410/9)
C('SALEM TOWNSHIP', 'State Senate', '40', 1888,
  ('Jennifer Shukaitis', 'DEM', 569, 316, 246, 7),
  ('Rosemary Brown', 'REP', 1313, 1148, 163, 2),
  ('Write Ins', '', 6, 5, 1, 0))
# SOUTH CANAAN TOWNSHIP, U.S. House 8 -- p.25 (all rows merged into one rowspan
# cell; verified against printed Total 831, 698/132/1)
C('SOUTH CANAAN TOWNSHIP', 'U.S. House', '8', 831,
  ('Matt Cartwright', 'DEM', 244, 149, 94, 1),
  ('Jim Bognet', 'REP', 585, 547, 38, 0),
  ('Write Ins', '', 2, 2, 0, 0))


def strip_tags(s):
    return html.unescape(TAG_RE.sub('', s)).strip()


def page_lines(text):
    """Yield (page_no, kind, content) events from the OCR markdown.

    kind: 'line' for plain/markdown lines, 'row' for a list of table cells.
    Table rows of one <table> are yielded consecutively.
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


class Parser:
    def __init__(self, county=COUNTY):
        self.county = county
        self.results = []
        self.problems = []
        self.blocks = {}       # (precinct, office) -> {'rows', 'total', 'pages'}
        self.block_order = []
        self.precinct = None
        self.page = 0
        self.countywide = False
        self.seen_rv = set()
        self.contest_idx = {}  # precinct -> last index into CONTEST_ORDER
        # pending table state
        self.pending_rows = []
        self.pending_total = None
        self.pending_pages = set()

    # -------------------------------------------------- helpers
    def guess_office(self):
        idx = self.contest_idx.get(self.precinct, -1)
        if idx + 1 < len(CONTEST_ORDER):
            return CONTEST_ORDER[idx + 1]
        return None

    def new_block(self, office):
        key = (self.precinct, office)
        if key not in self.blocks:
            self.blocks[key] = {'rows': [], 'total': None, 'total_row': None, 'pages': set()}
            self.block_order.append(key)
        return self.blocks[key]

    def add_rv_bc(self, ballots, reg):
        if self.precinct in self.seen_rv:
            return
        self.seen_rv.add(self.precinct)
        if self.precinct in BALLOTS_FIX:
            ballots, reg = BALLOTS_FIX[self.precinct]
            ballots, reg = str(ballots), str(reg)
        for office, votes in (('Registered Voters', reg),
                              ('Ballots Cast', ballots)):
            self.results.append({
                'county': self.county, 'precinct': self.precinct,
                'office': office, 'district': '', 'party': '',
                'candidate': '', 'votes': votes,
                'election_day': '', 'early_voting': '', 'provisional': '',
            })

    def handle_precinct(self, name):
        name = name.upper().strip()
        self.precinct = PRECINCT_FIXES.get(name, name)
        self.countywide = False
        self.contest_idx[self.precinct] = -1

    def flush_table(self):
        """Assign the buffered table rows to a contest block."""
        if not self.pending_rows and self.pending_total is None:
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
        offices = set()
        districts = set()
        for r in self.pending_rows:
            hit = CANDIDATES.get(r['candidate'].lower())
            if hit:
                offices.add(hit[0])
                if hit[1]:
                    districts.add(hit[1])
        district = districts.pop() if len(districts) == 1 else ''
        if len(offices) == 1:
            office = offices.pop()
        elif len(offices) > 1:
            # merged/garbled table: flag for manual review
            self.problems.append(
                (self.page, self.precinct, 'mixed offices', self.pending_rows))
            office = sorted(offices)[0]
        else:
            office = self.guess_office()
        if office is None:
            self.problems.append(
                (self.page, self.precinct, 'dropped rows, no office',
                 self.pending_rows))
            self.pending_rows = []
            self.pending_total = None
            self.pending_pages = set()
            return
        blk = self.new_block(office)
        blk['pages'] |= self.pending_pages
        if self.pending_total is not None:
            blk['total'] = self.pending_total['votes']
            blk['total_row'] = self.pending_total
        for r in self.pending_rows:
            hit = CANDIDATES.get(r['candidate'].lower())
            if hit:
                r['office'], r['district'], r['party'] = hit
            elif r['candidate'] == 'Write Ins':
                r['office'] = office
                r['district'] = district
            else:
                # unrecognized name from a scrambled table: drop it (the
                # CORRECTIONS table supplies the real rows)
                self.problems.append(
                    (self.page, self.precinct, 'dropped unknown candidate', r))
                continue
            blk['rows'].append(r)
        self.pending_rows = []
        self.pending_total = None
        self.pending_pages = set()

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

    def handle_line(self, line):
        if any(s in line for s in PAGE_HEADER_SKIPS):
            return
        if COUNTYWIDE_RE.match(line):
            self.flush_table()
            self.countywide = True
            self.precinct = None
            return
        pm = PRECINCT_RE.match(line)
        if pm:
            self.flush_table()
            self.handle_precinct(pm.group(1))
            return
        cm = CONTEST_RE.match(line)
        if cm:
            self.flush_table()
            office = infer_office_from_header(cm.group(1))
            if office and self.precinct:
                self.contest_idx[self.precinct] = CONTEST_ORDER.index(office)
            return
        if ('registered' in line or 'ballots' in line) and not self.countywide:
            bm_r = REGISTERED_RE.search(line)
            if bm_r and self.precinct:
                reg = bm_r.group(1).replace(',', '')
                bm_b = BALLOTS_RE.search(line)
                ballots = bm_b.group(1).replace(',', '') if bm_b else ''
                self.add_rv_bc(ballots, reg)

    def handle_row(self, cells):
        if not cells or self.countywide:
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
        cm = CONTEST_RE.match(flat)
        if cm and len(cells) <= 2:
            self.flush_table()
            office = infer_office_from_header(cm.group(1))
            if office and self.precinct:
                self.contest_idx[self.precinct] = CONTEST_ORDER.index(office)
            return
        if 'registered' in flat and 'choice' not in flat.lower():
            self.flush_table()
            bm_r = REGISTERED_RE.search(flat)
            if bm_r and self.precinct:
                reg = bm_r.group(1).replace(',', '')
                bm_b = BALLOTS_RE.search(flat)
                ballots = bm_b.group(1).replace(',', '') if bm_b else ''
                self.add_rv_bc(ballots, reg)
            return
        if self.precinct is None or len(cells) not in (5, 6):
            return
        name = cells[0].strip().replace('\n', ' ').strip()
        if is_meta_name(name):
            if name.lower() == 'total':
                self.pending_total = {
                    'votes': self._int(cells[1]),
                    'ed': self._int(cells[3]) if len(cells) > 3 else None,
                    'mi': self._int(cells[4]) if len(cells) > 4 else None,
                    'pr': self._int(cells[5]) if len(cells) > 5 else None,
                }
            return
        if not name or not cells[1].strip().isdigit():
            self.problems.append((self.page, self.precinct, 'bad row', cells))
            return
        low = name.lower()
        if low.startswith('write-in') or low.startswith('write in'):
            candidate = 'Write Ins'
        else:
            candidate = NAME_FIX.get(low, name)
        if len(cells) == 5:
            # PR column lost at the page edge in the OCR -- padded with 0 and
            # flagged so the value can be verified against the page image
            self.problems.append(
                (self.page, self.precinct, '5-cell row, PR padded 0', cells))
            cells = list(cells) + ['0']
        self.pending_pages.add(self.page)
        self.pending_rows.append({
            'county': self.county, 'precinct': self.precinct,
            'office': '', 'district': '', 'party': '',
            'candidate': candidate, 'votes': cells[1].strip().replace(',', ''),
            'election_day': cells[3].strip().replace(',', ''),
            'early_voting': cells[4].strip().replace(',', ''),
            'provisional': cells[5].strip().replace(',', ''),
        })

    @staticmethod
    def _int(v):
        try:
            return int(v.replace(',', ''))
        except ValueError:
            return None

    # -------------------------------------------------- output
    def emit(self):
        failures = []
        emitted_keys = set()
        for key in self.block_order:
            emitted_keys.add(key)
            blk = self.blocks[key]
            corr = CORRECTIONS.get(key)
            rows = corr['rows'] if corr else blk['rows']
            total = corr['total'] if corr and corr.get('total') is not None \
                else blk['total']
            pages = sorted(blk['pages'])
            if not rows:
                failures.append((key, pages, 'no rows', None, total))
                continue
            s = sum(self._int(r['votes']) or 0 for r in rows)
            if total is not None and s != total:
                failures.append((key, pages, f'sum {s} != printed total {total}',
                                 rows, total))
            trow = corr.get('total_row') if corr else blk.get('total_row')
            if trow:
                for col, label in (('election_day', 'ED'), ('early_voting', 'MI'),
                                   ('provisional', 'PR')):
                    want, got = trow[label.lower()], None
                    try:
                        got = sum(int(r[col] or 0) for r in rows)
                    except ValueError:
                        continue
                    if want is not None and got != want:
                        failures.append((key, pages,
                                         f'{label} sum {got} != printed {want}',
                                         rows, total))
            self.results.extend(rows)
        # RV/BC rows for precincts whose meta line the OCR dropped entirely
        for prec, (ballots, reg) in BALLOTS_FIX.items():
            if prec in self.seen_rv:
                continue
            idx = next((i for i, r in enumerate(self.results)
                        if r['precinct'] == prec), len(self.results))
            for office, votes in (('Registered Voters', str(reg)),
                                  ('Ballots Cast', str(ballots))):
                self.results.insert(idx, {
                    'county': self.county, 'precinct': prec,
                    'office': office, 'district': '', 'party': '',
                    'candidate': '', 'votes': votes,
                    'election_day': '', 'early_voting': '', 'provisional': '',
                })
                idx += 1
        # corrections for blocks that never parsed at all
        for key, corr in CORRECTIONS.items():
            if key in emitted_keys:
                continue
            s = sum(self._int(r['votes']) or 0 for r in corr['rows'])
            if corr.get('total') is not None and s != corr['total']:
                failures.append((key, [], f'corrected sum {s} != printed {corr["total"]}',
                                 corr['rows'], corr['total']))
            self.results.extend(corr['rows'])
        # RV/BC rows were appended as encountered (interleaved with blocks in
        # precinct order); emit blocks after them.
        return self.results, failures, self.problems


def infer_office_from_header(text):
    t = text.upper()
    if 'UNITED STATES SENATOR' in t:
        return 'U.S. Senate'
    if 'GOVERNOR' in t:
        return 'Governor'
    if 'CONGRESS' in t:
        return 'U.S. House'
    if 'SENATOR IN THE GENERAL ASSEMBLY' in t:
        return 'State Senate'
    if 'REPRESENTATIVE IN THE GENERAL ASSEMBLY' in t:
        return 'State House'
    return None


def parse(text, county=COUNTY):
    p = Parser(county)
    p.feed(text)
    return p.emit()


def main():
    if len(sys.argv) != 3:
        print(__doc__)
        sys.exit(1)
    ocr_path, out_path = sys.argv[1], sys.argv[2]
    with open(ocr_path, encoding='utf-8') as f:
        text = f.read()
    results, failures, problems = parse(text)
    fieldnames = ['county', 'precinct', 'office', 'district', 'party',
                  'candidate', 'votes', 'election_day', 'early_voting',
                  'provisional']
    with open(out_path, 'w', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(results)
    print(f'wrote {len(results)} rows to {out_path}')
    print(f'validation failures: {len(failures)}')
    for key, pages, msg, rows, total in failures:
        print(f'  {key} pages={pages}: {msg}')
        if rows:
            for r in rows:
                print(f"     {r['candidate']!r} {r['votes']} (ED {r['election_day']} "
                      f"MI {r['early_voting']} PR {r['provisional']})")
    print(f'problem rows: {len(problems)}')
    for item in problems:
        print('  ', item)


if __name__ == '__main__':
    main()