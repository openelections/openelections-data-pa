#!/usr/bin/env python3
"""
Wayne County, PA 2023 General Election parser.

Source: "Wayne County Official Results by Precinct 2023 General.pdf"
(Dominion "Statement of Votes Cast by Geography", two-line contest headers).

Format (one block per precinct):
    Precinct BERLIN TOWNSHIP #1
       JUSTICE OF THE SUPREME COURT (Vote for 1)
       319 ballots (0 over voted ballots, 0 overvotes, 10 undervotes), 882 registered voters, turnout 36.17%
           Daniel McCaffery                   88      28.48%         55        33          0
           Write-in                            0       0.00%          0         0          0
           Total                             309     100.00%        257        51          1
           Overvotes                           0
           Undervotes                         10

Uses the shared sovc_geo_np line state machine (two-line Wayne variant), fed
with pdftotext -layout text. The last ~8 pages of the PDF are a per-contest
countywide rollup with no "All Precincts" marker line; the rollup is detected
(the first contest header repeated within the same precinct section) and
everything after it is dropped so its rows aren't misattributed to the last
precinct (WAYMART BOROUGH).

Usage: python pa_wayne_general_2023_results_parser.py <input_pdf_or_txt> <output_csv>
"""

import re
import sys
import tempfile
import subprocess
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from sovc_geo_np import SovcGeoConfig, process_lines  # noqa: E402

COUNTY = 'Wayne'

CONTEST_RE = re.compile(r'^(.{2,120}?)\s*\(Vote for (\d+)\)$')
BALLOTS_RE = re.compile(
    r'^(\d[\d,]*)\s+ballots\s*\(.*?\),\s*([\d,]+)\s+registered voters'
)

