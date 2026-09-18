#!/usr/bin/env python3
"""
Wayne County, PA 2023 Municipal Primary parser.

Source: Dominion "Statement of Votes Cast by Geography" reports
(same family as pa_wayne_general_2023_results_parser.py), fed as PaddleOCR-VL
markdown text (the PDFs are scanned; pdftotext yields nothing). OCR layout:

  - Contest headers as centered <div>s or "## " lines or a colspan=6 table row:
        JUSTICE OF THE SUPREME COURT (DEMOCR) (Vote for 1)
    The party token is OCR-truncated ("DEMOCR"/"REPUBL"; occasionally full
    DEMOCRAT/REPUBLICAN) -> DEM / REP.
  - Ballots line as a centered <div> or colspan=6 row:
        2496 ballots (10 over voted ballots, 10 overvotes, 287 undervotes),
        9171 registered voters, turnout 27.22%
  - Candidate data as one <table> per contest with 6-column rows:
        name | total | vote % | ED | MI | PR
    followed by Total / Overvotes / Undervotes rows. Multiple "Write-in" rows
    occur in Vote-for-2/3 contests and are summed into one "Write-ins" row.
  - Some whole contest blocks appear as a single <table> whose first rows are
    colspan=6 header/ballots cells.

Two report variants:
  county summary / by-office  -> "All Precincts" only -> county-level CSV (9 cols)
  precinct results            -> "Precinct <NAME>" sections -> precinct CSV (10 cols)
Mode is auto-detected from "Precinct " lines; --county/--precinct override.

Party conventions (2023 primary, repo-wide): every candidate/Write-in/
Undervotes/Overvotes row carries the contest header's party; metadata rows
(Registered Voters / Ballots Cast) stay party-empty. Office/district
normalization mirrors pa_wayne_general_2023_results_parser.normalize_office.

Usage: python pa_wayne_primary_2023_results_parser.py <input_txt> <output_csv> [--county|--precinct]
"""

import html
import re
import sys
from pathlib import Path

COUNTY = 'Wayne'

CONTEST_RE = re.compile(
    r'^(?P<title>.{2,200}?)\s*\((?P<party>DEMOCR[A-Z]*|REPUBL[A-Z]*|REPUBLIC[A-Z]*|DEM\b)\)\s*'
    r'\(Vote for (?P<vote_for>\d+)\)$',
    re.IGNORECASE,
)
# Same, but without anchors (for embedded/concatenated headers in OCR text)
CONTEST_ANY_RE = re.compile(
    r'(?P<title>.{2,200}?)\s*\((?P<party>DEMOCR[A-Z]*|REPUBL[A-Z]*|REPUBLIC[A-Z]*|DEM\b)\)\s*'
    r'\(Vote for (?P<vote_for>\d+)\)',
    re.IGNORECASE,
)
BALLOTS_RE = re.compile(
    r'^(?P<ballots>\d[\d,]*)\s+ballots?\s*\(.*?\),\s*'
    r'(?P<rv>\d[\d,]*)\s+registered voters',
    re.IGNORECASE,
)
# OCR-garbled ballots lines: 'baliots', 'overVotes/underVotes', 'registered
# Votes', 'without' for 'turnout', '.' for ',' (even an unclosed paren).  Only
# the two counts are load-bearing (ballots, registered voters); the rest of
# the line is punctuation noise.
BALLOTS_LOOSE_RE = re.compile(
    r'(?P<ballots>\d[\d,]*)\s*bal\w{0,6}\b.*?\s(?P<rv>\d[\d,]*)\s*reg\w*\s*v\w{2,7}',
    re.IGNORECASE,
)
# tail of a ballots line whose head was cut at a page break:
# 's, 164 undervotes), 779 registered voters, turnout 27.47%'
BALLOTS_TAIL_RE = re.compile(
    r'(?P<rv>\d[\d,]*)\s*reg\w*\s*v\w{2,7}', re.IGNORECASE)
# last-resort ballots line: only the ballots count survived OCR
BALLOTS_MIN_RE = re.compile(r'(?P<ballots>\d[\d,]*)\s*bal\w{0,6}\b', re.IGNORECASE)
COUNTY_STATS_RE = re.compile(
    r'Total Ballots Cast:\s*(?P<bc>\d[\d,]+).*?Registered Voters:\s*(?P<rv>\d[\d,]+)',
    re.IGNORECASE,
)
PRECINCT_RE = re.compile(r'^Precinct\s+(.+)$')
PCT_RE = re.compile(r'^\d+(?:\.\d+)?%$')

SKIP_PREFIXES = (
    'PRIMARY, May',
    'Ballots Cast:',
    'All Precincts. All Districts',
    'Statement of Votes Cast',
    'Statement or Votes Cast',
    'WAYNE COUNTY,',
    'All Precincts, All Districts',
    'Total Ballots Cast',
    'precincts reported out of',
    'Page:',
    '2023-',
    'MI =',
    'ED =',
    'PR =',
)

# OCR variants of the over/undervote row labels -> normalized candidate name
OVERUNDER = {
    'overvotes': 'Overvotes', 'overtotes': 'Overvotes', 'over votes': 'Overvotes',
    'overtotes': 'Overvotes', 'oversvotes': 'Overvotes', 'oversotes': 'Overvotes',
    'avervotes': 'Overvotes', 'overtotes': 'Overvotes',
    'undervotes': 'Undervotes', 'undervottes': 'Undervotes', 'under votes': 'Undervotes',
}
OVERUNDER_ANY = (  # fuzzy substrings (cell text lowercased, spaces stripped)
    ('underv', 'Undervotes'), ('inderv', 'Undervotes'), ('ndervot', 'Undervotes'),
    ('overv', 'Overvotes'), ('overt', 'Overvotes'), ('oversv', 'Overvotes'),
    ('overso', 'Overvotes'), ('sotes', 'Overvotes'), ('averv', 'Overvotes'),
    ('averro', 'Overvotes'), ('nderv', 'Undervotes'),
)

# The 36 Wayne precincts (from the county's own pdftotext general file);
# OCR sometimes truncates the precinct header ("DAM TOWNSHIP #1") -> repaired
KNOWN_PRECINCTS = [
    'BERLIN TOWNSHIP #1', 'BERLIN TOWNSHIP #2', 'BETHANY BOROUGH',
    'BUCKINGHAM TOWNSHIP', 'CANAAN TOWNSHIP', 'CHERRY RIDGE TOWNSHIP',
    'CLINTON TOWNSHIP #1', 'CLINTON TOWNSHIP #2', 'DAMASCUS TOWNSHIP #1',
    'DAMASCUS TOWNSHIP #2', 'DREHER TOWNSHIP', 'DYBERRY TOWNSHIP',
    'HAWLEY BOROUGH', 'HONESDALE BOROUGH #1', 'HONESDALE BOROUGH #2',
    'HONESDALE BOROUGH #3', 'LAKE TOWNSHIP', 'LEBANON TOWNSHIP',
    'LEHIGH TOWNSHIP', 'MANCHESTER TOWNSHIP', 'MOUNT PLEASANT TOWNSHIP #1',
    'MOUNT PLEASANT TOWNSHIP #2-3', 'OREGON TOWNSHIP', 'PALMYRA TOWNSHIP',
    'PAUPACK TOWNSHIP', 'PRESTON TOWNSHIP', 'PROMPTON BOROUGH',
    'SALEM TOWNSHIP', 'SCOTT TOWNSHIP', 'SOUTH CANAAN TOWNSHIP',
    'STARRUCCA BOROUGH', 'STERLING TOWNSHIP', 'TEXAS TOWNSHIP #1',
    'TEXAS TOWNSHIP #2', 'TEXAS TOWNSHIP #3', 'WAYMART BOROUGH',
]

