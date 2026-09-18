#!/usr/bin/env python3
"""
Lycoming County, PA 2023 General Election parser.

Source: "Lycoming County Precinct Results 2023 General.pdf"
(Electionware-style "Official Results by Precinct", one-line contest headers
with NO registered-voters/turnout text, candidate data lines with a Vote %
column):

    Precinct Anthony Township
       Justice of the Supreme Court (Vote for 1)
           Daniel McCaffery                          46    17.10%        29        17         0
           Write-in                                   0     0.00%         0         0         0
           Total                                    269   100.00%       239        30         0

The report has no per-precinct registered-voters / ballots-cast figures, so no
Registered Voters / Ballots Cast rows are emitted (the source simply doesn't
report them at precinct level).

Contest titles like "Old Lycoming Supervisor (4yr)", "Loyalsock SD" or
"Magisterial District Judge 29-3-02" are split into office + district.

Usage: python pa_lycoming_general_2023_results_parser.py <input_pdf_or_txt> <output_csv>
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
    'November 7, 2023 Municipal Election',
    'Lycoming County',
    'All Precincts, All Districts',
    'Total Ballots Cast:',
    'precincts reported out of',
    'Choice ',
)

LOCAL_OFFICE_RE = re.compile(
    r'^(.+?)\s+(Supervisor|Auditor|Tax Collector|Council|Mayor|Controller|Treasurer|'
    r'Judge of Elections|Inspector of Elections|Constable|Assessor)\b(.*)$'
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


def _titlecase(s):
    words = []
    for w in s.split():
        if w.lower() in ('of', 'the', 'and', 'for'):
            words.append(w.lower())
        else:
            words.append(w.title())
    return ' '.join(words)


def _term_suffix(remainder):
    """'(4yr)' -> ' 4 Year Term'."""
    m = re.search(r'\((\d+)yr\)', remainder or '')
    if m:
        return f' {m.group(1)} Year Term'
    return ''


def normalize_office(office_text):
    """(office, district) from a contest title."""
    office = office_text.strip()
    district = ''

    # Magisterial District Judge <code>
    mdj = re.match(r'^Magisterial District Judge\s+([\d-]+)$', office)
    if mdj:
        return 'Magisterial District Judge', mdj.group(1)

    # Retention questions: "Superior Retention 1", "Supreme Retention 2", ...
    ret = re.match(r'^(Superior|Supreme|Common Pleas|Commonwealth)\s+Retention\s*(\d*)$', office, re.IGNORECASE)
    if ret:
        label = {'Superior': 'Superior Court', 'Supreme': 'Supreme Court',
                 'Common Pleas': 'Court of Common Pleas',
                 'Commonwealth': 'Commonwealth Court'}[ret.group(1).title()]
        seat = f' {ret.group(2)}' if ret.group(2) else ''
        return f'{label} Retention{seat}', ''

    # School director: "<NAME> SD [region] [(2yr)]"
    sd = re.match(r'^(.+?)\s+SD\s*(\d*|Boro)\b(.*)$', office)
    if sd:
        dist = sd.group(1).strip()
        region = sd.group(2)
        if region:
            dist += f' {region}'
        office_out = 'School Director' + _term_suffix(sd.group(3))
        return office_out, dist

    # Generic "<MUNICIPALITY> <OFFICE> [term suffix]":
    loc = LOCAL_OFFICE_RE.match(office)
    if loc:
        muni = loc.group(1).strip()
        if muni.lower() == 'county':
            # "County Controller", "County Treasurer": countywide office
            return _titlecase(f'{muni} {loc.group(2)}'.strip()), ''
        off = loc.group(2).strip()
        rest = (loc.group(3) or '').strip()
        term = _term_suffix(rest)
        rest_clean = re.sub(r'\s*\(\d+yr\)', '', rest).strip()
        parts = [off]
        if rest_clean:
            parts.append(rest_clean)
        return ' '.join(parts) + term, muni

    return _titlecase(office), ''


def parse(text):
    results = []
    current_precinct = None
    current_office = None
    writein = None

    def flush_writein():
        nonlocal writein
        if writein and current_precinct and current_office:
            office, district = normalize_office(current_office)
            results.append({
                'county': COUNTY, 'precinct': current_precinct,
                'office': office, 'district': district, 'party': '',
                'candidate': 'Write-ins',
                'votes': str(writein['votes']), 'election_day': str(writein['ed']),
                'mail': str(writein['mi']), 'provisional': str(writein['pr']),
            })
        writein = None

    for raw in text.split('\n'):
        line = raw.strip()
        if not line:
            continue
        if any(line.startswith(p) for p in SKIP_PREFIXES):
            continue

        prec = PRECINCT_RE.match(line)
        if prec:
            flush_writein()
            current_precinct = prec.group(1).strip()
            current_office = None
            continue

        if not current_precinct:
            continue

        contest = CONTEST_RE.match(line)
        if contest:
            flush_writein()
            current_office = contest.group(1).strip()
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
        if name == 'Write-in':
            if writein is None:
                writein = {'votes': 0, 'ed': 0, 'mi': 0, 'pr': 0}
            writein['votes'] += int(votes)
            writein['ed'] += int(ed)
            writein['mi'] += int(mi)
            writein['pr'] += int(pr)
            continue

        office, district = normalize_office(current_office)
        results.append({
            'county': COUNTY, 'precinct': current_precinct,
            'office': office, 'district': district, 'party': '',
            'candidate': name, 'votes': votes,
            'election_day': ed, 'mail': mi, 'provisional': pr,
        })
    flush_writein()
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
        print('Usage: python pa_lycoming_general_2023_results_parser.py <input_pdf_or_txt> <output_csv>')
        sys.exit(1)
    text = extract_text(sys.argv[1])
    results = parse(text)
    write_csv(results, sys.argv[2])


if __name__ == '__main__':
    main()