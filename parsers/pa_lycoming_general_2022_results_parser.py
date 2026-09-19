#!/usr/bin/env python3
"""
Lycoming County, PA 2022 General Election parser.

Source: "Lycoming PA 2022 General Election Precinct Results.pdf"
(Electionware-style "Official Results by Precinct", same vendor layout as the
county's 2023 General report: one-line contest headers, candidate data lines
with a Vote % column and ED / MI / PR vote breakdowns):

    Precinct Anthony
       United States Senator (Vote for 1)
           John Fetterman                    84     16.90%        54         30        0
           Write-in                           0      0.00%         0          0        0
           Total                            497    100.00%       444         53        0

The report covers only the contests Lyoming's vendor export includes: United
States Senator, Governor and Lieutenant Governor, Representative in Congress
(9th / 15th District), Representative in the General Assembly (83rd / 84th
District) and the Montgomery Borough Question. No per-precinct
registered-voters / ballots-cast figures are printed, so no Registered Voters
/ Ballots Cast rows are emitted.

Contest data can span a page break (the Total line appears after the repeated
page header); page-header lines are skipped and the Total line still closes
the contest.

The MI (mail) breakdown column is mapped to `early_voting` per the 2022
output convention.

Usage: python pa_lycoming_general_2022_results_parser.py <input_pdf_or_txt> <output_csv>
"""

import csv
import re
import subprocess
import sys
import tempfile
from pathlib import Path

COUNTY = 'Lycoming'

PRECINCT_RE = re.compile(r'^Precinct\s+(.+)$')
CONTEST_RE = re.compile(r'^(.{2,140}?)\s*\(Vote for (\d+)\)$')
DATA_LINE_RE = re.compile(
    r'^(.+?)\s+(\d[\d,]*)\s+(\d+\.\d+)%\s+(\d[\d,]*)\s+(\d[\d,]*)\s+(\d[\d,]*)$'
)

SKIP_PREFIXES = (
    'Official Results by Precinct',
    'November 8, 2022 General Election',
    'Lycoming County',
    'All Precincts, All Districts',
    'Total Ballots Cast:',
    'Choice ',
)

SKIP_RES = (
    re.compile(r'^\d+ precincts reported out of'),  # "81 precincts reported out of 81 total"
)

STATEWIDE_OFFICE_MAP = {
    'United States Senator': ('U.S. Senate', ''),
    'Governor and Lieutenant Governor': ('Governor', ''),
}


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


def normalize_office(office_text):
    """(office, district) from a contest title."""
    office = office_text.strip()

    if office in STATEWIDE_OFFICE_MAP:
        return STATEWIDE_OFFICE_MAP[office]

    # "Representative in Congress (15th District)"
    ush = re.match(r'^Representative in Congress\s*\((\d+)(?:st|nd|rd|th)?\s+District\)$', office)
    if ush:
        return 'U.S. House', ush.group(1)

    # "Representative in the General Assembly (84th District)"
    sha = re.match(
        r'^Representative in the General Assembly\s*\((\d+)(?:st|nd|rd|th)?\s+District\)$',
        office)
    if sha:
        return 'State House', sha.group(1)

    # Local ballot questions: keep as printed
    return office, ''


def parse(text):
    results = []
    current_precinct = None
    current_office = None
    current_district = ''
    writein = None

    def flush_writein():
        nonlocal writein
        if writein and current_precinct and current_office:
            results.append({
                'county': COUNTY, 'precinct': current_precinct,
                'office': current_office, 'district': current_district, 'party': '',
                'candidate': 'Write Ins',
                'votes': str(writein['votes']), 'election_day': str(writein['ed']),
                'early_voting': str(writein['mi']), 'provisional': str(writein['pr']),
            })
        writein = None

    for raw in text.split('\n'):
        line = raw.strip()
        if not line:
            continue
        if any(line.startswith(p) for p in SKIP_PREFIXES):
            continue
        if any(r.match(line) for r in SKIP_RES):
            continue

        prec = PRECINCT_RE.match(line)
        if prec:
            flush_writein()
            current_precinct = prec.group(1).strip()
            current_office = None
            current_district = ''
            continue

        if not current_precinct:
            continue

        contest = CONTEST_RE.match(line)
        if contest:
            flush_writein()
            current_office, current_district = normalize_office(contest.group(1))
            continue

        if not current_office:
            continue

        if re.match(r'^Total\s', line):
            flush_writein()
            continue

        data = DATA_LINE_RE.match(line)
        if not data:
            continue

        name = data.group(1).strip()
        votes, ed, mi, pr = (data.group(2), data.group(4), data.group(5), data.group(6))
        if name.lower() == 'total':
            flush_writein()
            continue
        if name.lower() == 'write-in':
            if writein is None:
                writein = {'votes': 0, 'ed': 0, 'mi': 0, 'pr': 0}
            writein['votes'] += int(votes.replace(',', ''))
            writein['ed'] += int(ed.replace(',', ''))
            writein['mi'] += int(mi.replace(',', ''))
            writein['pr'] += int(pr.replace(',', ''))
            continue

        results.append({
            'county': COUNTY, 'precinct': current_precinct,
            'office': current_office, 'district': current_district, 'party': '',
            'candidate': name, 'votes': votes,
            'election_day': ed, 'early_voting': mi, 'provisional': pr,
        })
    flush_writein()
    return results


FIELDNAMES = ['county', 'precinct', 'office', 'district', 'party',
              'candidate', 'votes', 'election_day', 'early_voting', 'provisional']


def write_csv(results, output_path):
    with open(output_path, 'w', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=FIELDNAMES)
        w.writeheader()
        w.writerows(results)
    print(f'Wrote {len(results)} rows to {output_path}')


def main():
    if len(sys.argv) < 3:
        print('Usage: python pa_lycoming_general_2022_results_parser.py <input_pdf_or_txt> <output_csv>')
        sys.exit(1)
    text = extract_text(sys.argv[1])
    results = parse(text)
    write_csv(results, sys.argv[2])


if __name__ == '__main__':
    main()