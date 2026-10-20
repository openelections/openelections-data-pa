#!/usr/bin/env python3
"""
Parser for Indiana County, PA 2022 General Primary (May 17, 2022) precinct results.

Source: "Indiana PA May-17-2022-Precint-by-Precint-Official.pdf" (Electionware
"Summary Results Report / Precinct Summary", OFFICIAL RESULTS, generated
06/06/2022 -- i.e. the post-canvass/recount official file).

Family: electionware_regex / electionware_text look-alike (same vendor report the
2025-general Indiana parser targets), but the 2022 PRIMARY format differs enough
that a standalone parser was written:
  - contest headers carry the ballot party ("DEM UNITED STATES SENATOR"), while
    candidate rows do NOT carry a party prefix;
  - Statistics carry party-level Registered Voters and Ballots Cast rows;
  - the Absentee/Mail-in column maps to `mail`, and there is an extra
    `provisional` column.

The report is parsed from `pdftotext -layout` text (the Electionware report has
a page-break quirk: the four Ballots Cast number columns print on the line
BEFORE the label; handled with a pending-numbers queue).

Output: OpenElections 2022 primary format,
    county,precinct,office,district,party,candidate,votes,election_day,mail,provisional
with Write-Ins rows carrying the contest party (primary requirement).

Usage:
    uv run python parsers/pa_indiana_primary_2022_results_parser.py <input_pdf> <output_csv>
"""

import csv
import re
import subprocess
import sys
import tempfile
from pathlib import Path

COUNTY = 'Indiana'

RV_RE = re.compile(r'^Registered Voters - (?P<kind>Total|DEMOCRATIC|REPUBLICAN|NONPARTISAN)\s+([\d,]+)\s*$')
BC_LABEL_RE = re.compile(r'^Ballots Cast - (?P<kind>Total|DEMOCRATIC|REPUBLICAN|NONPARTISAN|Blank)\s*$')
NUMS_ONLY_RE = re.compile(r'^\s{5,}(?P<n0>[\d,]+)\s+(?P<n1>[\d,]+)\s+(?P<n2>[\d,]+)\s+(?P<n3>[\d,]+)\s*$')
DATA_RE = re.compile(
    r'^(?P<name>.*?)\s{2,}(?P<v>[\d,]+)\s+([\d,]+)\s+([\d,]+)\s+([\d,]+)\s*$'
)
OFFICE_RE = re.compile(r'^(?P<party>DEM|REP)\s(?P<office>[A-Z][A-Z0-9 #/\-&.,\']*)$')
VOTE_FOR_RE = re.compile(r'^Vote For (?P<n>\d+)$')

SKIP_NAMES = {
    'Total Votes Cast', 'Overvotes', 'Undervotes', 'Contest Totals', 'Not Assigned',
}
PAGE_SKIP = (
    'Summary Results Report', 'OFFICIAL RESULTS', 'General Primary Election',
    'May 17, 2022', 'Precinct Summary', 'Election Summary', 'Report generated',
    'STATISTICS', 'Vote For', 'TOTAL', 'Election', 'Absentee', 'Provisional',
    'Day', 'Mail-in', 'Registered Voters - Total',
)

# RV per-party rows get a party code; "Total" rows keep party empty.
# Special statistic values go in the OFFICE column, candidate empty
# (CLAUDE.md special-office values; Beaver/McKean 2022-primary precedent).
PARTY_CODE = {'Total': '', 'DEMOCRATIC': 'DEM', 'REPUBLICAN': 'REP', 'NONPARTISAN': 'NONPARTISAN'}


def title_case(name):
    """ARMARGH -> Armagh; INDIANA #3/1 -> Indiana #3/1."""
    def cap_word(w):
        if re.fullmatch(r'[\d#/\-]+', w):
            return w
        return w.capitalize()
    return ' '.join(cap_word(w) for w in name.split())


DISTRICT_RE = re.compile(r'\b(\d+)(?:ST|ND|RD|TH)\s+DISTRICT\b')


