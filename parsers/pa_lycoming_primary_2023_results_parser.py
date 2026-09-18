#!/usr/bin/env python3
"""
Lycoming County, PA 2023 Municipal Primary parser.

Sources (same custom "Official Results" layout as the 2023 general, with a
"(Dem)"/"(Rep)" party token in every contest header):

  County summary  "Lycoming County Official Results 2023 Primary.pdf"
      All Precincts
          Justice of the Supreme Court (Dem) (Vote for 1), 18804 registered voters, turnout 26.97%
              Daniel McCaffery                      2067      44.22%       1285       778           4
              Write-in                                26       0.56%         23         2           1
              Total                                 4674     100.00%       2924      1742           8

  Per-precinct    "Lycoming County Precinct Results 2023 Primary.pdf"
      Precinct Anthony
          Justice of the Supreme Court (Dem) (Vote for 1)
              ...same data lines, no registered-voters/turnout suffix...

The mode (county summary vs by-precinct) is auto-detected from the presence of
"Precinct ..." lines.

Conventions (matching pa_lycoming_general_2023_results_parser.py):
  - contest title split into office + district via the general parser's
    normalize_office();
  - the header's "(Dem)"/"(Rep)" token becomes the party of EVERY row of the
    contest, including the aggregated "Write-ins" row (repo-wide primary
    convention: keeps DEM/REP sections of the same office from colliding in
    the duplicate_entries test);
  - duplicate "Write-in" rows in a contest are summed into ONE "Write-ins" row;
  - the source has no per-precinct/per-contest registered-voters or
    ballots-cast rows; only the county summary's header statistics line
    ("Total Ballots Cast: 18952, Registered Voters: 60630") is reported, which
    is emitted as Registered Voters / Ballots Cast rows in county mode.

Usage: python pa_lycoming_primary_2023_results_parser.py <input_pdf_or_txt> <output_csv>
"""

import csv
import re
import subprocess
import sys
import tempfile
from pathlib import Path

from pa_lycoming_general_2023_results_parser import normalize_office as _general_normalize_office

COUNTY = 'Lycoming'


def normalize_office(office_text):
    """(office, district) from a contest title.

    Same conventions as the 2023 general parser, except the school-director
    region: the general parser's SD regex silently drops any non-numeric
    region other than Boro (so "Muncy SD Boro", "Muncy SD Creek" and
    "Muncy SD Twp" all collapse to district "Muncy"). Mirror instead what
    Lycoming's 2023 general *precinct* file did:
        "Muncy SD Boro"        -> School Director, Muncy
        "Muncy SD Creek"       -> School Director, Muncy Creek
        "Muncy SD Twp"         -> School Director, Muncy Twp
        "East Lycoming SD 1"   -> School Director, East Lycoming 1
        "Montgomery SD 1 (2yr)"-> School Director 2 Year Term, Montgomery 1
        "Wellsboro SD (2yr)"   -> School Director 2 Year Term, Wellsboro
    """
    m = re.match(r'^(.+?)\s+SD\s*(.*?)(?:\s*\((\d+)yr\))?\s*$', office_text)
    if m:
        dist = m.group(1).strip()
        region = m.group(2).strip()
        if region and region != 'Boro':
            dist += f' {region}'
        office_out = 'School Director' + (f' {m.group(3)} Year Term'
                                          if m.group(3) else '')
        return office_out, dist
    return _general_normalize_office(office_text)

PRECINCT_RE = re.compile(r'^Precinct\s+(.+)$')
CONTEST_RE = re.compile(
    r'^(?P<title>.+?)\s*\((?P<party>Dem|Rep)\)\s*\(Vote for (?P<vote_for>\d+)\)'
    r'(?:,\s*(?P<rv>[\d,]+) registered voters, turnout (?P<turnout>[\d.]+)%'
    r'(?:,\s*(?P<bc>[\d,]+) ballots cast)?\s*)?$',
    re.IGNORECASE,
)
DATA_LINE_RE = re.compile(
    r'^(.+?)\s+(\d[\d,]*)\s+(\d+\.\d+)%\s+(\d[\d,]*)\s+(\d[\d,]*)\s+(\d[\d,]*)$'
)
STATS_RE = re.compile(
    r'^Total Ballots Cast:\s*([\d,]+)'
    r'(?:,\s*Registered Voters:\s*([\d,]+))?'
    r'(?:,\s*Overall Turnout:\s*[\d.]+%)?\s*$'
)

