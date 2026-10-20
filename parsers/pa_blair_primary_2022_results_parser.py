#!/usr/bin/env python3
"""
Parser for Blair County, PA 2022 General Primary (May 17, 2022) precinct results.

Sources:
  - "Blair PA 2022 Primary Official Precinct Results.pdf" (Electionware
    "Precinct Summary Results Group Report", OFFICIAL RESULTS, generated
    09/23/2022) -- all contests for all 93 precincts, but its Republican
    U.S. Senate race is PRE-recount.
  - "Blair PA 2022 Primary Official Precinct Results -RECOUNT.pdf" (same
    vendor/report, 09/23/2022 9:57 AM) -- full contest list but only the
    Republican U.S. Senate race carries real (POST-recount) values; every
    other contest is zeroed. Per the June 2022 mandatory recount (Oz vs
    McCormick), the GOP U.S. Senate rows are taken from this file.

Family: electionware (Precinct Summary Group Report) look-alike; the 2022
PRIMARY format differs enough from the 2025-general Electionware format that a
standalone parser is written (following the Indiana primary precedent):
  - contest headers carry the ballot party ("DEM UNITED STATES SENATOR",
    "REP GOVERNOR", "DEM DEMOCRATIC COUNTY COMMITTEE ALTOONA 1"); candidate
    rows carry NO party prefix;
  - Statistics carry per-party Registered Voters / Ballots Cast rows
    (emitted with a party code, Indiana/Beaver/McKean 2022-primary precedent);
  - columns are Total / Election Day / Mail / Provisional.

The precinct name is the ALL-CAPS line immediately above each "Statistics"
marker (same strategy as the shared electionware engine's block extractor);
Electionware repeats precinct name, office header and "Vote For" at page
breaks, which this state machine tolerates naturally.

Output: OpenElections 2022 primary format,
    county,precinct,office,district,party,candidate,votes,election_day,mail,provisional
with Write Ins rows carrying the contest party (primary requirement).

Usage:
    uv run python parsers/pa_blair_primary_2022_results_parser.py <input_pdf> <output_csv>

Optional: --senate-from <recount_pdf> replaces the Republican U.S. Senate rows
with the (post-recount) values parsed from the recount report.
"""

import csv
import copy
import re
import subprocess
import sys
import tempfile
from pathlib import Path

COUNTY = 'Blair'

RVP_LABEL_RE = re.compile(
    r'^Registered Voters - (?P<kind>Total|Democratic|Republican|Nonpartisan|'
    r'DEMOCRATIC|REPUBLICAN|NONPARTISAN|Total)\s+([\d,]+)\s*$')
BC_LABEL_RE = re.compile(
    r'^Ballots Cast - (?P<kind>Total|Democratic|Republican|Nonpartisan|Blank|'
    r'DEMOCRATIC|REPUBLICAN|NONPARTISAN)\s+'
    r'(?P<v>[\d,]+)\s+(?P<ed>[\d,]*)\s+(?P<mail>[\d,]*)\s+(?P<prov>[\d,]*)\s*$')
DATA_RE = re.compile(
    r'^(?P<name>.*?)\s{2,}(?P<v>[\d,]+)\s+(?P<ed>[\d,]+)\s+(?P<mail>[\d,]+)\s+(?P<prov>[\d,]+)\s*$')
OFFICE_RE = re.compile(r'^(?P<party>DEM|REP)\s(?P<office>[A-Z][A-Z0-9 #/\-&.,\']*)$')
VOTE_FOR_RE = re.compile(r'^Vote For (?P<n>\d+)$')
STATS_HEADER_RE = re.compile(r'^Statistics\b')
DISTRICT_RE = re.compile(r'\b(\d+)(?:ST|ND|RD|TH)\s+DISTRICT\b')

SKIP_NAMES = {'Total Votes Cast', 'Overvotes', 'Undervotes', 'Contest Totals',
              'Not Assigned'}

PARTY_CODE = {'Total': '', 'Democratic': 'DEM', 'Republican': 'REP',
              'DEMOCRATIC': 'DEM', 'REPUBLICAN': 'REP',
              'Nonpartisan': 'NONPARTISAN', 'NONPARTISAN': 'NONPARTISAN'}

# Lines never considered as precinct names and always skipped.
SKIP_PREFIXES = (
    'Precinct Summary Results Group Report',
    'Precinct_Summary',
    'Precinct Summary',
    'Report generated with Electionware',
    'Statistics',
    'TOTAL',
    'Vote For',
    'GENERAL PRIMARY ELECTION',
    'GENERAL ELECTION',
    'MAY 17, 2022',
    'BLAIR COUNTY, PENNSYLVANIA',
    'OFFICIAL RESULTS',
)