def normalize_office(party, raw):
    """Map the printed contest title to (office, district). raw has no party prefix."""
    t = raw.strip()
    m = DISTRICT_RE.search(t)
    district = str(int(m.group(1))) if m else ''

    if 'UNITED STATES SENATOR' in t:
        return 'U.S. Senate', district
    if 'REPRESENTATIVE IN CONGRESS' in t:
        return 'U.S. House', district
    if 'GENERAL ASSEMBLY' in t:
        # "Representative in the General Assembly" -> State House (2020 precedent)
        return 'State House', district
    if t == 'GOVERNOR':
        return 'Governor', ''
    if 'LIEUTENANT GOVERNOR' in t:
        return 'Lieutenant Governor', ''
    if 'STATE COMMITTEE' in t:
        # "MEMBER OF THE DEMOCRATIC/REPUBLICAN STATE COMMITTEE"
        which = 'Democratic' if 'DEMOCRATIC' in t else 'Republican'
        return f'Member of {which} State Committee', district
    if 'COUNTY COMMITTEE' in t:
        which = 'Democratic' if 'DEMOCRATIC' in t else 'Republican'
        seat = t.replace('DEMOCRATIC COUNTY COMMITTEE', '').replace('REPUBLICAN COUNTY COMMITTEE', '')
        seat = (' ' + title_case(seat.strip())) if seat.strip() else ''
        return f'{which} County Committee{seat}', district
    # local office as printed
    return title_case(t), district


def clean(v):
    return v.replace(',', '').strip()


def parse_lines(text, results):
    """Parse one page's lines; append result rows to `results`."""
    lines = text.split('\n')
    state = {'precinct': None, 'office': None, 'district': '', 'party': ''}
    pending_nums = []  # Ballots Cast 4-number columns that print ABOVE their label
    stats_seen = {}

    # find page header county line, then precinct name (first non-blank line;
    # blank-line counts vary between pages, and later lines in the window can be
    # contest headers, e.g. "LIQUOR LICENSE REFERENDUM ARMSTRONG")
    for i, raw in enumerate(lines):
        if 'Indiana County' in raw:
            for j in range(i + 1, min(i + 6, len(lines))):
                cand = lines[j].strip()
                if not cand or re.fullmatch(r'[\d,\s]+', cand):
                    continue
                state['precinct'] = title_case(cand)
                break
            break

    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        if 'Indiana County' in raw or 'May 17, 2022' in raw:
            continue

        if any(line.startswith(p) for p in PAGE_SKIP) and not \
           (line.startswith('Registered Voters') or line.startswith('Ballots Cast')):
            continue

        # ---- Statistics: Registered Voters (label + single number on one line)
        m = RV_RE.match(line)
        if m:
            kind = m.group('kind')
            key = ("RV", kind)
            if key not in stats_seen:
                stats_seen[key] = True
                results.append({
                    'county': COUNTY, 'precinct': state['precinct'],
                    # stats labels go in the office column, candidate empty
                    # (matches the 2022-primary files that pass missing_values)
                    'office': 'Registered Voters', 'district': '',
                    'party': PARTY_CODE[kind],
                    'candidate': '',
                    'votes': clean(m.group(2)), 'election_day': '', 'mail': '', 'provisional': '',
                })
            continue

        # ---- Statistics: Ballots Cast numbers print one line ABOVE the label
        m = NUMS_ONLY_RE.match(raw)
        if m:
            pending_nums.append(tuple(m.group(k) for k in ('n0', 'n1', 'n2', 'n3')))
            continue
        m = BC_LABEL_RE.match(line)
        if m:
            kind = m.group('kind')
            if not pending_nums:
                sys.exit(f'ERROR: Ballots Cast label with no preceding numbers: {line!r}')
            nums = pending_nums.pop(0)
            key = ('BC', kind)
            if key not in stats_seen:
                stats_seen[key] = True
                office = {'Total': 'Ballots Cast', 'Blank': 'Ballots Cast - Blank'}.get(
                    kind, 'Ballots Cast')
                results.append({
                    'county': COUNTY, 'precinct': state['precinct'],
                    'office': office, 'district': '',
                    'party': PARTY_CODE.get(kind, ''),
                    'candidate': '',
                    'votes': clean(nums[0]), 'election_day': clean(nums[1]),
                    'mail': clean(nums[2]), 'provisional': clean(nums[3]),
                })
            continue

        if '%' in line:
            continue

        # ---- Contest header (party-prefixed)
        m = OFFICE_RE.match(line)
        if m and not DATA_RE.match(line):
            office, district = normalize_office(m.group('party'), m.group('office'))
            state['office'], state['district'], state['party'] = office, district, m.group('party')
            continue

        # ---- Contest header without party prefix (e.g. LIQUOR LICENSE REFERENDUM
        # ARMSTRONG) — all-caps, has no numbers, and is not the precinct name
        if (line == line.upper() and line == line.strip()
                and re.match(r'^[A-Z0-9 #/\-\',.&]+$', line)
                and not DATA_RE.match(line)
                and not RV_RE.match(line) and not BC_LABEL_RE.match(line)
                and state['precinct'] and line != state['precinct']):
            office, district = normalize_office('', line)
            state['office'], state['district'], state['party'] = office, district, ''
            continue

        # ---- Data rows
        m = DATA_RE.match(line)
        if not m:
            # lone number line at page bottom (a data row split by the printer) -- warn
            if re.fullmatch(r'[\d,\s]+', line):
                print(f'WARN unassigned numbers line: {raw!r}')
            continue
        name = m.group('name').strip()
        if not name or name in SKIP_NAMES or name.startswith('Write-In:'):
            continue

        if not state['office'] or not state['precinct']:
            print(f'WARN data row with no contest context: {raw!r}')
            continue

        # DATA_RE groups(): 1=name, 2=votes, 3=election_day, 4=mail, 5=provisional
        votes = clean(m.group('v'))
        ed, mail_v, prov = (clean(m.group(k)) for k in (3, 4, 5))

        if name == 'Write-In Totals':
            candidate = 'Write Ins'
        else:
            candidate = name

        results.append({
            'county': COUNTY, 'precinct': state['precinct'],
            'office': state['office'], 'district': state['district'],
            'party': state['party'], 'candidate': candidate,
            'votes': votes, 'election_day': ed, 'mail': mail_v, 'provisional': prov,
        })