PARTY = {'DEM': 'DEM', 'DEMOCR': 'DEM', 'DEMOCRAT': 'DEM', 'DEMOCRATIC': 'DEM',
         'REP': 'REP', 'REPUBL': 'REP', 'REPUBLIC': 'REP', 'REPUBLICAN': 'REP'}

TABLE_RE = re.compile(r'<table[^>]*>.*?</table>', re.DOTALL | re.IGNORECASE)
TR_RE = re.compile(r'<tr[^>]*>(.*?)</tr>', re.IGNORECASE | re.DOTALL)
TD_RE = re.compile(r'<td[^>]*>(.*?)</td>', re.IGNORECASE | re.DOTALL)
TAG_RE = re.compile(r'<[^>]+>')


def strip_tags(s):
    s = html.unescape(TAG_RE.sub('', s)).replace('\n', ' ').strip()
    # OCR emits literal "\n" (backslash-n) for line breaks inside cells
    return s.replace('\\n', '\n').strip()


def tokens(text):
    """Turn the OCR markdown into an ordered stream of ('text', str) and
    ('row', [cells]) tokens, preserving source order."""
    out = []
    for raw in text.split('\n'):
        line = raw.strip()
        if not line or re.match(r'^=+ PAGE \d+ =+$', line):
            continue
        if re.match(r'^\$\$?', line):   # OCR LaTeX noise
            continue
        low = line.lower()
        if re.match(r'^#{1,6}$', line):   # bare LaTeX/heading noise, no content
            continue
        if line.startswith('## '):
            out.append(('text', line[3:].strip()))
            continue
        if low.startswith('<table'):
            for tr in TR_RE.findall(line):
                cells = [strip_tags(c) for c in TD_RE.findall(tr)]
                if cells:
                    out.append(('row', cells))
            continue
        div = re.match(r'^<div[^>]*>(.*)</div>$', line, re.IGNORECASE)
        if div:
            out.append(('text', strip_tags(div.group(1)).strip()))
            continue
        out.append(('text', strip_tags(line).strip()))
    return out


import difflib


def match_ballots(val):
    """Ballots line -> (ballots, rv, rest) or None; ballots or rv may be
    None when OCR garbled just that number. `rest` is any trailing text OCR
    merged into the same line (may itself contain data rows)."""
    m = BALLOTS_RE.match(val)
    if m:
        return (int(m.group('ballots').replace(',', '')),
                int(m.group('rv').replace(',', '')), '')
    m = BALLOTS_LOOSE_RE.match(val)
    if m:
        rest = val[m.end():].strip(' .,;')
        return (int(m.group('ballots').replace(',', '')),
                int(m.group('rv').replace(',', '')), rest)
    if 'bal' in val.lower() or 'underv' in val.lower():
        m = BALLOTS_TAIL_RE.search(val)
        if m and not re.search(r'(?<![\d/])\d[\d,]*\s*reg', val):
            # RV head-on-digits garbled ('7/9 registered voters'): keep only
            # an unambiguous tail RV
            return (None, int(m.group('rv').replace(',', '')), '')
        m = BALLOTS_MIN_RE.search(val)
        if m:
            rest = val[m.end():].strip(' .,;')
            return (int(m.group('ballots').replace(',', '')), None, rest)
    return None


NUMISH_RE = re.compile(r'^[\d.,%()\s]+$')


def expand_cells(cells):
    """Split OCR cells that merged several numeric cells ('84 0 100.00% 0.00%')
    into individual tokens; text cells are left untouched."""
    out = []
    for c in cells:
        if ' ' in c and NUMISH_RE.match(c) and re.search(r'\d', c):
            out.extend(t for t in c.split() if t)
        else:
            out.append(c)
    return out


def repair_precinct(name):
    """Repair an OCR-truncated precinct header against the known list.
    Returns (name, repaired_bool)."""
    if not name:
        return name, False
    n = re.sub(r'\s+', ' ', name).strip()
    for k in KNOWN_PRECINCTS:
        if n.upper() == k:
            return k, n != k
    best = difflib.get_close_matches(n.upper(), KNOWN_PRECINCTS, n=1, cutoff=0.72)
    if best:
        return best[0], True
    return name, False


# Garbled pct cell: digits with 3+ decimals and no % sign ('32.0170')
PCT_GARBLED_RE = re.compile(r'^\d+\.\d{3,}$')


def classify_cells(cells, ctx, problems):
    """One data row -> (kind, name, nums) or None. Shared wrapper around
    classify_row() that expands merged numeric cells and tolerates garbled
    pct cells."""
    expanded = expand_cells(cells)
    res = classify_row(expanded, ctx, problems)
    if res is None and expanded is not cells:
        res = classify_row(cells, ctx, problems)   # unexpanded shape fallback
    return res


pending_name = None    # name spilled from a merged OCR cell (module state)
precinct_classify = False   # set by parse() once precinct mode is detected
last_pct = None        # Vote-% cell of the previous data row (label-shift check)
tail_shift = False     # OCR shifted a block's tail labels up by one (the
                       # named row merged its 'Write-in' label): the row
                       # labeled Total is the write-in remainder, Total is
                       # labeled Overvotes, Overvotes is labeled Undervotes
tail_shift_under = False   # shifted block: real Undervotes row still expected


def _count_nums(cells):
    """Number of pure-integer cells in a raw row (label-shift check)."""
    return sum(1 for c in cells if re.match(r'^\d[\d,]*$', c))