def title_case(name):
    """ALTOONA -> Altoona; BELLWOOD BOR -> Bellwood Bor; ALTOONA 10 DIV 1 ->
    Altoona 10 Div 1; DAVE MCCORMICK -> Dave McCormick. Matches the Blair
    general file's precinct spelling and the repository's Mc-aware candidate
    capitalization."""
    words = []
    for w in name.split():
        if re.fullmatch(r'[\d#/\-]+', w):
            words.append(w)
        elif len(w) >= 3 and w.upper().startswith('MC') and w.isupper():
            # MCCORMICK -> McCormick
            words.append('Mc' + w[2:].capitalize())
        else:
            words.append(w.capitalize())
    return ' '.join(words)


def normalize_office(party, raw):
    """Map the printed contest title (party prefix already stripped) to
    (office, district)."""
    t = raw.strip()
    m = DISTRICT_RE.search(t)
    district = str(int(m.group(1))) if m else ''

    if 'UNITED STATES SENATOR' in t:
        return 'U.S. Senate', district
    if 'REPRESENTATIVE IN CONGRESS' in t:
        return 'U.S. House', district
    if t.startswith(('SENATOR IN THE GENERAL ASSEMBLY', 'SENATOR')):
        if 'GENERAL ASSEMBLY' in t:
            return 'State Senate', district
    if 'REPRESENTATIVE IN THE GENERAL ASSEMBLY' in t:
        return 'State House', district
    if t == 'GOVERNOR':
        return 'Governor', ''
    if 'LIEUTENANT GOVERNOR' in t:
        return 'Lieutenant Governor', ''
    if 'STATE COMMITTEE' in t:
        which = 'Democratic' if 'DEMOCRATIC' in t else 'Republican'
        return f'Member of {which} State Committee', district
    if 'COUNTY COMMITTEE' in t:
        which = 'Democratic' if 'DEMOCRATIC' in t else 'Republican'
        seat = t.replace('DEMOCRATIC COUNTY COMMITTEE', '')
        seat = re.sub(r'\s+', ' ', seat.replace('REPUBLICAN COUNTY COMMITTEE', ''))
        seat = (' ' + title_case(seat.strip())) if seat.strip() else ''
        return f'{which} County Committee{seat}', district
    _ = party  # party is carried separately on every row
    # local/judicial office as printed
    return title_case(t), district


def parse_lines(text, results, precincts_seen, state):
    """Parse one page's lines; append result rows to `results`."""
    lines = text.split('\n')
    stats_keys = {}
    for idx, raw in enumerate(lines):
        line = raw.strip()
        if not line:
            continue

        # ---- New precinct: name line(s) directly above the Statistics marker
        if STATS_HEADER_RE.match(line):
            # Walk back over page headers/footers to the ALL-CAPS precinct name.
            name = None
            for j in range(idx - 1, -1, -1):
                s = lines[j].strip()
                if not s:
                    continue
                if STATS_HEADER_RE.match(s):
                    break  # hit the previous precinct's Statistics block
                if any(s.startswith(p) for p in SKIP_PREFIXES):
                    continue
                if re.search(r'\d+\s*of\s*[\d,]+$', s):
                    continue
                if 'Copyright' in s:
                    continue
                name = s
                break
            if name is None:
                sys.exit(f'ERROR: no precinct name found above Statistics: {raw!r}')
            state['precinct'] = title_case(name)
            state['office'] = None
            state['party'] = ''
            state['district'] = ''
            state['stats_done'] = False
            stats_keys = {}
            precincts_seen.add(state['precinct'])
            continue

        if not state['precinct']:
            continue  # header/footer lines before the first precinct section

        if 'BLAIR COUNTY, PENNSYLVANIA' in line or 'May 17, 2022' in line or \
                'May 17, 2022' in raw:
            continue
        if any(line.startswith(p.rstrip()) for p in SKIP_PREFIXES if p) and \
                not line.startswith(('Registered Voters', 'Ballots Cast')):
            continue
        if '%' in line.split()[-1]:
            continue

        # ---- Statistics: Registered Voters (label + single number, same line)
        m = RVP_LABEL_RE.match(line)
        if m:
            kind = m.group('kind')
            key = ('RV', kind)
            if not state['stats_done']:
                results.append({
                    'county': COUNTY, 'precinct': state['precinct'],
                    'office': 'Registered Voters', 'district': '',
                    'party': PARTY_CODE[kind], 'candidate': '',
                    'votes': m.group(2).replace(',', ''),
                    'election_day': '', 'mail': '', 'provisional': '',
                })
            continue

        # ---- Statistics: Ballots Cast (label + 4 numbers, same line)
        m = BC_LABEL_RE.match(line)
        if m and not state['stats_done']:
            kind = m.group('kind')
            office = 'Ballots Cast - Blank' if kind == 'Blank' else 'Ballots Cast'
            results.append({
                'county': COUNTY, 'precinct': state['precinct'],
                'office': office, 'district': '',
                'party': PARTY_CODE.get(kind, ''),
                'candidate': '',
                'votes': m.group('v').replace(',', ''),
                'election_day': m.group('ed').replace(',', ''),
                'mail': m.group('mail').replace(',', ''),
                'provisional': m.group('prov').replace(',', ''),
            })
            continue

        if '%' in line:
            continue

        # ---- Contest header (party-prefixed, repeated at page breaks)
        m = OFFICE_RE.match(line)
        if m and not DATA_RE.match(line):
            office, district = normalize_office(m.group('party'), m.group('office'))
            state['office'], state['district'], state['party'] = \
                office, district, m.group('party')
            state['stats_done'] = True
            continue

        # ---- Data rows
        m = DATA_RE.match(line)
        if not m:
            if re.fullmatch(r'[\d,\s]+', line):
                print(f'WARN unassigned numbers line: {raw!r}')
            continue
        name = m.group('name').strip()
        if not name or name in SKIP_NAMES or name.startswith('Write-In:'):
            continue
        if not state['office'] or not state['precinct']:
            print(f'WARN data row with no contest context: {raw!r}')
            continue

        votes = m.group('v').replace(',', '')
        ed, mail_v, prov = (m.group(k).replace(',', '') for k in ('ed', 'mail', 'prov'))
        candidate = 'Write Ins' if name == 'Write-In Totals' else title_case(name)
        results.append({
            'county': COUNTY, 'precinct': state['precinct'],
            'office': state['office'], 'district': state['district'],
            'party': state['party'], 'candidate': candidate,
            'votes': votes, 'election_day': ed, 'mail': mail_v, 'provisional': prov,
        })