def merge_write_ins(results):
    """The DEM County Committee prints one section per seat (two sections per
    precinct, 1-seat each). Candidate rows differ, but both sections emit a
    'Write-In Totals' row, which would produce duplicate/ambiguous 'Write Ins'
    rows. Aggregate them per (precinct, office, district, party)."""
    merged = []
    seen = {}
    for r in results:
        if r['candidate'] == 'Write Ins':
            key = (r['precinct'], r['office'], r['district'], r['party'])
            if key in seen:
                row = seen[key]
                for f in ('votes', 'election_day', 'mail', 'provisional'):
                    row[f] = str(int(row[f] or 0) + int(r[f] or 0))
                continue
            seen[key] = r
        merged.append(r)
    return merged


def main():
    if len(sys.argv) != 3:
        print(__doc__)
        sys.exit(1)
    pdf_path, output_path = sys.argv[1], sys.argv[2]
    if not Path(pdf_path).exists():
        sys.exit(f'ERROR: PDF not found: {pdf_path}')

    with tempfile.TemporaryDirectory() as td:
        txt_path = str(Path(td) / 'precinct.txt')
        subprocess.run(['pdftotext', '-layout', pdf_path, txt_path], check=True)
        text = Path(txt_path).read_text()

    pages = text.split('\f')
    results = []
    for page in pages:
        parse_lines(page, results)
    results = merge_write_ins(results)

    with open(output_path, 'w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=[
            'county', 'precinct', 'office', 'district', 'party', 'candidate',
            'votes', 'election_day', 'mail', 'provisional'])
        writer.writeheader()
        writer.writerows(results)

    print(f'Parsed {len(pages)} pages -> {len(results)} rows -> {output_path}')


if __name__ == '__main__':
    main()