def classify_row(cells, ctx, problems):
    """Parse one candidate/stats table row.
    Returns (kind, name, nums) or None if not a data row.
    Handles OCR shapes:
      [name, votes, pct, ED, MI, PR]   standard
      [name, votes, pct, ED, MI]       PR column lost (PR=0, forced by the
                                       printed Total's ED+MI breakdown)
      [votes, pct, ED, MI, ...]        name lost (uses pending spill name)
      [name, votes, ED, MI, PR]        pct cell lost
      [label, count, ...]              Over/Undervotes rows
      [name Write-in, ...]             Write-in label merged into the name
    """
    global pending_name, last_pct, tail_shift, tail_shift_under
    name = cells[0]
    name_missing = False
    if '\n' in name:
        # merged OCR cell: "Francis M. Nebzydoski\nWrite-in" -> first line
        # is this row's name; a second line is the NEXT row's lost name
        first, extra = (name.split('\n', 1) + [''])[:2]
        name = first.strip()
        pending_name = extra.strip() or None
    low = name.lower().replace(' ', '')
    overunder = None
    for frag, lab in OVERUNDER_ANY:
        if frag in low:
            overunder = lab
            break
    # Vote-% cell of this row (drives the label-shift repair below)
    pct_val = None
    for c in cells[1:]:
        if PCT_RE.match(c):
            try:
                pct_val = float(c[:-1])
            except ValueError:
                pct_val = None
            break
    kind = None
    if name == 'Total':
        kind = 'total'
        if precinct_classify and pct_val is not None and pct_val < 99.0 \
                and last_pct is not None \
                and abs(last_pct + pct_val - 100) < 0.05:
            # Label shift: this is the un-named write-in remainder row of
            # the preceding named-Write-in row (pcts add to 100.00%)
            kind = 'writein'
            tail_shift = True
    elif overunder:
        kind = 'over' if overunder == 'Overvotes' else 'under'
        if precinct_classify and pct_val is not None and pct_val >= 99.0:
            # an Over/Undervotes row never carries a Vote-% cell: this is
            # the block's Total row (labels shifted up by one)
            kind = 'total'
        elif precinct_classify and tail_shift:
            # still in a shifted block: the row labeled Undervotes is the
            # real Overvotes row
            kind = 'over'
            tail_shift = False
            tail_shift_under = True
            if _count_nums(cells) > 1:
                problems.append(
                    f'NOTE: extra numeric cell(s) in shifted over/under row '
                    f'{cells!r} of {ctx} (garbled merge); not used')
        else:
            tail_shift = False
    elif low.startswith('write-in'):
        kind = 'writein'
    elif re.match(r'(?i)^(.+?)\s+write-?ins?$', name):
        # Write-in label merged into the name cell ("Zachary Tamblyn
        # Write-in"): a named write-in candidate row
        name = re.match(r'(?i)^(.+?)\s+write-?ins?$', name).group(1).strip()
        kind = 'cand'
    elif re.match(r'^\d[\d,]*$', name) or PCT_RE.match(name):
        # name cell lost -> previous row's merged spill-over name
        if pending_name:
            name = pending_name
            pending_name = None
            if '\n' in name:   # spill cell itself merged two rows' names
                first, extra = (name.split('\n', 1) + [''])[:2]
                name = first.strip()
                pending_name = extra.strip() or None
            low = name.lower().replace(' ', '')
            if name == 'Total':
                kind = 'total'
            else:
                ou = None
                for frag, lab in OVERUNDER_ANY:
                    if frag in low:
                        ou = lab
                        break
                if ou:
                    kind = 'over' if ou == 'Overvotes' else 'under'
                else:
                    kind = 'writein' if low.startswith('write-in') else 'cand'
            name_missing = True
        else:
            return None
    else:
        if not re.search(r'[A-Za-z]', name):
            # OCR junk cell ('(0') in the name position
            return None
        kind = 'cand'
        tail_shift = False
        tail_shift_under = False
    last_pct = pct_val
    vals = []
    pct_first = False   # pct before any number -> the votes cell was lost
    seen_num = False
    for c in (cells[1:] if not name_missing else cells):
        if c == '':
            continue
        if PCT_RE.match(c):
            if not seen_num:
                pct_first = True
            continue
        if PCT_GARBLED_RE.match(c):
            # OCR garbled the vote-% cell ('32.0170'); it is not data
            continue
        if re.match(r'^[a-zA-Z]{1,2}$', c):
            # stray OCR letter in a numeric row ('Write-in', '10', .., 'b', ..)
            continue
        if re.match(r'(?i)^(turnout|without|registered)\b', c):
            continue   # OCR page-tail text merged into the row
        if re.match(r'^\d[\d,]*$', c):
            seen_num = True
            vals.append(int(c.replace(',', '')))
        else:
            return None
    if not vals:
        return None
    if kind in ('over', 'under'):
        return kind, name, (vals[0],)
    if pct_first and len(vals) == 3:
        # votes cell lost to OCR ('Write-in 100.00% 0 1 0'); the breakdown
        # must sum to the row total, so votes = ED + MI + PR
        vals = [sum(vals)] + vals
        problems.append(f'NOTE: votes cell lost in row {name!r} of {ctx}; '
                        f'votes={vals[0]} (= ED+MI+PR breakdown)')
    if len(vals) == 3:
        vals = vals + [0]
        problems.append(f'NOTE: PR column lost in row {name!r} of {ctx}; '
                        f'PR=0 (ED+MI sum to the printed total)')
    if len(vals) > 4:
        problems.append(f'NOTE: extra numeric cells in row {name!r} of '
                        f'{ctx}: {cells!r}; keeping the first four')
        vals = vals[:4]
    if len(vals) != 4:
        return None
    return kind, name, tuple(vals)


def normalize_office(office_text, precinct_muni=None):
    """(office, district) from a contest title, mirroring
    pa_wayne_general_2023_results_parser conventions:
      - 'TOWNSHIP AUDITOR - 6 YEAR TERM - STERLING TOWNSHIP' ->
        ('Township Auditor 6 Year Term', 'Sterling Township')
      - 'WESTERN WAYNE SCHOOL DIRECTOR - REGION #3' ->
        ('School Director', 'Western Wayne Region 3')
      - 'WAYNE HIGHLANDS SCHOOL DIRECTOR - REGION #3 - 4 YEAR TERM' ->
        ('School Director 4 Year Term', 'Wayne Highlands Region 3')
    Bare municipality-less titles (TAX COLLECTOR 2 YEAR TERM, COUNCIL MEMBER,
    BOROUGH AUDITOR - 2 YEAR TERM) keep district '' here; the county summary
    is cross-checked against the precinct report where the precinct's
    municipality disambiguates them.
    """
    office = re.sub(r'\s+', ' ', office_text).strip()
    district = ''

    # Split trailing "- MUNICIPALITY" suffix(es)
    m = re.match(r'^(.*?)\s*-\s*([A-Z][A-Z\s.]*?(?:TOWNSHIP|BOROUGH))\s*$', office)
    if m:
        office, district = m.group(1).strip(), m.group(2).strip()
        district = district.title()

    # Magisterial district numbers (none expected in Wayne 2023 primary, kept
    # for parity with the general parser)
    m = re.search(r'(\d+-\d+-\d+)', office)
    if m:
        district = district or m.group(1)
        office = re.sub(r'\s*' + re.escape(m.group(1)), '', office).strip()

    # School director: 'WAYNE HIGHLANDS SCHOOL DIRECTOR - REGION #3 - 4 YEAR TERM'
    sd = re.match(r'^(.*?)\s+SCHOOL DIRECTOR(?:\s+(.*))?$', office, re.IGNORECASE)
    if sd:
        dist = sd.group(1).strip().title()
        rest = (sd.group(2) or '').strip()
        region = re.search(r'REGION\s*#?\s*(\d+)', rest, re.IGNORECASE)
        if region:
            dist = f'{dist} Region {region.group(1)}'
            rest = re.sub(r'-?\s*REGION\s*#?\s*\d+\s*', '', rest, flags=re.IGNORECASE).strip()
        term = re.search(r'(\d+)\s*YEAR TERM', rest, re.IGNORECASE)
        office = (f'School Director {term.group(1)} Year Term' if term
                  else 'School Director')
        return office, (district or dist)

    # Title case, small words lowercase, term suffixes preserved
    words = office.split()
    norm = []
    for w in words:
        if w.lower() in ('of', 'the', 'and', 'for'):
            norm.append(w.lower())
        else:
            norm.append(w.title())
    office = ' '.join(norm)
    office = office.replace('County Auditors', 'County Auditor')
    office = office.replace('County Commissioners', 'County Commissioner')
    # The primary headers use "- " separators inside the office/term part
    # ("Township Auditor - 6 Year Term"); the county's own general file spells
    # these without dashes ("Township Auditor 6 Year Term") - match that.
    office = office.replace(' - ', ' ')
    if office.startswith('Supervisor'):
        office = 'Township ' + office
    # OCR (and the report) spell Prompton Borough "Promption"; the county's
    # general file (pdftotext) uses "Prompton"
    office = office.replace('Promption ', 'Prompton ')
    district = district.replace('Promption ', 'Prompton ')

    # Contest titles printed without a municipality suffix in the countywide
    # report. Their municipalities, from the county's own general file (the
    # same bare titles get the precinct's municipality there) and verified
    # against the 2023 primary precinct report (each appears in exactly one
    # borough's precinct sections):
    BARE_TITLE_DISTRICT = {
        'Borough Auditor 2 Year Term': 'Starrucca Borough',
        'Borough Auditor 6 Year Term': 'Prompton Borough',
        'Tax Collector 2 Year Term': 'Prompton Borough',
        'Council Member': 'Honesdale Borough',
    }
    if not district and office in BARE_TITLE_DISTRICT:
        district = BARE_TITLE_DISTRICT[office]

    # OCR-garbled contest header -> real title (verified: the garbled block's
    # printed Total/Overvotes/Undervotes match the companion report's clean
    # block exactly; see work2023p/validate/wayne.md)
    GARBLED_TITLES = {
        'COINCHI MEMRFR.4 YEAR TFRM. RFTHANY BOROUGH':
            'COUNCIL MEMBER - 4 YEAR TERM - BETHANY BOROUGH',
    }
    if office_text in GARBLED_TITLES:
        office = GARBLED_TITLES[office_text]
        return normalize_office(office, precinct_muni)

    # Countywide offices keep district '' even in the precinct report (the
    # county's own general precinct file leaves their district empty);
    # municipality-scoped titles carry their municipality themselves, and
    # the four bare titles above are pinned by BARE_TITLE_DISTRICT.
    return office, district


