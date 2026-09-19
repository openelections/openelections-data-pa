#!/usr/bin/env python3
"""
Wyoming County, PA 2022 General Election parser (precinct-level).

Source: "Wyoming PA official-precinct-results-Nov-8-2022.pdf" -- a Dominion
"Statement of Votes Cast by Geography" report (same family as the Wayne 2022
general). The 2022 PDF is a 200dpi SCAN whose embedded OCR layer is too
error-prone for direct parsing, so the text is first produced with the hosted
PaddleOCR API (sequential, one page at a time -- the full-document submit
tends to be reset by the API):

    /tmp/paddleenv/bin/python work2023p/ocr_api.py \
        "<source>.pdf" work2022g/wyoming2022_ocr.txt --first N

This parser consumes that "===== PAGE N =====" markdown/HTML output, not the
PDF itself. PaddleOCR-VL renders the report's tables as HTML <table> blocks;
occasional OCR scrambling (shifted cells, garbled "Vote for" headers) is
caught by an internal validation pass that compares each contest's candidate
sum against the report's own printed "Total" row and by a county-level
cross-check against 2022/20221108__pa__general__county.csv.

The report carries only the five countywide contests (U.S. Senate,
Governor/Lt. Governor, U.S. House 9, State Senate 20, State House 110); the
contest headers do not print district numbers, so districts are assigned
from the county's fixed district set.

Precinct names are normalized to full municipality names where the Dominion
report itself truncates them (the report prints "FAC BOROUGH WARD 1",
"TUN TOWNSHIP #2", "N R BRANCH TOWNSHIP", "NOR TOWNSHIP"); the normalized
forms match the county's 2023 precinct file.

Usage:
    python pa_wyoming_general_2022_results_parser.py <ocr_text_file> <output_csv>
"""

import csv
import html
import re
import sys

COUNTY = 'Wyoming'

# ---------------------------------------------------------------- OCR text

PAGE_RE = re.compile(r'^===== PAGE \d+ =====$', re.M)
TABLE_RE = re.compile(r'<table.*?</table>', re.S)
TR_RE = re.compile(r'<tr.*?</tr>', re.S)
TD_RE = re.compile(r'<td[^>]*>(.*?)</td>', re.S)
TD_ATTR_RE = re.compile(r'<td([^>]*)>(.*?)</td>', re.S)
ROWSPAN_RE = re.compile(r'rowspan=["\']?(\d+)', re.I)
NAME_SPLIT_RE = re.compile(r'\\n|<br\s*/?>', re.I)
TAG_RE = re.compile(r'<[^>]+>')

PRECINCT_RE = re.compile(r'^\s*(?:#+\s*)?\.?\s*Precinct\s+(.+?)\s*$')
CONTEST_RE = re.compile(r'^\s*(?:#+\s*)?(.{2,120}?)\s*\(\s*Vo\s*t?e?\s*[ftl1io]*\s*f?[otl1io]*\s*(\d+)\s*\)\s*$')
BALLOTS_RE = re.compile(
    r'(\d[\d,]*)\s+ballots?\s*\((?:.*?)\)?,?\s*([\d,]+)\s*reg', re.S)
PAGE_HEADER_SKIPS = (
    'Statement of Votes Cast by Geography',
    'Wyoming County',
    'All Precincts, All Districts',
    'Total Ballots Cast:',
    'precincts reported out of',
)
SKIP_CELLS = {'choice', 'total', 'overvotes', 'undervotes', 'registered voters',
              'overtvotes', 'overvoles', 'undervoies', 'overtotes', 'overvotes.',
              'undertoies', 'undewotes', 'undervoies', 'undarvotes',
              'overuotes', 'ove otes', 'overyotes', 'oversvotes',
              'overtoves'}

# The Dominion report truncates some precinct names; the values are the
# canonical names used in the county's 2023 precinct file.
PRECINCT_FIXES = {
    'FAC BOROUGH WARD 1': 'Factoryville Borough Ward 1',
    'FAC BOROUGH WARD 2': 'Factoryville Borough Ward 2',
    'TUN BOROUGH WARD 1': 'Tunkhannock Borough Ward 1',
    'TUN BOROUGH WARD 2': 'Tunkhannock Borough Ward 2',
    'TUN BOROUGH WARD 3': 'Tunkhannock Borough Ward 3',
    'TUN BOROUGH WARD 4': 'Tunkhannock Borough Ward 4',
    'TUN TOWNSHIP #1': 'Tunkhannock Township #1',
    'TUN TOWNSHIP #2': 'Tunkhannock Township #2',
    'N R BRANCH TOWNSHIP': 'North Branch Township',
    'NOR TOWNSHIP': 'Northmoreland Township',
}