def parse_report(pdf_path):
    """Parse one Electionware precinct-summary PDF -> (rows, set of precincts)."""
    with tempfile.TemporaryDirectory() as td:
        txt_path = str(Path(td) / 'precinct.txt')
        subprocess.run(['pdftotext', '-layout', pdf_path, txt_path], check=True)
        text = Path(txt_path).read_text()

    results = []
    precincts = set()
    state = {'precinct': None, 'office': None, 'district': '', 'party': '',
             'stats_done': False}
    for page in text.split('\f'):
        parse_lines(page, results, precincts, state)
    return results, precincts


def replace_senate(rows, recount_rows):
    """Return (rows_with_recount_Senate, changed_precincts_delta_summary)."""
    recount_senate = [r for r in recount_rows
                      if r['office'] == 'U.S. Senate' and r['party'] == 'REP']
    if not recount_senate:
        sys.exit('ERROR: no Republican U.S. Senate rows in recount report')

    # Candidate names should match the main report's section (same vendor).
    main_key = {(r['precinct'], r['candidate']): r for r in rows
                if r['office'] == 'U.S. Senate' and r['party'] == 'REP'}
    rec_key = {(r['precinct'], r['candidate']): r for r in recount_senate}

    if set(main_key) != set(rec_key):
        only_main = sorted(set(main_key) - set(rec_key))[:5]
        only_rec = sorted(set(rec_key) - set(main_key))[:5]
        sys.exit(f'ERROR: (precinct, candidate) mismatch main vs recount: '
                 f'main-only {only_main} recount-only {only_rec}')

    deltas = {}
    for key, rec in rec_key.items():
        before = int(main_key[key]['votes'])
        after = int(rec['votes'])
        if before != after:
            deltas.setdefault(key[0], []).append(
                f"{key[1]} {before}->{after}")

    # Drop main's REP U.S. Senate rows, splice in the recount ones (keeping
    # row order roughly: rebuilt at the end anyway).
    out = [r for r in rows if not (r['office'] == 'U.S. Senate' and r['party'] == 'REP')]
    out.extend(copy.deepcopy(s) for s in sorted(
        recount_senate, key=lambda r: (r['precinct'], r['candidate'])))
    return out, deltas


def main():
    if len(sys.argv) not in (3, 5):
        print(__doc__)
        sys.exit(1)
    pdf_path, output_path = sys.argv[1], sys.argv[2]
    if not Path(pdf_path).exists():
        sys.exit(f'ERROR: PDF not found: {pdf_path}')

    rows, precincts = parse_report(pdf_path)

    if len(sys.argv) == 5 and sys.argv[3] == '--senate-from':
        recount_pdf = sys.argv[4]
        if not Path(recount_pdf).exists():
            sys.exit(f'ERROR: recount PDF not found: {recount_pdf}')
        recount_rows, rec_precincts = parse_report(recount_pdf)
        rows, deltas = replace_senate(rows, recount_rows)
        print(f'Recount splice: {len(deltas)} precincts changed; '
              f'deltas: ' + '; '.join(f'{p}: {", ".join(c)}'
                                      for p, c in sorted(deltas.items())))

    with open(output_path, 'w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=[
            'county', 'precinct', 'office', 'district', 'party', 'candidate',
            'votes', 'election_day', 'mail', 'provisional'])
        writer.writeheader()
        writer.writerows(rows)

    print(f'Parsed {len(precincts)} precincts -> {len(rows)} rows -> {output_path}')


if __name__ == '__main__':
    main()