class Contest:
    def __init__(self, title, party, vote_for, precinct=None):
        self.title = title
        self.party = party
        self.vote_for = vote_for
        self.precinct = precinct
        self.rows = []           # (candidate, votes, ed, mi, pr) in source order
        self.writein = None      # accumulating dict
        self.writein_done = False
        self.total = None        # (votes, ed, mi, pr) from the printed Total row
        self.over = None
        self.under = None
        self.ballots = None      # from the ballots line (total + over + under)
        self.rv = None

    def add_writein(self, votes, ed, mi, pr):
        if self.writein is None:
            self.writein = {'votes': 0, 'ed': 0, 'mi': 0, 'pr': 0}
        self.writein['votes'] += votes
        self.writein['ed'] += ed
        self.writein['mi'] += mi
        self.writein['pr'] += pr

    def flush_writein(self):
        if self.writein is not None and not self.writein_done:
            w = self.writein
            self.rows.append(('Write-ins', w['votes'], w['ed'], w['mi'], w['pr']))
            self.writein_done = True


def parse(text, problems, catalog=None):
    """Returns (contests, stats, precinct_mode, seen_precincts, orphan_runs,
    precinct_party_rv).

    catalog: optional contest catalog (from the companion countywide report)
    enabling fuzzy repair of OCR-garbled contest headers in precinct mode.
    """
    precinct_mode = False
    for kind, val in tokens(text):
        if kind == 'text' and PRECINCT_RE.match(val):
            precinct_mode = True
            break
    global precinct_classify, last_pct, tail_shift, tail_shift_under
    precinct_classify = precinct_mode
    last_pct = None
    tail_shift = False
    tail_shift_under = False

    contests = []            # Contest objects in source order
    current = None
    stats = {'bc': None, 'rv': None}
    seen_precincts = []
    current_precinct = None
    recent = []             # last few text tokens (debug context for orphans)
    orphan_runs = []        # runs of data blocks whose contest headers were OCR-lost
    run = None              # orphan run currently being buffered
    pending_ballots = []    # (ballots, rv) lines waiting for a headerless block
    seen_keys = {}          # (precinct, title, party) -> Contest (dup detection)
    precinct_party_rv = {}  # precinct -> {'DEM': set((ballots, rv)), 'REP': ...}

    def close_contest():
        nonlocal current, run
        if run is not None:
            orphan_runs.append(run)
            run = None
        if pending_ballots:
            problems.append(f'NOTE: {len(pending_ballots)} ballots line(s) '
                            'before a lost-header run were never attached '
                            f'({pending_ballots!r})')
            pending_ballots.clear()
        if current is not None:
            current.flush_writein()
            current = None

    def complete(c):
        return c is not None and c.total is not None and c.over is not None \
            and c.under is not None

    def new_subblock():
        """Latest sub-block of the current orphan run (Contest-shaped)."""
        nonlocal run
        global tail_shift, tail_shift_under
        tail_shift = False
        tail_shift_under = False
        if run is None:
            run = {'blocks': [], 'precinct': current_precinct,
                   'index': len(contests), 'ctx': ' | '.join(recent[-6:])}
        if not run['blocks'] or complete(run['blocks'][-1]):
            blk = Contest('', None, None, precinct=current_precinct)
            if pending_ballots:
                blk.ballots, blk.rv = pending_ballots.pop(0)
            run['blocks'].append(blk)
        return run['blocks'][-1]

    def handle_text(val):
        """Process one text token; returns True if consumed."""
        nonlocal current, current_precinct
        stats_m = COUNTY_STATS_RE.search(val)
        if stats_m:
            stats['bc'] = int(stats_m.group('bc').replace(',', ''))
            stats['rv'] = int(stats_m.group('rv').replace(',', ''))
            return True
        if any(val.startswith(p) for p in SKIP_PREFIXES):
            return True
        if re.match(r'^\d+ precincts reported out of', val) \
                or re.match(r'^Turnout:?', val):
            return True
        if val in ('All Precincts', 'Choice', '2023-05-31') or not val:
            return True
        if re.match(r'^\d{2}:\d{2}:\d{2}$', val):
            return True
        if re.search(r'^(ED|MI|PR)\s*=', val) or val.startswith('Absentee Ball') \
                or val.startswith('Mail-in and'):
            return True
        prec = PRECINCT_RE.match(val)
        if prec and precinct_mode:
            close_contest()
            pname, fixed = repair_precinct(prec.group(1).strip())
            if fixed:
                problems.append(f'NOTE: repaired OCR-truncated precinct name '
                                f'{prec.group(1).strip()!r} -> {pname!r}')
            current_precinct = pname
            if current_precinct not in seen_precincts:
                seen_precincts.append(current_precinct)
            return True
        if prec:
            return True
        # OCR sometimes concatenates a contest header + the next ballots line
        # into one text token; split at each embedded header and process.
        m = CONTEST_ANY_RE.search(val)
        if m and (m.start() > 0 or m.end() < len(val)):
            if m.start() > 0:
                handle_text(val[:m.start()].strip())
            title = val[m.start():m.end()]
            rest = val[m.end():].strip()
            handle_contest_header(title)
            if rest:
                handle_text(rest)
            return True
        if m:
            handle_contest_header(val)
            return True
        bal = match_ballots(val)
        if bal is not None and current is not None:
            current.ballots, current.rv, rest = bal
            if rest:
                # OCR merged data rows into the ballots line at a page break:
                # '52 ballots (...), 200 registered voters, turnout 26.00%
                #  Write-in 100.00% 0 1 0'
                merged_tail_rows(rest)
            return True
        if bal is not None:
            # ballots line whose contest header was OCR-lost: queue it for
            # the next headerless sub-block (identified by its RV below)
            if precinct_mode:
                pending_ballots.append((bal[0], bal[1]))
                return True
        if precinct_mode and catalog and \
                re.match(r"^[A-Z][A-Z0-9 ,.#!&'/-]+$", val) \
                and 4 <= len(val) <= 60:
            # header fragments: an OCR-truncated contest header ('## DISTRICT
            # ATTOR' at a page bottom; the party/vote-for part was cut off).
            # Fuzzy-repair the title from the catalog.
            fixed = fuzzy_header(val)
            if fixed is not None:
                return True
        if current is None:
            problems.append(f'unattached text: {val!r}')
            return True
        if not PCT_RE.match(val):
            problems.append(f'unparsed text in contest {current.title!r}: {val!r}')
        return True

    def fuzzy_header(val):
        """Fuzzy-repair a garbled/truncated contest header against the
        catalog. Returns True if a contest was opened."""
        t = re.sub(r'\s+', ' ', val).strip(' #.,;:')
        vote_for = None
        vm = re.search(r'v[oeo]t[eo]\s*fo[r]?\s*(\d+)', t, re.IGNORECASE)
        if vm:
            vote_for = int(vm.group(1))
        title_part = re.split(r'\s*\((?:DEMOCR|REPUBL|REPUBLIC)', t, 1,
                              flags=re.IGNORECASE)[0].strip(' #.,;:-')
        title_part = re.sub(r'\s+v[oeo]t[eo]\s+fo[r]?\s+\d+\s*$', '',
                            title_part, flags=re.IGNORECASE)
        okey = title_key(title_part).split(' - ')[0]
        known = catalog['office2keys']
        best = difflib.get_close_matches(okey, list(known), n=1, cutoff=0.78)
        if not best:
            return None
        real_title, real_party = catalog['norm2key'][known[best[0]][0]]
        real_vf = catalog['titles'][real_title.upper()][1]
        nonlocal current
        close_contest()
        current = Contest(real_title, real_party,
                          vote_for or real_vf, precinct=current_precinct)
        contests.append(current)
        problems.append(f'NOTE: fuzzy-repaired contest header {val!r} -> '
                        f'{real_title!r} ({real_party})')
        return True

    def merged_tail_rows(rest):
        """Data rows OCR merged into a ballots line's tail."""
        for part in re.split(r'(?=\b(?:Write-?in|Total|Over\w*|Under\w*)\b)', rest):
            part = part.strip()
            if not part or re.match(r'(?i)^(turnout|without|registered)\b', part):
                continue
            toks = part.split()
            name = toks[0]
            nums = [t for t in toks[1:] if re.match(r'^\d[\d,]*$', t)]
            cells = [name] + nums
            res = classify_cells(cells, f'{current.precinct!r} {current.title!r}'
                                 if current is not None else 'no contest', problems)
            if res is not None and current is not None:
                apply_row(current, *res, 'merged ballots-line tail')
            elif res is None:
                problems.append(f'unparsed merged tail row: {part!r}')

    def handle_contest_header(val):
        nonlocal current
        global tail_shift, tail_shift_under
        tail_shift = False
        tail_shift_under = False
        contest_m = CONTEST_RE.match(val)
        close_contest()
        ptok = contest_m.group('party').upper()
        if ptok.startswith('DEM'):
            party = 'DEM'
        elif ptok.startswith('REP'):
            party = 'REP'
        else:
            party = None
            problems.append(f'unknown party token in header: {val!r}')
        title = contest_m.group('title').strip()
        # Repair an OCR-garbled title against the catalog (keeps the header's
        # own party/vote-for tokens as authoritative). Precinct-mode only:
        # the countywide reports' headers are clean, and fuzzy repair there
        # would corrupt distinct term-length titles (4 -> 6 year etc).
        if precinct_mode and catalog is not None:
            tkey = title_key(title)
            office_part = tkey.split(' - ')[0]
            if tkey in catalog['norm2key']:
                real = catalog['norm2key'][tkey][0]
                if real != title:
                    problems.append(f'NOTE: title spelling normalized '
                                    f'{title!r} -> {real!r}')
                    title = real
            elif title.upper() not in catalog['titles']:
                # match on the office part only, so a municipality the
                # companion report prints differently (or omits; it prints
                # some titles bare) cannot pull the match to a different
                # municipality's contest (Prompton -> Waymart etc.)
                okeys = catalog['office2keys'].get(office_part)
                if okeys and len(okeys) == 1:
                    pass   # keep the precinct's own title (office verified)
                elif okeys:
                    muni = title_key(title).split(' - ')[-1] \
                        if ' - ' in tkey else ''
                    with_muni = [k for k in okeys if k.endswith(' - ' + muni)]
                    if len(with_muni) == 1:
                        pass
                else:
                    best = difflib.get_close_matches(
                        office_part, list(catalog['office2keys']),
                        n=1, cutoff=0.78)
                    if best and best[0] != office_part:
                        # replace only the office part; the header's own
                        # municipality (printed intact) is more reliable
                        cand_title = catalog['norm2key'][
                            catalog['office2keys'][best[0]][0]][0]
                        cm = re.match(r'^(.*?)(?:\s*-\s*[A-Z][A-Z\s.#]*'
                                      r'(?:TOWNSHIP|BOROUGH))?\s*$',
                                      cand_title)
                        fixed_office = cm.group(1).strip()
                        tm = re.match(r'^(.*?)\s*-\s*([A-Z][A-Z\s.#]*'
                                      r'(?:TOWNSHIP|BOROUGH))\s*$', title)
                        office_txt = tm.group(1).strip() if tm else title
                        muni = tm.group(2) if tm else None
                        real = fixed_office + (f' - {muni}' if muni else '')
                        if real != title:
                            problems.append(f'NOTE: fuzzy-repaired garbled '
                                            f'title {title!r} -> {real!r}')
                            title = real
        current = Contest(title, party,
                          int(contest_m.group('vote_for')),
                          precinct=current_precinct)
        contests.append(current)

    def apply_row(contest, kind, name, nums, ctx):
        """kind: 'total'|'over'|'under'|'writein'|'cand'."""
        if kind == 'total':
            if contest.total is not None:
                problems.append(f'duplicate Total row: {contest.title!r} ({ctx})')
            else:
                contest.total = tuple(nums)
            contest.flush_writein()
        elif kind == 'over':
            contest.over = (contest.over or 0) + nums[0]
        elif kind == 'under':
            contest.under = (contest.under or 0) + nums[0]
        elif kind == 'writein':
            contest.add_writein(*nums)
        else:
            contest.rows.append((name, *nums))

    for kind, val in tokens(text):
        if kind == 'text':
            if val and not val.startswith('Precinct '):
                recent.append(val[:90])
                del recent[:-6]
            handle_text(val)
            continue

        # table row token
        cells = [c.strip() for c in val]
        while cells and cells[0] == '':
            cells.pop(0)
        while cells and cells[-1] == '':
            cells.pop()
        if not cells:
            continue

        # The page's Choice-header row sometimes arrives merged with the
        # first data row of the next block ('Choice ... PR | Total | 1 | ...').
        if cells[0] == 'Choice':
            if len(cells) > 6:
                cells = cells[6:]
            else:
                continue
        if cells[0] == 'All Precincts' and not any(cells[1:]):
            continue

        # page-header rows: ("Statement of Votes Cast by Geography",
        # "Page: 3 of 27") etc.
        if len(cells) <= 2 and any(cells[0].startswith(p) for p in SKIP_PREFIXES):
            single = ' '.join(cells).strip()
            stats_m = COUNTY_STATS_RE.search(single)
            if stats_m:
                stats['bc'] = int(stats_m.group('bc').replace(',', ''))
                stats['rv'] = int(stats_m.group('rv').replace(',', ''))
            continue

        if len(cells) == 1:
            # a shifted block's real Undervotes row can survive as a bare
            # numeric cell after the shifted Overvotes row
            if cells[0] and tail_shift_under and current is not None \
                    and re.match(r'^\d[\d,]*$', cells[0]):
                current.under = (current.under or 0) + \
                    int(cells[0].replace(',', ''))
                tail_shift_under = False
                problems.append(
                    f'NOTE: bare numeric row {cells[0]} taken as Undervotes '
                    f'in {current.title!r} ({current.precinct!r}; OCR label '
                    'shift)')
                continue
            # a data row whose name cell was lost entirely: a bare numeric
            # cell right after a merged-cell spill pairs with that pending
            # name (e.g. Palmyra 'Overvotes' 13) instead of leaking into
            # handle_text as unattached text
            if cells[0] and pending_name and current is not None \
                    and not complete(current) \
                    and (re.match(r'^\d[\d,]*$', cells[0])
                         or PCT_RE.match(cells[0])):
                ctx1 = f'{current.precinct!r} {current.title!r}'
                parsed = classify_row([cells[0]], ctx1, problems)
                if parsed is not None:
                    kind, name, nums = parsed
                    if kind == 'cand':
                        current.rows.append((name, *nums))
                    elif kind == 'writein':
                        current.add_writein(*nums)
                    elif kind == 'total':
                        apply_row(current, 'total', name, nums, ctx1)
                    elif kind == 'over':
                        current.over = (current.over or 0) + nums[0]
                    elif kind == 'under':
                        current.under = (current.under or 0) + nums[0]
                    continue
            # colspan header/ballots cells appear as single-cell rows
            if cells[0]:
                handle_text(cells[0])
            continue

        # OCR merged a ballots line into a table row cell
        # ('50 ballots (...), 183 registered voters, turnout', 'Write-in', 9, ..)
        if any(re.match(r'^\d[\d,]*\s*bal', c, re.I) for c in cells):
            kept = []
            for c in cells:
                if re.match(r'^\d[\d,]*\s*bal\w{0,6}\b', c, re.I):
                    handle_text(c)
                else:
                    kept.append(c)
            cells = kept
            if not cells:
                continue

        ctx = f'{current.precinct!r} {current.title!r}' if current is not None \
            else 'no contest'
        parsed = classify_cells(cells, ctx, problems)

        # A contest whose Total + Overvotes + Undervotes rows are all seen is
        # finished; further data rows belong to a NEW block whose header was
        # OCR-lost -> buffer as an orphan block, never absorb them.
        if complete(current):
            close_contest()

        if current is None:
            if parsed is None:
                problems.append(f'data row outside contest: {cells!r}')
                continue
            kind, name, nums = parsed
            target = new_subblock()
        else:
            if parsed is None:
                problems.append(f'unparsed row in contest {current.title!r}: {cells!r}')
                continue
            kind, name, nums = parsed
            target = current

        if target is current or isinstance(target, Contest):
            if kind == 'cand':
                target.rows.append((name, *nums))
            elif kind == 'writein':
                target.add_writein(*nums)
            elif kind == 'total':
                apply_row(target, 'total', name, nums, ctx)
            elif kind == 'over':
                target.over = (target.over or 0) + nums[0]
            elif kind == 'under':
                target.under = (target.under or 0) + nums[0]

    close_contest()

    _post_parse(contests, seen_keys, precinct_party_rv, problems)
    return (contests, stats, precinct_mode, seen_precincts, orphan_runs,
            precinct_party_rv)


