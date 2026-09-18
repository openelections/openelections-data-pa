#!/usr/bin/env python3
"""Parse Montgomery County PA 2023 general election results (Dominion
"Election Summary Report", county-level totals for all contests).

The source text (pdftotext -layout of the county's Official Results PDF) has
two reports concatenated:

  * Pages 1-67: "Election Summary Report -- Summary for: All Contests, All
    Districts, All Tabulators, All Counting Groups" -- county-level totals for
    every contest on the ballot.  This parser reads ONLY this section.
  * Pages 1-3218 (of a second report, "Statement of Votes Cast Report"):
    per-precinct crosstabs.  Parsing stops at the "Statement of" marker; those
    pages are future work.

Contest block layout:

    Justice of the Supreme Court (Vote for 1)
    Precincts Reported: 426 of 426 (100.00%)
                                                  Election Day   Mail-in ...
    Times Cast                                      176,701     75,655 ...
    Candidate                           Party       Election Day   Mail-in ...
    Daniel McCaffery                     DEM         100,108     64,019 ...
    Write-in                                             185        45 ...
    Total Votes                                      175,289     75,120 ...

Retention questions appear as contests whose title carries the judge's name
and whose rows are Yes/No (e.g. "Judge of the Superior Court-Jack Panella
(Vote for 1)"); they are emitted as office "<Office> Retention - <Name>" with
candidates Yes/No and no party, matching the 2025 county-file style.

Usage:
    python3 parsers/pa_montgomery_general_2023_results_parser.py \
        <input.txt> <output.csv> [--county Montgomery]
"""

import csv
import re
import sys

CONTEST_RE = re.compile(r'^(.+?)\s+\(Vote for (\d+)\)$')
NUM_RE = re.compile(r'^\d{1,3}(?:,\d{3})*$')
NUM = r'\d{1,3}(?:,\d{3})*'
FOUR_NUMS = r'{n}\s+{n}\s+{n}\s+{n}'.format(n=NUM)
# candidate row: <name> [<party>] <ED> <MI> <PR> <Total>
ROW_RE = re.compile(r'^(?P<prefix>.*\S)\s+(%s)$' % FOUR_NUMS)
TIMES_CAST_RE = re.compile(
    r'^Times Cast\s+(\d{1,3}(?:,\d{3})*)\s+(\d{1,3}(?:,\d{3})*)\s+'
    r'(\d{1,3}(?:,\d{3})*)\s+(\d{1,3}(?:,\d{3})*)\s*/\s*(\d{1,3}(?:,\d{3})*)')
RV_RE = re.compile(
    r'^Registered Voters:\s+\d{1,3}(?:,\d{3})*\s+of\s+'
    r'(\d{1,3}(?:,\d{3})*)')
TOTAL_VOTES_RE = re.compile(r'^Total Votes\s+(%s)$' % FOUR_NUMS)
STOP_MARKER = 'Statement of'

PARTIES = {'DEM', 'REP', 'LBR', 'LIB', 'GRN', 'GRE', 'CON', 'IND', 'NPA',
           'WFP', 'D/R', 'WRITE-IN', 'DEM/REP', 'REP/DEM', 'KEY'}

COUNTYWIDE_OFFICES = {
    'Justice of the Supreme Court',
    'Judge of the Superior Court',
    'Judge of the Commonwealth Court',
    'Judge of the Court of Common Pleas',
    'County Commissioner',
    'Clerk of Courts',
    'Controller',
    'Coroner',
    'District Attorney',
    'Prothonotary',
    'Recorder of Deeds',
    'Register of Wills',
    'Sheriff',
    'Treasurer',
}

# Municipality offices appearing in Montgomery 2023 titles.
MUNI_OFFICE_RE = re.compile(
    r'^(?P<muni>.+?)\s+(?P<kind>Township|Borough)\s+'
    r'(?P<office>Council|Commissioner|Supervisor|Auditor)'
    r'(?:\s+(?P<qual>Ward \d+|At-Large))?'
    r'(?:\s+(?P<term>\d)\s+Year Term)?$')