SKIP_SUBSTRINGS = (
    'precincts reported out of',
)

SKIP_PREFIXES = (
    'Official Results',
    'May 16, 2023 Municipal Primary',
    'Lycoming County',
    'All Precincts, All Districts',
    'Choice ',
)

PARTY = {'Dem': 'DEM', 'Rep': 'REP'}


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


def parse(text, problems):
    """Parse either the county summary or the by-precinct report.

    Returns (results, contests) where contests is a dict for validation:
      key -> {'header':..., 'party':..., 'vote_for':..., 'rv':...,
              'rows': [(name, votes, ed, mi, pr)], 'total': (...), 'precinct':...}
    """
    results = []
    contests = {}
    current_precinct = None          # None => county-level (All Precincts)
    current_contest = None           # key into contests
    writein = None                   # accumulating dict
    stats = {'ballots_cast': None, 'registered_voters': None}
    in_county_summary = 'Official Results by Precinct' not in text

    def flush_writein():
        nonlocal writein
        if writein and current_contest:
            c = contests[current_contest]
            c['rows'].append(('Write-ins', writein['votes'], writein['ed'],
                              writein['mi'], writein['pr']))
        writein = None

    for raw in text.split('\n'):
        line = raw.strip()
        if not line:
            continue
        if any(p in line for p in SKIP_SUBSTRINGS):
            continue
        if any(line.startswith(p) for p in SKIP_PREFIXES):
            continue

        prec = PRECINCT_RE.match(line)
        if prec:
            flush_writein()
            current_precinct = prec.group(1).strip()
            current_contest = None
            continue

        stats_m = STATS_RE.match(line)
        if stats_m:
            # Header statistics line (county summary: "Total Ballots Cast:
            # 18952, Registered Voters: 60630, Overall Turnout: 31.26%").
            # Appears at the top of every page; only the countywide header
            # stats are reported, and only the county summary uses them.
            if in_county_summary and current_contest is None:
                stats['ballots_cast'] = int(stats_m.group(1).replace(',', ''))
                stats['registered_voters'] = (int(stats_m.group(2).replace(',', ''))
                                              if stats_m.group(2) else None)
            continue

        contest = CONTEST_RE.match(line)
        if contest:
            flush_writein()
            title = contest.group('title').strip()
            party = PARTY[contest.group('party').capitalize()]
            vote_for = int(contest.group('vote_for'))
            rv = int(contest.group('rv').replace(',', '')) if contest.group('rv') else None
            office, district = normalize_office(title)
            key = (current_precinct, party, office, district)
            if key in contests:
                problems.append(f'duplicate contest header: {key}')
            current_contest = key
            contests[key] = {
                'header': title, 'party': party, 'vote_for': vote_for,
                'rv': rv, 'precinct': current_precinct,
                'rows': [], 'total': None,
            }
            continue

        if current_contest is None:
            continue

        if re.match(r'^Total\s', line):
            data = DATA_LINE_RE.match(line)
            if data:
                c = contests[current_contest]
                if c['total'] is None:
                    c['total'] = (int(data.group(2).replace(',', '')),
                                  int(data.group(4)), int(data.group(5)),
                                  int(data.group(6)))
                else:
                    problems.append(f'duplicate Total row: {current_contest}')
            flush_writein()
            continue

        data = DATA_LINE_RE.match(line)
        if not data:
            problems.append(f'unparsed line: {line!r}')
            continue

        name = data.group(1).strip()
        votes = int(data.group(2).replace(',', ''))
        ed, mi, pr = int(data.group(4)), int(data.group(5)), int(data.group(6))
        if name == 'Write-in':
            if writein is None:
                writein = {'votes': 0, 'ed': 0, 'mi': 0, 'pr': 0}
            writein['votes'] += votes
            writein['ed'] += ed
            writein['mi'] += mi
            writein['pr'] += pr
            continue

        contests[current_contest]['rows'].append((name, votes, ed, mi, pr))
    flush_writein()

    # Build output rows.
    for key, c in contests.items():
        office, district = normalize_office(c['header'])
        for name, votes, ed, mi, pr in c['rows']:
            row = {
                'county': COUNTY,
                'office': office, 'district': district, 'party': c['party'],
                'candidate': name, 'votes': str(votes),
                'election_day': str(ed), 'mail': str(mi), 'provisional': str(pr),
            }
            if not in_county_summary:
                row = {'precinct': c['precinct'], **row}
            results.append(row)

    # County summary header statistics -> Registered Voters / Ballots Cast rows.
    if in_county_summary and stats['ballots_cast'] is not None:
        if stats['registered_voters']:
            results.insert(0, {
                'county': COUNTY, 'office': 'Registered Voters',
                'district': '', 'party': '', 'candidate': '',
                'votes': str(stats['registered_voters']),
                'election_day': '', 'mail': '', 'provisional': '',
            })
        results.insert(1, {
            'county': COUNTY, 'office': 'Ballots Cast',
            'district': '', 'party': '', 'candidate': '',
            'votes': str(stats['ballots_cast']),
            'election_day': '', 'mail': '', 'provisional': '',
        })

    return results, contests, stats, in_county_summary