def _register_party_rv(precinct_party_rv, c):
    if c.precinct and c.party and c.ballots is not None:
        precinct_party_rv.setdefault(c.precinct, {'DEM': set(), 'REP': set()})
        precinct_party_rv[c.precinct][c.party].add((c.ballots, c.rv))


def _same_rows(a, b):
    """True if two contest instances carry identical printed data."""
    return (a.rows == b.rows and a.total == b.total
            and a.over == b.over and a.under == b.under)


def _post_parse(contests, seen_keys, precinct_party_rv, problems):
    """End-of-parse bookkeeping for the whole contest stream:
    duplicate-block detection and per-precinct party RV registration."""
    for c in list(contests):
        if c.title and c.party:
            key = (c.precinct, c.title, c.party)
            prior = seen_keys.get(key)
            if prior is None:
                seen_keys[key] = c
                _register_party_rv(precinct_party_rv, c)
            elif _same_rows(prior, c):
                contests.remove(c)
                problems.append(f'NOTE: dropped OCR-duplicated block {key!r} '
                                f'(identical data repeated)')
            else:
                contests.remove(c)
                problems.append(f'PROBLEM: dropped duplicate block {key!r} '
                                f'with DIFFERENT data '
                                f'(rows={c.rows} total={c.total} vs '
                                f'rows={prior.rows} total={prior.total})')