SKIP_PREFIXES = (
    'Statement of Votes Cast by Geography',
    'WAYNE COUNTY, NOVEMBER',
    'All Precincts, All Districts',
    'Total Ballots Cast:',
    'precincts reported out of',
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


def truncate_rollup(lines):
    """The trailing countywide rollup has no marker line. It begins with the
    first contest header whose title repeats within the current precinct
    section; drop everything from there on."""
    current_precinct = None
    seen_contests = set()
    out = []
    for line in lines:
        stripped = line.strip()
        prec = re.match(r'^Precinct\s+(.+)$', stripped)
        if prec:
            current_precinct = prec.group(1)
            seen_contests = set()
        elif current_precinct and CONTEST_RE.match(stripped):
            title = CONTEST_RE.match(stripped).group(1)
            if title in seen_contests:
                break
            seen_contests.add(title)
        out.append(line)
    return out


def normalize_office(office_text, precinct_muni):
    """Split 'OFFICE [TERM] - MUNICIPALITY' (or bare office, where the
    municipality is the precinct's) into (office, district)."""
    office = office_text.strip()
    district = ''

    # Contest titles carry "- MUNICIPALITY" suffixes in this report
    # (and always in the rollup section, which we truncate away).
    m = re.match(r'^(.*?)\s*-\s*([A-Z][A-Z\s]+?(?:TOWNSHIP|BOROUGH|REGION))\s*$', office)
    if m:
        office, muni = m.group(1).strip(), m.group(2).strip()
        district = muni.title()
    else:
        district = ''

    office = re.sub(r'\s+', ' ', office)

    # Title case, keeping known small words lowercase
    words = office.split()
    norm = []
    for w in words:
        if w.lower() in ('of', 'the', 'and', 'for'):
            norm.append(w.lower())
        elif w.isdigit():
            norm.append(w)
        else:
            norm.append(w.title() if not w.isupper() else w.title())
    office = ' '.join(norm)
    office = office.replace('Thecourts', 'the Courts')
    office = office.replace('County Commissioners', 'County Commissioner')
    office = office.replace('County Auditors', 'County Auditor')
    # Normalize retention contest naming ("...-PANELLA RETENTION" vs
    # "... - STABILE RETENTION")
    office = re.sub(r'\s*-\s*', ' - ', office)

    # Magisterial district / region numbers
    if not district:
        m = re.search(r'(\d+-\d+-\d+)', office)
        if m:
            district = m.group(1)
            office = re.sub(r'\s*' + re.escape(district), '', office).strip()

    m = re.search(r'REGION #(\d+)', office_text.upper())
    if m and district == '':
        district = f'Region {m.group(1)}'

    # Contests printed without a municipality suffix ("BOROUGH AUDITOR
    # 2 YEAR TERM", "TAX COLLECTOR 2 YEAR TERM") belong to the precinct's
    # own municipality.
    if not district and re.match(
            r'^(TOWNSHIP|BOROUGH)\s|^(TAX COLLECTOR|COUNCIL MEMBER|SUPERVISOR|MAYOR|'
            r'JUDGE OF ELECTION|INSPECTOR OF ELECTION|CONSTABLE)\b', office.upper()):
        district = precinct_muni

    # School director contests: office = "School Director [term]",
    # district = "<School District> [Region N]"
    sd = re.match(r'^(.*?)\s+SCHOOL DIRECTOR(?:\s+(.*))?$', office, re.IGNORECASE)
    if sd:
        dist = sd.group(1).strip().title()
        rest = (sd.group(2) or '').strip()
        region = re.search(r'REGION #?(\d+)', office_text.upper())
        if region:
            dist = re.sub(r'\s*REGION #?\d+\s*$', '', dist).strip()
            dist = f'{dist} Region {region.group(1)}'
        term = re.search(r'(FOUR|TWO) YEAR TERM', rest, re.IGNORECASE)
        if term:
            num = {'FOUR': '4', 'TWO': '2'}[term.group(1).upper()]
            office = f'School Director {num} Year Term'
        else:
            office = 'School Director'
        return office, dist

    # Generic "OFFICE - MUNICIPALITY" already split above; keep term suffixes
    office = office.replace('Year Term', 'Year Term')
    return office, district


def precinct_municipality(precinct):
    """'BERLIN TOWNSHIP #1' -> 'Berlin Township' (used for contests whose
    title has no municipality suffix)."""
    muni = re.sub(r'\s*#\d+$', '', precinct).strip()
    return muni.title()


def parse(text):
    lines = truncate_rollup(text.split('\n'))
    config = SovcGeoConfig(
        county=COUNTY,
        skip_prefixes=SKIP_PREFIXES,
        contest_re=CONTEST_RE,
        ballots_re=BALLOTS_RE,
        skip_over_undervotes=True,
        countywide_marker=None,
    )
    state = process_lines(lines, config)
    # normalize the one statewide candidate whose source spelling differs
    # from the repo standard (Bucks/York: 'Harry F. Smail, Jr.')
    name_fix = {'Harry F. Smail Jr.': 'Harry F. Smail, Jr.'}
    results = []
    for r in state.results:
        cand = 'Write-ins' if r['candidate'] == 'Write-in' else r['candidate']
        cand = name_fix.get(cand, cand)
        office_raw = r['office']
        if office_raw in ('Registered Voters', 'Ballots Cast'):
            office, district = office_raw, ''
        else:
            muni = precinct_municipality(r['precinct'])
            office, district = normalize_office(office_raw, muni)
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
    import csv
    with open(output_path, 'w', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=FIELDNAMES)
        w.writeheader()
        w.writerows(results)
    print(f'Wrote {len(results)} rows to {output_path}')


def main():
    if len(sys.argv) < 3:
        print('Usage: python pa_wayne_general_2023_results_parser.py <input_pdf_or_txt> <output_csv>')
        sys.exit(1)
    text = extract_text(sys.argv[1])
    results = parse(text)
    write_csv(results, sys.argv[2])


if __name__ == '__main__':
    main()