def reconcile(contests, problems):
    """Every contest: candidate rows + write-ins == source Total row."""
    ok = 0
    for key, c in sorted(contests.items(), key=lambda kv: str(kv[0])):
        sums = [sum(r[i] for r in c['rows']) for i in range(1, 5)]
        if c['total'] is None:
            problems.append(f'no Total row: {key}')
            continue
        if sums != list(c['total']):
            problems.append(
                f'MISMATCH {key}: rows {sums} != total {c["total"]} ({c["header"]})')
            continue
        ok += 1
    print(f'Contest-totals reconciliation: {ok}/{len(contests)} contests match '
          f'(candidates + write-ins == Total).')
    return ok


FIELDNAMES_COUNTY = ['county', 'office', 'district', 'party',
                     'candidate', 'votes', 'election_day', 'mail', 'provisional']
FIELDNAMES_PRECINCT = ['county', 'precinct', 'office', 'district', 'party',
                       'candidate', 'votes', 'election_day', 'mail', 'provisional']


def write_csv(results, output_path, precinct_mode):
    fields = FIELDNAMES_PRECINCT if precinct_mode else FIELDNAMES_COUNTY
    with open(output_path, 'w', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(results)
    print(f'Wrote {len(results)} rows to {output_path}')


def main():
    if len(sys.argv) < 3:
        print('Usage: python pa_lycoming_primary_2023_results_parser.py <input_pdf_or_txt> <output_csv>')
        sys.exit(1)
    text = extract_text(sys.argv[1])
    problems = []
    results, contests, stats, in_county_summary = parse(text, problems)
    write_csv(results, sys.argv[2], precinct_mode=not in_county_summary)
    reconcile(contests, problems)
    if in_county_summary:
        print(f"Header stats: Registered Voters {stats['registered_voters']}, "
              f"Ballots Cast {stats['ballots_cast']}")
        print(f'Contests parsed: {len(contests)} '
              f'({sum(1 for c in contests.values() if c["party"] == "DEM")} DEM, '
              f'{sum(1 for c in contests.values() if c["party"] == "REP")} REP)')
    else:
        print(f'Precinct-contest instances parsed: {len(contests)} '
              f'across {len({c["precinct"] for c in contests.values()})} precincts')
    if problems:
        print(f'PROBLEMS ({len(problems)}):')
        for p in problems[:40]:
            print('  ' + p)
        sys.exit(2)


if __name__ == '__main__':
    main()