def canonicalize_names(contests, catalog, precinct_mode, problems):
    """Precinct mode: repair OCR-mangled candidate names against the
    catalog's candidate lists (unique fuzzy match), e.g. 'Harry F. Small
    Jr.' -> 'Harry F. Smail Jr.'.  Ambiguous matches are left as printed."""
    if not precinct_mode or catalog is None:
        return
    n = 0
    for c in contests:
        if not c.title or c.party is None:
            continue
        raw_key = catalog['norm2key'].get(title_key(c.title))
        if raw_key is None:
            continue
        cands = catalog['cands'].get((raw_key[0], c.party)) \
            or catalog['cands'].get(raw_key) or set()
        if not cands:
            continue
        lower_map = {x.lower(): x for x in cands}
        fixed = []
        for row in c.rows:
            name = row[0]
            repl = None
            if name not in cands and not re.search(r'(?i)write-?in', name):
                m = difflib.get_close_matches(name.lower(), list(lower_map),
                                              n=2, cutoff=0.72)
                if len(m) == 1:
                    repl = lower_map[m[0]]
                elif len(m) > 1:
                    problems.append(
                        f'NOTE: ambiguous OCR candidate name {name!r} in '
                        f'{c.title!r} ({c.precinct!r}); left as printed')
            if repl is not None:
                problems.append(
                    f'NOTE: candidate name normalized {name!r} -> {repl!r} '
                    f'in {c.title!r} ({c.precinct!r})')
                fixed.append((repl,) + tuple(row[1:]))
                n += 1
            else:
                fixed.append(row)
        c.rows = fixed
    if n:
        print(f'Candidate names normalized against the county catalog: '
              f'{n} row(s)')


def precinct_municipality(precinct_name):
    """'HONESDALE BOROUGH #1' -> 'Honesdale Borough'; 'STERLING TOWNSHIP'
    -> 'Sterling Township'. Strips the trailing precinct number."""
    name = re.sub(r'\s*#\d+(?:-\d+)?\s*$', '', precinct_name or '').strip()
    return name.title() if name else ''


def build_rows(contests, precinct_mode, muni_for_contest=None):
    """Flatten Contest objects into CSV row dicts (no RV/BC rows)."""
    rows = []
    for c in contests:
        key = (c.precinct, c.title)
        if muni_for_contest and key in muni_for_contest:
            office, district = muni_for_contest[key]
        else:
            muni = precinct_municipality(c.precinct) if precinct_mode else None
            office, district = normalize_office(c.title, muni)
        for (name, votes, ed, mi, pr) in c.rows:
            cand = name
            if cand in ('Write-in',):
                cand = 'Write-ins'
            if cand in ('Overvotes', 'Overtotes', 'Undervotes', 'Undervottes'):
                cand = OVERUNDER.get(cand.lower().replace(' ', ''), cand)
            row = {
                'county': COUNTY,
                'office': office,
                'district': district,
                'party': c.party,
                'candidate': cand,
                'votes': str(votes),
                'election_day': str(ed),
                'mail': str(mi),
                'provisional': str(pr),
            }
            if precinct_mode:
                row = {'precinct': c.precinct, **row}
            rows.append(row)
        # Overvotes / Undervotes rows (source order, party = contest party)
        for label, val in (('Overvotes', c.over), ('Undervotes', c.under)):
            if val is None:
                continue
            row = {
                'county': COUNTY,
                'office': office,
                'district': district,
                'party': c.party,
                'candidate': label,
                'votes': str(val),
                'election_day': '',
                'mail': '',
                'provisional': '',
            }
            if precinct_mode:
                row = {'precinct': c.precinct, **row}
            rows.append(row)
    return rows


def stats_rows(stats, precinct=None):
    out = []
    for office, val in (('Registered Voters', stats['rv']), ('Ballots Cast', stats['bc'])):
        if val is None:
            continue
        row = {'county': COUNTY, 'office': office, 'district': '', 'party': '',
               'candidate': '', 'votes': str(val), 'election_day': '',
               'mail': '', 'provisional': ''}
        if precinct is not None:
            row = {'precinct': precinct, **row}
        out.append(row)
    return out


