#!/usr/bin/env python3
"""
Bucks County, PA 2023 General Election parser.

Source: "Bucks County Certified Results Precinct Level 2023 General.pdf"
(Dominion "Statement of Votes Cast by Geography", one-line contest headers,
no Vote % column):

    Precinct Bedminster Twp East
       JUSTICE OF THE SUPREME COURT (Vote for 1), 2878 registered voters, turnout 51.74%
           Daniel McCaffery                   649        344       301         4
           Write-in                             1          1         0         0
           Total                             1479       1045       427         7

Data lines are "name votes ED MI PR" (4 numbers, no percentage column), so the
shared sovc_geo_np state machine is used with a custom data_line_re. Registered
Voters comes from the contest header; per-precinct Ballots Cast is derived as
round(registered * turnout / 100) (the report has no explicit per-precinct
ballots-cast figure).

The report ends with an "All Precincts" countywide rollup section (dropped via
countywide_marker). Within each precinct the two Superior Court retention
contests share the identical header "SUPERIOR COURT RETENTION - Jurisdiction
Wide"; a pre-pass renames the 2nd..Nth occurrence within a precinct to
"... #N" so the two seats stay distinct.

Usage: python pa_bucks_general_2023_results_parser.py <input_pdf_or_txt> <output_csv>
"""

import csv
import re
import subprocess
import sys
import tempfile
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from sovc_geo_np import SovcGeoConfig, process_lines  # noqa: E402

COUNTY = 'Bucks'

CONTEST_RE = re.compile(
    r'^(.{2,140}?)\s*\(Vote for (\d+)\),\s*([\d,]+)\s+registered voters,\s*turnout\s+([\d.]+)%$'
)
DATA_LINE_RE = re.compile(
    r'^(.+?)\s+(\d[\d,]*)\s+(\d[\d,]*)\s+(\d[\d,]*)\s+(\d[\d,]*)$'
)

SKIP_PREFIXES = (
    'Statement of Votes Cast by Geography',
    'Jurisdiction Wide, Municipal Election',
    'All Precincts, All Districts',
    'Total Ballots Cast:',
    'Choice ',
)


def extract_text(input_path: str) -> str:
    p = Path(input_path)
    if p.suffix.lower() == '.txt':
        return p.read_text(encoding='utf-8')
    with tempfile.NamedTemporaryFile(mode='w+', suffix='.txt', delete=False) as tmp:
        tmp_path = tmp.name
    try:
        subprocess.run(['pdftotext', '-layout', str(p), tmp_path],
                       check=True, capture_output=True)
        return Path(tmp_path).read_text(encoding='utf-8')
    finally:
        Path(tmp_path).unlink(missing_ok=True)


def dedupe_contest_titles(lines):
    """Rename repeated contest headers within one precinct block (Bucks prints
    the two Superior Court retention seats under identical headers) by
    appending ' #N' (N = 1-based occurrence) to every occurrence, so the seats
    stay distinct and consistently named."""
    def base_title(title):
        return re.sub(r'\s+#\d+\s*$', '', title)

    # Pass 1: count occurrences of each base title per precinct block.
    current_precinct = None
    counts = Counter()
    totals = Counter()
    for line in lines:
        stripped = line.strip()
        prec = re.match(r'^Precinct\s+(.+)$', stripped)
        if prec:
            current_precinct = prec.group(1)
        elif stripped == 'All Precincts':
            current_precinct = None
        elif current_precinct:
            m = CONTEST_RE.match(stripped)
            if m:
                totals[(current_precinct, base_title(m.group(1)))] += 1

    # Pass 2: tag every occurrence of a repeated title with its 1-based index.
    current_precinct = None
    counts = Counter()
    out = []
    for line in lines:
        stripped = line.strip()
        prec = re.match(r'^Precinct\s+(.+)$', stripped)
        if prec:
            current_precinct = prec.group(1)
            counts = Counter()
        elif stripped == 'All Precincts':
            current_precinct = None
            counts = Counter()
        elif current_precinct:
            m = CONTEST_RE.match(stripped)
            if m:
                base = base_title(m.group(1))
                if totals[(current_precinct, base)] > 1:
                    counts[base] += 1
                    new = re.sub(r'\s*\(Vote for',
                                 f' #{counts[base]} (Vote for', stripped, count=1)
                    line = line.replace(stripped, new, 1)
        out.append(line)
    return out


def _titlecase(s):
    words = []
    for w in s.split():
        if w.lower() in ('of', 'the', 'and', 'for'):
            words.append(w.lower())
        else:
            words.append(w.title())
    return ' '.join(words)