def _num(s):
    return int(s.replace(',', ''))


def map_contest(title):
    """Map a contest header title to (office, district)."""
    # Retention questions: "Judge of the Superior Court-Jack Panella"
    m = re.match(r'^Judge of the (Superior Court|Court of Common Pleas)-(.+)$',
                 title)
    if m:
        return '%s Retention - %s' % (m.group(1), m.group(2)), ''
    if title in COUNTYWIDE_OFFICES:
        return title, ''
    # "Magisterial District Judge 38-1-01"
    m = re.match(r'^Magisterial District Judge (\d{2}-\d-\d{2})$', title)
    if m:
        return 'Magisterial District Judge', m.group(1)
    # School director contests: "<District> School Director [Region N]
    # [At-Large] [N Year Term]"
    m = re.match(r'^(?P<school>.+?)\s+School Director(?P<rest>.*)$', title)
    if m:
        rest = m.group('rest').strip()
        term = ''
        tm = re.search(r'(?P<n>\d)\s+Year Term$', rest)
        if tm:
            term = ' (%s Year)' % tm.group('n')
            rest = rest[:tm.start()].strip()
        district = (m.group('school') + ' ' + rest).strip()
        return 'School Director' + term, district
    # Bryn Athyn (Borough of Bryn Athyn) -- titles omit "Borough".
    if title == 'Bryn Athyn Council':
        return 'Borough Council', 'Bryn Athyn'
    if title == 'Bryn Athyn Auditor':
        return 'Borough Auditor', 'Bryn Athyn'
    # Norristown (borough, county seat) -- title omits "Borough".
    m = re.match(r'^Norristown Council(?P<qual> At-Large| Ward \d+)?$', title)
    if m:
        return 'Borough Council', ('Norristown' + (m.group('qual') or '')).strip()
    # Generic municipality offices:
    # "<Muni> Township|Borough <office> [Ward N|At-Large] [N Year Term]"
    m = MUNI_OFFICE_RE.match(title)
    if m:
        office = ('Township ' if m.group('kind') == 'Township'
                  else 'Borough ') + m.group('office')
        if m.group('term'):
            office += ' (%s Year)' % m.group('term')
        district = m.group('muni')
        if m.group('qual'):
            district += ' ' + m.group('qual')
        return office, district
    raise ValueError('Unrecognized contest title: %r' % title)