def title_key(raw):
    """Normalized lookup key for a contest title (OCR spelling variants of
    the same contest collapse to the same key: 'Promption'->'Prompton',
    'REGION # 3'->'REGION #3', whitespace runs)."""
    office, district = normalize_office(raw)
    key = office + (' - ' + district if district else '')
    return re.sub(r'\s+', ' ', key).upper().replace('# ', '#')


def build_catalog(rec_contests):
    """Contest catalog from the companion countywide report (By-Office file):
    {'titles': {UPPER_TITLE: (party, vote_for)},
     'order': {(title, party): position},
     'cands': {(title, party): set(candidate names)}}"""
    titles = {}
    order = {}
    cands = {}
    norm2key = {}
    office2keys = {}
    for i, c in enumerate(rec_contests):
        if not c.title or c.party is None:
            continue
        key = (c.title, c.party)
        titles.setdefault(c.title.upper(), (c.party, c.vote_for))
        titles.setdefault(title_key(c.title), (c.party, c.vote_for))
        norm2key.setdefault(title_key(c.title), key)
        office2keys.setdefault(title_key(c.title).split(' - ')[0],
                               []).append(title_key(c.title))
        order.setdefault(key, i)
        c.flush_writein()
        names = {r[0] for r in c.rows if r[0] != 'Write-ins'}
        cands.setdefault(key, set()).update(names)
    return {'titles': titles, 'order': order, 'cands': cands,
            'norm2key': norm2key, 'office2keys': office2keys}


def _copy_block(src, title, party, vote_for, precinct=None):
    """Turn a recovered orphan block into a full Contest."""
    c = Contest(title, party, vote_for, precinct=precinct)
    c.rows = list(src.rows)
    c.total = src.total
    c.over = src.over
    c.under = src.under
    c.ballots = src.ballots
    c.rv = src.rv
    return c


def _position_between(key, lo_key, hi_key, catalog):
    """True if catalog order places key strictly between the two neighbor
    contest keys (either neighbor may be None = unbounded)."""
    order = catalog['order']
    if key not in order:
        return False
    pos = order[key]
    if lo_key is not None and lo_key in order and pos <= order[lo_key]:
        return False
    if hi_key is not None and hi_key in order and pos >= order[hi_key]:
        return False
    return True


def _block_signature(block):
    """Named candidate rows of an orphan block (write-ins excluded)."""
    return {r[0] for r in block.rows if r[0] != 'Write-ins'}


def _fuzzy_names_match(names, contest_names):
    """True if every block name fuzzy-matches some candidate of the contest
    (OCR-garbled names)."""
    if not names:
        return False
    cn = list(contest_names)
    for n in names:
        if n in contest_names:
            continue
        if not difflib.get_close_matches(n, cn, n=1, cutoff=0.72):
            return False
    return True


def recover_orphan_runs(runs, contests, rec_contests, problems, precinct_mode,
                        precinct_party_rv):
    """Attach OCR-lost-header data blocks to their contests.

    A run of data blocks whose contest headers were lost by OCR is buffered
    as Contest-shaped sub-blocks (rows + printed Total + Overvotes +
    Undervotes, no title).  Identity is recovered from the companion
    countywide report (an independent OCR run of the same Statement of Votes
    Cast):

      county mode: the contest whose printed (Total, Overvotes, Undervotes)
      triple matches the block's exactly, and whose (title, party) is absent
      from the input file, is the match.

      precinct mode: candidate-name signature against the catalog (every
      block name a candidate of exactly one not-yet-seen contest); ties and
      name-less (write-in-only) blocks are narrowed to contests strictly
      between the parsed neighbors in countywide contest order, with the
      party (when known) from the block's ballots-line RV vs the precinct's
      per-party (ballots, RV) pairs.

    Requires --recover <by_office_ocr.txt>.
    """
    if not runs:
        return contests
    n_blocks = sum(len(r['blocks']) for r in runs)
    if rec_contests is None:
        problems.append(
            f'{n_blocks} orphan data block(s) in {len(runs)} run(s) with lost '
            'contest headers; pass --recover <by-office ocr text> to restore '
            'them')
        return contests

    existing = set()
    for c in contests:
        if c.title and c.party:
            existing.add((c.precinct, c.title, c.party))

    catalog = build_catalog(rec_contests)
    used = set()             # (precinct, title, party) already matched
    insertions = []      # (index, Contest)
    def _norm_key(c):
        if not c.title or c.party is None:
            return None
        tk = title_key(c.title)
        return catalog['norm2key'].get(tk, (c.title, c.party))

    for run in runs:
        prev_key = None
        if run['index'] > 0:
            prev_key = _norm_key(contests[run['index'] - 1])
        next_key = None
        if run['index'] < len(contests):
            next_key = _norm_key(contests[run['index']])
        for bi, block in enumerate(run['blocks']):
            block.flush_writein()
            if not block.rows and block.total is None:
                continue
            pos = run['index'] + bi
            src = None
            if not precinct_mode:
                # county mode: exact (total, over, under) triple match
                if block.total is not None and block.over is not None \
                        and block.under is not None:
                    key = (block.total, block.over, block.under)
                    matches = [c for c in rec_contests
                               if (c.total, c.over, c.under) == key
                               and c.title and c.party
                               and (c.title, c.party) not in
                               {(x.title, x.party) for x in contests}
                               and id(c) not in used]
                    if len(matches) == 1:
                        src = matches[0]
                    elif matches:
                        problems.append(
                            f'orphan block ambiguous ({len(matches)} triple '
                            f'matches): total={block.total} rows='
                            f'{block.rows[:3]}')
            else:
                precinct = block.precinct
                names = _block_signature(block)
                pool = [key for key in catalog['order']
                        if (precinct,) + key not in existing
                        and (precinct,) + key not in used]

                def vf_ok(key):
                    vf = catalog['titles'].get(key[0].upper(), (None,))[1]
                    if (block.ballots is None or not vf
                            or block.total is None or block.under is None):
                        return True
                    return (block.total[0] + (block.over or 0) + block.under
                            == block.ballots * vf)

                def narrow(matches):
                    if len(matches) > 1:
                        filtered = [k for k in matches if vf_ok(k)]
                        if 1 <= len(filtered) < len(matches):
                            matches = filtered
                    if len(matches) > 1:
                        narrowed = [k for k in matches
                                    if _position_between(k, prev_key,
                                                         next_key, catalog)]
                        if len(narrowed) >= 1:
                            matches = narrowed
                    return matches

                if names:
                    matches = narrow([key for key in pool
                                      if names <= catalog['cands'][key]])
                    if len(matches) > 1:
                        exact = [k for k in matches
                                 if names == catalog['cands'][k]
                                 and vf_ok(k)]
                        if 1 <= len(exact) < len(matches):
                            matches = exact
                    if not matches:
                        # OCR-garbled candidate names: fuzzy signature
                        matches = narrow([key for key in pool
                                          if _fuzzy_names_match(
                                              names, catalog['cands'][key])])
                else:
                    # write-in-only block: party from the ballots-line RV,
                    # then vote-for and countywide-order position between
                    # the neighbors
                    parties = set()
                    if block.ballots is not None and block.rv is not None:
                        pr = precinct_party_rv.get(precinct, {})
                        for p, pairs in pr.items():
                            if (block.ballots, block.rv) in pairs:
                                parties.add(p)
                    if not parties:
                        parties = {'DEM', 'REP'}
                    matches = narrow([k for k in pool if k[1] in parties
                                      and not catalog['cands'][k]])
                if len(matches) == 1:
                    src_key = matches[0]
                    used.add((precinct,) + src_key)
                    rec_title, rec_party = src_key
                    rec_vf = catalog['titles'].get(rec_title.upper(), (
                        None, None))[1]
                    c = _copy_block(block, rec_title, rec_party, rec_vf,
                                    precinct=precinct)
                    insertions.append((pos, c, src_key))
                    continue
                elif matches:
                    problems.append(
                        f'orphan block ambiguous ({len(matches)} candidates) '
                        f'in {precinct!r}: names={sorted(names)} '
                        f'matches={matches}')
            if src is not None:
                c = _copy_block(block, src.title, src.party, src.vote_for)
                insertions.append((pos, c, (src.title, src.party)))
                continue
            problems.append(
                f'ORPHAN BLOCK UNRECOVERED (precinct {block.precinct!r}, '
                f'after position {run["index"]}): total={block.total} '
                f'over={block.over} under={block.under} rows={block.rows}')

    # Ascending insert order (original stream positions, unadjusted) matches
    # the county deliverable's recovered-block placement.
    for pos, c, src_key in sorted(insertions, key=lambda t: t[0]):
        contests.insert(pos, c)
        existing.add((c.precinct, c.title, c.party))
        print(f'RECOVERED contest from orphan block: {c.title!r} '
              f'({c.party}, {len(c.rows)} data rows) at position {pos}')
    return contests