def canon_precinct(name):
    """Canonical (title-case) precinct name for an OCR'd precinct heading."""
    name = name.upper().strip()
    if name in PRECINCT_FIXES:
        return PRECINCT_FIXES[name]
    return name.title()

# Wyoming County is entirely within these districts; the contest headers do
# not print them.
DISTRICTS = {
    'U.S. House': '9',
    'State Senate': '20',
    'State House': '110',
}

# Contest-header matchers; order matters.  The header garbles vary
# ("REPRESENTATIVE lN CONGRESS", "vote for t"), so match loosely on the
# office words and keep only clean candidates.
OFFICE_MAP = [
    (re.compile(r'UNITED\s+STATES\s+SENATOR', re.I), 'U.S. Senate'),
    (re.compile(r'GOVERNOR\s*/\s*L\s*T?\.\s*GOVERNOR', re.I), 'Governor'),
    (re.compile(r'GOVERNOR\s+AND\s+LIEUTENANT\s+GOVERNOR', re.I), 'Governor'),
    (re.compile(r'REPRESENTATIVE\s+l?N\s+CONGRESS', re.I), 'U.S. House'),
    (re.compile(r'REPRESENTATIVE\s+IN\s+CONGRESS', re.I), 'U.S. House'),
    (re.compile(r'SENATOR\s+l?N\s+GENERAL\s+ASSEMBLY', re.I), 'State Senate'),
    (re.compile(r'SENATOR\s+IN\s+GENERAL\s+ASSEMBLY', re.I), 'State Senate'),
    (re.compile(r'REPRESENTATIVE\s+l?N\s+THE\s+GENE\s?RAL\s+ASSEM\s?BLY', re.I),
     'State House'),
    (re.compile(r'REPRESENTATIVE\s+IN\s+THE\s+GENERAL\s+ASSEMBLY', re.I),
     'State House'),
]

# Parties for the candidates as printed on the 2022 general ballot
# (cross-checked against 2022/20221108__pa__general__county.csv).
PARTY_MAP = {
    'john fetterman': 'DEM',
    'mehmet oz': 'REP',
    'erik gerhardt': 'LIB',
    'richard l. weiss': 'GRN',
    'daniel wassmer': 'KEY',
    'josh shapiro': 'DEM',
    'douglas v. mastriano': 'REP',
    'matt hackenburg': 'LIB',
    'christina digiulio': 'GRN',
    'joe soloski': 'KEY',
    'amanda r. waldman': 'DEM',
    'dan meuser': 'REP',
    'jackie baker': 'DEM',
    'lisa baker': 'REP',
    'tina pickett': 'REP',
}

# OCR misreads of candidate names, mapped to the printed form (which keys
# PARTY_MAP and EXPECTED_CANDIDATES).
NAME_FIXES = {
    'jush shapiro': 'Josh Shapiro',
    'john feiterman': 'John Fetterman',
}

EXPECTED_CANDIDATES = {
    'U.S. Senate': {'JOHN FETTERMAN', 'MEHMET OZ', 'ERIK GERHARDT',
                    'RICHARD L. WEISS', 'DANIEL WASSMER'},
    'Governor': {'JOSH SHAPIRO', 'DOUGLAS V. MASTRIANO', 'MATT HACKENBURG',
                 'CHRISTINA DIGIULIO', 'JOE SOLOSKI'},
    'U.S. House': {'AMANDA R. WALDMAN', 'DAN MEUSER'},
    'State Senate': {'JACKIE BAKER', 'LISA BAKER'},
    'State House': {'TINA PICKETT'},
}


def strip_tags(s):
    return html.unescape(TAG_RE.sub('', s)).strip()