def parse(input_path, county='Montgomery'):
    with open(input_path, encoding='utf-8-sig') as f:
        lines = f.read().splitlines()

    contests = []          # list of dicts, in source order
    cur = None
    registered_voters = None
    ballots_cast = None    # (bc, ed, mail, prov) from first Times Cast

    for raw in lines:
        line = raw.rstrip()
        if line.strip().startswith(STOP_MARKER):
            break  # start of the per-precinct "Statement of Votes Cast Report"
        if not line.strip():
            continue

        m = CONTEST_RE.match(line.strip())
        if m and not line[:1].isspace():
            cur = {'title': m.group(1).strip(), 'vote_for': int(m.group(2)),
                   'rows': [], 'total_votes': None, 'total_index': None}
            contests.append(cur)
            continue

        if cur is None:
            m = RV_RE.match(line.strip())
            if m:
                registered_voters = _num(m.group(1))
            continue

        stripped = line.strip()
        if stripped.startswith('Page:') or stripped.startswith('Times Cast'):
            tm = TIMES_CAST_RE.match(stripped)
            if tm and ballots_cast is None:
                ballots_cast = (_num(tm.group(4)), _num(tm.group(1)),
                                _num(tm.group(2)), _num(tm.group(3)))
            continue
        if stripped.startswith('Candidate') or \
                stripped.startswith('Election Day') or \
                stripped.startswith('Precincts Reported'):
            continue
        tv = TOTAL_VOTES_RE.match(stripped)
        if tv:
            nums = [_num(x) for x in tv.group(1).split()]
            cur['total_votes'] = nums
            # Rows printed after the Total Votes line (Dominion lists named
            # write-in candidates there, excluded from the printed total)
            # are kept separately from the rows that must sum to the total.
            cur['total_index'] = len(cur['rows'])
            continue

        row = ROW_RE.match(stripped)
        if row:
            nums = [_num(x) for x in row.group(2).split()]
            tokens = row.group('prefix').split()
            party = ''
            if tokens and tokens[-1] in PARTIES:
                party = tokens.pop()
            name = ' '.join(tokens).strip()
            if name == 'Write-in':
                name = 'Write-ins'
            cur['rows'].append({'candidate': name, 'party': party,
                                'election_day': nums[0], 'mail': nums[1],
                                'provisional': nums[2], 'votes': nums[3]})
            continue
        raise ValueError('Unrecognized line in contest %r: %r'
                         % (cur['title'], line))

    # ---- validation -----------------------------------------------------
    problems = []
    extras = []
    for c in contests:
        if c['total_votes'] is None:
            problems.append('%s: missing Total Votes line' % c['title'])
            continue
        split = c['total_index'] if c['total_index'] is not None \
            else len(c['rows'])
        pre, post = c['rows'][:split], c['rows'][split:]
        for r in c['rows']:
            if r['election_day'] + r['mail'] + r['provisional'] != r['votes']:
                problems.append('%s / %s: ED+MI+PR != Total (%d+%d+%d != %d)'
                                % (c['title'], r['candidate'],
                                   r['election_day'], r['mail'],
                                   r['provisional'], r['votes']))
        row_sum = sum(r['votes'] for r in pre)
        if row_sum != c['total_votes'][3]:
            problems.append('%s: candidate totals sum to %d, Total Votes %d'
                            % (c['title'], row_sum, c['total_votes'][3]))
        for i, label in ((0, 'Election Day'), (1, 'Mail-in'), (2, 'Provisional')):
            col = sum(r[['election_day', 'mail', 'provisional'][i]]
                      for r in pre)
            if col != c['total_votes'][i]:
                problems.append('%s: %s column sums to %d, Total Votes %d'
                                % (c['title'], label, col,
                                   c['total_votes'][i]))
        for r in post:
            if r['party'] != 'WRITE-IN':
                problems.append('%s: non-write-in row after Total Votes: %r'
                                % (c['title'], r['candidate']))
            else:
                extras.append('%s: %s %d' % (c['title'], r['candidate'],
                                             r['votes']))
    if problems:
        raise ValueError('Validation failed:\n' + '\n'.join(problems))
    if extras:
        print('Note: %d named write-in rows printed after the Total Votes '
              'line (excluded from the printed contest totals in the source; '
              'kept in the output):' % len(extras))
        for e in extras:
            print('  ' + e)

    rows = [['Registered Voters', '', '', '', registered_voters, '', '', ''],
            ['Ballots Cast', '', '', '', ballots_cast[0], ballots_cast[1],
             ballots_cast[2], ballots_cast[3]]]
    for c in contests:
        office, district = map_contest(c['title'])
        for r in c['rows']:
            rows.append([office, district, r['party'], r['candidate'],
                         r['votes'], r['election_day'], r['mail'],
                         r['provisional']])
    return county, rows


def main(argv):
    args = [a for a in argv[1:] if not a.startswith('--')]
    county = 'Montgomery'
    for i, a in enumerate(argv):
        if a == '--county' and i + 1 < len(argv):
            county = argv[i + 1]
    if len(args) != 2:
        sys.exit('usage: pa_montgomery_general_2023_results_parser.py '
                 '<input.txt> <output.csv> [--county Montgomery]')
    input_path, output_path = args
    county, rows = parse(input_path, county)
    with open(output_path, 'w', newline='', encoding='utf-8') as f:
        w = csv.writer(f)
        w.writerow(['county', 'office', 'district', 'party', 'candidate',
                    'votes', 'election_day', 'mail', 'provisional'])
        for office, district, party, candidate, votes, ed, mail, prov in rows:
            w.writerow([county, office, district, party, candidate,
                        votes, ed, mail, prov])
    print('wrote %d rows (%d contests) to %s'
          % (len(rows), len(rows) - 2, output_path))


if __name__ == '__main__':
    main(sys.argv)