def reconcile(contests, problems):
    """Per contest: candidate rows + write-ins == printed Total; and
    Total + overvotes + undervotes == ballots-line total."""
    ok_rows = 0
    ok_ballots = 0
    n_ballot_checks = 0
    for c in contests:
        if c.total is None:
            problems.append(f'no Total row: {c.title!r} '
                            f'(precinct {c.precinct!r})')
            continue
        sums = tuple(sum(r[i] for r in c.rows) for i in range(1, 5))
        if sums != c.total:
            problems.append(
                f'RECONCILE MISMATCH {c.title!r} (precinct {c.precinct!r}): '
                f'rows {sums} != printed total {c.total}')
        else:
            ok_rows += 1
        if c.ballots is not None and c.over is not None and c.under is not None:
            n_ballot_checks += 1
            # undervotes = ballots*vote_for - total - overvotes, so the
            # printed "N ballots" line must satisfy
            #     Total + overvotes + undervotes == ballots * Vote-for-N
            if c.total[0] + c.over + c.under == c.ballots * c.vote_for:
                ok_ballots += 1
            else:
                problems.append(
                    f'BALLOTS MISMATCH {c.title!r} (precinct {c.precinct!r}): '
                    f'total {c.total[0]} + over {c.over} + under {c.under} '
                    f'!= ballots {c.ballots} x vote_for {c.vote_for}')
    print(f'Contest-totals reconciliation: {ok_rows}/{len(contests)} contests '
          f'match (candidates + write-ins == printed Total).')
    if n_ballot_checks:
        print(f'Ballots-line check: {ok_ballots}/{n_ballot_checks} contests '
              f'match (Total + overvotes + undervotes == ballots line).')
    return ok_rows


FIELDNAMES_COUNTY = ['county', 'office', 'district', 'party',
                     'candidate', 'votes', 'election_day', 'mail', 'provisional']
FIELDNAMES_PRECINCT = ['county', 'precinct', 'office', 'district', 'party',
                       'candidate', 'votes', 'election_day', 'mail', 'provisional']


def write_csv(rows, output_path, precinct_mode):
    fields = FIELDNAMES_PRECINCT if precinct_mode else FIELDNAMES_COUNTY
    import csv
    with open(output_path, 'w', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)
    print(f'Wrote {len(rows)} rows to {output_path}')


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument('input')
    ap.add_argument('output')
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument('--county', action='store_true')
    mode.add_argument('--precinct', action='store_true')
    ap.add_argument('--recover', nargs='+', metavar='COUNTYWIDE_OCR_TXT',
                    help='companion countywide report(s) (OCR text) used to '
                         'restore contest blocks whose header was OCR-lost; '
                         'one file = the by-office report, two files = a '
                         'countywide summary report plus its recover file')
    args = ap.parse_args()

    text = Path(args.input).read_text(encoding='utf-8') \
        if Path(args.input).suffix.lower() == '.txt' else None
    if text is None:
        print('Only OCR text input is supported for this parser '
              '(source PDFs are scanned).', file=sys.stderr)
        sys.exit(1)

    problems = []
    rec_contests = None
    catalog = None
    if args.recover:
        rec_files = args.recover
        rec_problems = []
        if len(rec_files) == 1:
            (rec_contests, _, _, _, rec_runs, _) = parse(
                Path(rec_files[0]).read_text(encoding='utf-8'), rec_problems)
            # The recover file's own OCR may have lost contest headers; a
            # first catalog-less pass over the main file provides countywide
            # (Total, Overvotes, Undervotes) triples to restore them, so the
            # catalog is complete.  (In precinct mode the main file's blocks
            # are per-precinct and won't triple-match; those rec headers
            # stay lost and the title repair keeps the precinct's own
            # spelling for them.)
            first_problems = []
            (first_contests, _, _, _, _, _) = parse(text, first_problems)
            rec_contests = recover_orphan_runs(rec_runs, rec_contests,
                                               first_contests, rec_problems,
                                               False, {})
        else:
            # --recover SUMMARY BY_OFFICE: the summary report's contest list
            # (its own lost headers restored via the by-office file) is the
            # most complete catalog.
            (rec_b, _, _, _, rec_runs_b, _) = parse(
                Path(rec_files[1]).read_text(encoding='utf-8'), rec_problems)
            (first_a, _, _, _, rec_runs_a, _) = parse(
                Path(rec_files[0]).read_text(encoding='utf-8'), rec_problems)
            rec_b = recover_orphan_runs(rec_runs_b, rec_b, first_a,
                                        rec_problems, False, {})
            (rec_a, _, _, _, rec_runs_a, _) = parse(
                Path(rec_files[0]).read_text(encoding='utf-8'), rec_problems,
                catalog=build_catalog(rec_b))
            rec_contests = recover_orphan_runs(rec_runs_a, rec_a, rec_b,
                                               rec_problems, False, {})
        catalog = build_catalog(rec_contests)
        if rec_problems:
            print(f'NOTE: recover file parsed with {len(rec_problems)} '
                  f'problem(s) (unused)')

    (contests, stats, precinct_mode, seen_precincts, orphan_runs,
     precinct_party_rv) = parse(text, problems, catalog=catalog)
    if args.county:
        precinct_mode = False
    if args.precinct:
        precinct_mode = True
    contests = recover_orphan_runs(orphan_runs, contests, rec_contests,
                                   problems, precinct_mode, precinct_party_rv)
    canonicalize_names(contests, catalog, precinct_mode, problems)
    if orphan_runs and args.recover is None:
        problems.append(
            f'{len(orphan_runs)} orphan data block run(s) with lost contest '
            'headers; their rows are NOT included')

    rows = build_rows(contests, precinct_mode)
    if not precinct_mode:
        rv_bc = stats_rows(stats)
        rows = rv_bc + rows
    write_csv(rows, sys.argv[2], precinct_mode)

    reconcile(contests, problems)

    n_dem = sum(1 for c in contests if c.party == 'DEM')
    n_rep = sum(1 for c in contests if c.party == 'REP')
    if precinct_mode:
        print(f'Precinct-contest instances parsed: {len(contests)} '
              f'across {len(seen_precincts)} precincts '
              f'({n_dem} DEM, {n_rep} REP contest instances)')
    else:
        print(f'Contests parsed: {len(contests)} ({n_dem} DEM, {n_rep} REP)')
        print(f'County stats: Registered Voters {stats["rv"]}, '
              f'Ballots Cast {stats["bc"]}')

    if problems:
        print(f'PROBLEMS ({len(problems)}):')
        for p in problems[:60]:
            print('  ' + p)
        if len(problems) > 60:
            print(f'  ... and {len(problems) - 60} more')
        sys.exit(2)


if __name__ == '__main__':
    main()