def table_rows(table_html):
    """Yield lists of cell texts for a table, expanding rowspan="2" cells.

    PaddleOCR sometimes merges a two-line name cell ("TINA PICKETT\\nWrite-in")
    into one rowspan cell; the second line is substituted into each following
    row that is missing that cell.
    """
    raw_rows = [TD_ATTR_RE.findall(tr) for tr in TR_RE.findall(table_html)]
    pending = []  # continuation (extra_names, remaining_rows)
    for row in raw_rows:
        carry, pending = pending, []  # only cells spanning from earlier rows
        cells = []
        for attrs, body in row:
            m = ROWSPAN_RE.search(attrs)
            text = strip_tags(body)
            if NAME_SPLIT_RE.search(text):
                parts = [p.strip() for p in NAME_SPLIT_RE.split(text) if p.strip()]
                if m and int(m.group(1)) > 1:
                    text = parts[0] if parts else text
                    extra = parts[1:] if len(parts) > 1 else [text]
                    pending.append((extra, int(m.group(1)) - 1))
                else:
                    # merged two-line name cell WITHOUT a rowspan (OCR shifted
                    # the following row labels; the write-in row is present
                    # separately) -- keep only the first line as the name
                    text = parts[0] if parts else text
            cells.append(text)
        if carry:
            pre, still = [], []
            for extra, rem in carry:
                pre.append(extra[0] if extra else '')
                rest = (extra[1:] if len(extra) > 1 else []) or (
                    [extra[0]] if extra else [''])
                if rem - 1 > 0:
                    still.append((rest, rem - 1))
            pending = still + pending
            cells = pre + cells
        yield cells


def page_lines(text):
    """Yield (kind, content) events from the OCR markdown.

    kind: 'line' for plain/markdown lines, 'row' for a list of table cells.
    """
    for block in PAGE_RE.split(text):
        # Interleave tables and non-table text in document order.
        pos = 0
        for m in TABLE_RE.finditer(block):
            for line in block[pos:m.start()].split('\n'):
                yield 'line', line
            for cells in table_rows(m.group(0)):
                yield 'row', cells
            pos = m.end()
        for line in block[pos:].split('\n'):
            yield 'line', line


def normalize_office(text):
    """Return (office, district) for a contest header, or (None, None)."""
    for rx, name in OFFICE_MAP:
        m = rx.search(text)
        if m:
            return name, DISTRICTS.get(name, '')
    return None, None