def normalize_office(office_text):
    """(office, district) from a contest title like
    'BRISTOL TOWNSHIP SCHOOL DIRECTOR - 4 YEAR' or
    'MAGISTERIAL DISTRICT JUDGE 07-2-01'."""
    office = office_text.strip()

    # Retention seat suffix added by dedup pass: "... - Jurisdiction Wide #2"
    seat = ''
    m = re.search(r'\s#(\d+)(?=\s|$)', office)
    if m:
        seat = f' #{m.group(1)}'
        office = re.sub(r'\s+#\d+\s*', ' ', office).strip()

    district = ''
    # Municipality suffix "- Jurisdiction Wide" on retention titles
    office = re.sub(r'\s*-\s*Jurisdiction Wide\s*$', '', office, flags=re.IGNORECASE)

    # Magisterial District Judge <code>
    mdj = re.match(r'^MAGISTERIAL DISTRICT JUDGE\s+([\d-]+)$', office)
    if mdj:
        return 'Magisterial District Judge', mdj.group(1)

    # Referendum: "BENSALEM TOWNSHIP REFERENDUM"
    ref = re.match(r'^(.+?)\s+REFERENDUM$', office)
    if ref:
        return 'Referendum', _titlecase(ref.group(1))

    # "<NAME> SCHOOL DIRECTOR [REGION N] - <T> YEAR"
    sd = re.match(
        r'^(.+?)\s+SCHOOL DIRECTOR(?:\s+REGION\s+(\d+))?\s*-\s*(\d+)\s+YEAR$', office)
    if sd:
        dist = _titlecase(sd.group(1))
        if sd.group(2):
            dist += f' Region {sd.group(2)}'
        return f'School Director {sd.group(3)} Year Term', dist

    # "<MUNI> <OFFICE> - <T> YEAR"
    loc = re.match(
        r'^(.+?(?:TOWNSHIP|BOROUGH))\s+(SUPERVISOR|AUDITOR|TAX COLLECTOR|COUNCIL|MAYOR|'
        r'CONSTABLE|JUDGE OF ELECTION|INSPECTOR OF ELECTION)\s*-\s*(\d+)\s+YEAR$', office)
    if loc:
        muni = _titlecase(loc.group(1))
        off = loc.group(2).title().replace('Of Election', 'of Election')
        return f'{off} {loc.group(3)} Year Term', muni

    # Fall back: split any trailing "- <N> YEAR" and title-case
    term = re.search(r'\s*-\s*(\d+)\s+YEAR$', office)
    if term:
        office = re.sub(r'\s*-\s*\d+\s+YEAR$', '', office)
        return f'{_titlecase(office)} {term.group(1)} Year Term' + seat, ''
    return _titlecase(office) + seat, ''


def parse(text):
    lines = dedupe_contest_titles(text.split('\n'))
    config = SovcGeoConfig(
        county=COUNTY,
        skip_prefixes=SKIP_PREFIXES,
        contest_re=CONTEST_RE,
        ballots_re=None,
        data_line_re=DATA_LINE_RE,
        countywide_marker='All Precincts',
    )
    state = process_lines(lines, config)
    results = []
    for r in state.results:
        cand = 'Write-ins' if r['candidate'] == 'Write-in' else r['candidate']
        office_raw = r['office']
        if office_raw in ('Registered Voters', 'Ballots Cast'):
            office, district = office_raw, ''
        else:
            office, district = normalize_office(office_raw)
        results.append({
            'county': COUNTY,
            'precinct': r['precinct'],
            'office': office,
            'district': district,
            'party': '',
            'candidate': cand,
            'votes': r['votes'],
            'election_day': r['election_day'],
            'mail': r['mail'],
            'provisional': r['provisional'],
        })
    return results


FIELDNAMES = ['county', 'precinct', 'office', 'district', 'party',
              'candidate', 'votes', 'election_day', 'mail', 'provisional']


def write_csv(results, output_path):
    with open(output_path, 'w', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=FIELDNAMES)
        w.writeheader()
        w.writerows(results)
    print(f'Wrote {len(results)} rows to {output_path}')


def main():
    if len(sys.argv) < 3:
        print('Usage: python pa_bucks_general_2023_results_parser.py <input_pdf_or_txt> <output_csv>')
        sys.exit(1)
    text = extract_text(sys.argv[1])
    results = parse(text)
    write_csv(results, sys.argv[2])


if __name__ == '__main__':
    main()