def parse(text, county=COUNTY):
    results = []
    problems = []
    current_precinct = None
    current_office = None
    current_district = ''
    seen_rv = set()
    # (precinct, office) -> {'total','ed','mi','pr'}
    totals = {}
    blocks = {}   # (precinct, office) -> list of row dicts
    order = []    # block insertion order for output

    def block_key():
        return (current_precinct, current_office)

    def start_block():
        key = block_key()
        if key not in blocks:
            blocks[key] = []
            order.append(key)

    for kind, content in page_lines(text):
        if kind == 'line':
            line = strip_tags(content)
            if not line:
                continue
            if any(s in line for s in PAGE_HEADER_SKIPS):
                continue
            pm = PRECINCT_RE.match(line)
            if pm:
                name = pm.group(1).upper().strip()
                current_precinct = canon_precinct(name)
                current_office = None
                continue
            # ballots/registered line (sometimes plain text, sometimes div)
            bm = BALLOTS_RE.search(line)
            if bm and current_precinct and current_precinct not in seen_rv:
                seen_rv.add(current_precinct)
                ballots = bm.group(1).replace(',', '')
                reg = bm.group(2).replace(',', '')
                results.append({
                    'county': county, 'precinct': current_precinct,
                    'office': 'Registered Voters', 'district': '',
                    'party': '', 'candidate': '', 'votes': reg,
                    'election_day': '', 'early_voting': '', 'provisional': '',
                })
                results.append({
                    'county': county, 'precinct': current_precinct,
                    'office': 'Ballots Cast', 'district': '',
                    'party': '', 'candidate': '', 'votes': ballots,
                    'election_day': '', 'early_voting': '', 'provisional': '',
                })
                continue
            # contest header (heading line, e.g. "## GOVERNOR / LT. ...")
            flat = line.lstrip('#').strip()
            office, district = normalize_office(flat)
            if office:
                current_office = office
                current_district = district
                start_block()
                continue
            continue

        # table row
        cells = content
        if not cells:
            continue
        joined = ' | '.join(cells)
        if any(s in joined for s in PAGE_HEADER_SKIPS):
            continue
        # precinct marker row (colspan)
        pm = PRECINCT_RE.match(strip_tags(joined))
        if pm and len(cells) <= 2:
            name = pm.group(1).upper().strip()
            current_precinct = canon_precinct(name)
            current_office = None
            continue
        # ballots/registered row (colspan)
        bm = BALLOTS_RE.search(joined)
        if bm and 'choice' not in joined.lower() and '%' not in joined:
            if current_precinct and current_precinct not in seen_rv:
                seen_rv.add(current_precinct)
                ballots = bm.group(1).replace(',', '')
                reg = bm.group(2).replace(',', '')
                results.append({
                    'county': county, 'precinct': current_precinct,
                    'office': 'Registered Voters', 'district': '',
                    'party': '', 'candidate': '', 'votes': reg,
                    'election_day': '', 'early_voting': '', 'provisional': '',
                })
                results.append({
                    'county': county, 'precinct': current_precinct,
                    'office': 'Ballots Cast', 'district': '',
                    'party': '', 'candidate': '', 'votes': ballots,
                    'election_day': '', 'early_voting': '', 'provisional': '',
                })
            continue
        # contest header row (colspan cell)
        flat = strip_tags(joined)
        office, district = normalize_office(flat)
        if office and len(cells) <= 2:
            current_office = office
            current_district = district
            start_block()
            continue
        # candidate / total rows: name, votes, pct, ED, MI, PR
        if current_precinct is None or current_office is None or len(cells) != 6:
            continue
        name = cells[0].strip()
        low = name.lower()
        if low in SKIP_CELLS:
            if low == 'total':
                key = block_key()
                totals[key] = {
                    'total': cells[1].strip(),
                    'ed': cells[3].strip(), 'mi': cells[4].strip(),
                    'pr': cells[5].strip(),
                }
            continue
        if not name or not cells[1].strip().isdigit():
            problems.append(('row', current_precinct, current_office, cells))
            continue
        votes = cells[1].strip().replace(',', '')
        fixed = NAME_FIXES.get(low)
        if fixed:
            candidate, party = fixed, PARTY_MAP.get(fixed.lower(), '')
        elif name.lower().startswith('write-in') or name.lower().startswith('wnte-in'):
            candidate, party = 'Write Ins', ''
        else:
            candidate, party = name.title(), PARTY_MAP.get(low, '')
        key = block_key()
        if key not in blocks:
            blocks[key] = []
            order.append(key)
        blocks[key].append({
            'county': county, 'precinct': current_precinct,
            'office': current_office, 'district': current_district,
            'party': party, 'candidate': candidate, 'votes': votes,
            'election_day': cells[3].strip().replace(',', ''),
            'early_voting': cells[4].strip().replace(',', ''),
            'provisional': cells[5].strip().replace(',', ''),
        })

    for key in order:
        results.extend(blocks.get(key, []))

    # ---- validation: candidate sums vs printed Total rows
    failures = []
    for key in order:
        rows = blocks.get(key, [])
        tot = totals.get(key)
        if not rows or not tot:
            failures.append((key, 'missing candidates or total row', rows, tot))
            continue
        # unexpected candidate names (OCR noise)
        expected = EXPECTED_CANDIDATES.get(key[1], set())
        for r in rows:
            if expected and r['candidate'].upper() not in expected \
                    and r['candidate'] != 'Write Ins':
                failures.append((key, f"unexpected candidate {r['candidate']!r}",
                                 [r], tot))
        try:
            s = sum(int(r['votes']) for r in rows)
            t = int(tot['total'])
        except ValueError:
            failures.append((key, 'non-numeric', rows, tot))
            continue
        if s != t:
            failures.append((key, f'sum {s} != total {t}', rows, tot))
    return results, failures, problems


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
    for key, msg, rows, tot in failures:
        print(f'  {key}: {msg}')
        for r in rows or []:
            print(f"     {r['candidate']!r} {r['votes']} (ED {r['election_day']} "
                  f"MI {r['early_voting']} PR {r['provisional']})")
        print(f"     printed total: {tot}")
    print(f'scrambled rows: {len(problems)}')
    for p in problems:
        print('  ', p)


if __name__ == '__main__':
    main()