#!/usr/bin/env python3
"""
Wyoming County, PA 2022 (May 17) General Primary per-precinct results parser.

Source: "Wyoming PA 2022 Official May 17 General Primary Results.pdf" -- a
Dominion "Statement of Votes Cast by Geography" report (same vendor/family as
the county's 2022 general and 2023 primary reports).  Unlike the county's
2022 general scan, this PDF carries a clean native text layer, so the parse
runs over a `pdftotext -layout` extraction (run automatically when the parser
is handed the PDF, or hand it the pre-extracted .txt).

Report layout (75 pages):
  * Page banner repeated on every page (skipped): "Statement of Votes Cast by
    Geography", "Wyoming County, GENERAL PRIMARY 2022", "All Precincts, All
    Districts, ...", countywide "Total Ballots Cast: ...", "28 precincts
    reported out of 28", column header "Choice ... Votes ... ED MI PR".
  * Body: 28 "Precinct <NAME>" sections (names truncated by the report:
    "FAC BOROUGH WARD 1", "TUN BOROUGH WARD 4", "TUN TOWNSHIP #1",
    "TUN TOWNSHIP #2", "NOR BRANCH TOWNSHIP", "NOR TOWNSHIP"), each containing
    per-contest blocks: a contest header ("UNITED STATES SENATOR DEM (DEM)
    (Vote for 1)"), a "N ballots (...), M registered voters, turnout" line,
    candidate rows (name, votes, vote %, ED, MI, PR), a "Total" row and
    "Overvotes"/"Undervotes" rows.  The DEM State House contest header prints
    as "REPRESENTATIVE IN THE GENERAL ASSEMBLY D" (truncated district label);
    it is the same HD-110 district as the Republican contest.
  * A final "All Precincts" countywide summary section (excluded from the
    per-precinct output; used for internal validation).

Contests carried by the report: U.S. Senator (DEM/REP), Governor (DEM/REP),
Lieutenant Governor (DEM/REP), Representative in Congress (DEM/REP, HD-9),
Senator in General Assembly (DEM/REP, SD-20), Representative in the General
Assembly (DEM/REP, HD-110 -- the DEM contest is write-in-only, as Tina
Pickett's district had no Democratic primary candidate), Member of the
Democratic/Republican State Committee (SD-66 committee district -- REP prints
William Eggleston, DEM is write-in-only), and per-precinct "Democratic
Committee Person NN" contests (Vote for 2; some precincts tally named
write-in candidates, kept as their own rows).

The "(REP)" party tag in the contest header sets the row's party; Write-in
rows carry the contest's party (primary-specific requirement).

Registered Voters / Ballots Cast: the source prints party-level figures (the
"19 ballots ..., 65 registered voters" line of each DEM block and the party's
ballots on each REP block), so each precinct gets four RV/BC rows tagged
DEM/REP; no combined row is printed by the source.

Recount: the Republican U.S. Senate contest was recounted in June 2022.  This
report is dated 2022-06-06 (after the recount) and its U.S. Senator REP
precinct rows sum exactly to the post-recount countywide totals printed on
"Wyoming PA 2022 Primary Recount Totals.pdf" (2022-06-01) and to the state's
post-recount county ENR file, so no substitution is needed.

Usage:
    python parsers/pa_wyoming_primary_2022_results_parser.py <input> <output>

    <input> is the source PDF (recommended; needs the `pdftotext` binary) or
    its `pdftotext -layout` text extract.
"""

import csv
import re
import subprocess
import sys
import tempfile
from collections import defaultdict

COUNTY = "Wyoming"

# ---------------------------------------------------------------------------
# Canonicalization
# ---------------------------------------------------------------------------

# The Dominion report truncates precinct names; canonical names as used in the
# county's 2018/2020/2023/2024 files.
PRECINCT_FIXES = {
    'FAC BOROUGH WARD 1': 'Factoryville Borough Ward 1',
    'FAC BOROUGH WARD 2': 'Factoryville Borough Ward 2',
    'TUN BOROUGH WARD 1': 'Tunkhannock Borough Ward 1',
    'TUN BOROUGH WARD 2': 'Tunkhannock Borough Ward 2',
    'TUN BOROUGH WARD 3': 'Tunkhannock Borough Ward 3',
    'TUN BOROUGH WARD 4': 'Tunkhannock Borough Ward 4',
    'TUN TOWNSHIP #1': 'Tunkhannock Township #1',
    'TUN TOWNSHIP #2': 'Tunkhannock Township #2',
    'NOR BRANCH TOWNSHIP': 'North Branch Township',
    'NOR TOWNSHIP': 'Northmoreland Township',
    'N R BRANCH TOWNSHIP': 'North Branch Township',   # 2022 general spelling
}

CANONICAL_PRECINCTS = [
    "Braintrim Township", "Clinton Township", "Eaton Township", "Exeter Township",
    "Factoryville Borough Ward 1", "Factoryville Borough Ward 2", "Falls Township",
    "Forkston Township", "Laceyville Borough", "Lemon Township",
    "Mehoopany Township", "Meshoppen Borough", "Meshoppen Township",
    "Monroe Township", "Nicholson Borough", "Nicholson Township",
    "North Branch Township", "Northmoreland Township", "Noxen Township",
    "Overfield Township", "Tunkhannock Borough Ward 1",
    "Tunkhannock Borough Ward 2", "Tunkhannock Borough Ward 3",
    "Tunkhannock Borough Ward 4", "Tunkhannock Township #1",
    "Tunkhannock Township #2", "Washington Township", "Windham Township",
]

# Wyoming County is entirely within these districts; the contest headers do
# not print U.S./state district numbers.  The DEM State House header prints a
# truncated label ("... GENERAL ASSEMBLY D") for the same HD-110 district.
DISTRICTS = {
    'U.S. House': '9',
    'State Senate': '20',
    'State House': '110',
    'Member of Democratic State Committee': '66',
    'Member of Republican State Committee': '66',
}

OFFICE_MAP = [
    (re.compile(r'UNITED\s+STATES\s+SENATOR', re.I), 'U.S. Senate'),
    (re.compile(r'^GOVERNOR\b', re.I), 'Governor'),
    (re.compile(r'LIEUTENANT\s+GOVERNOR', re.I), 'Lieutenant Governor'),
    (re.compile(r'REPRESENTATIVE\s+IN\s+CONGRESS', re.I), 'U.S. House'),
    (re.compile(r'SENATOR\s+IN\s+GENERAL\s+ASSEMBLY', re.I), 'State Senate'),
    (re.compile(r'REPRESENTATIVE\s+IN\s+THE\s+GENERAL\s+ASSEMBLY', re.I),
     'State House'),
    (re.compile(r'MEMBER\s+OF\s+THE\s+DEMOCRATIC\s+STATE\s+COMMITTEE', re.I),
     'Member of Democratic State Committee'),
    (re.compile(r'MEMBER\s+OF\s+THE\s+REPUBLICAN\s+STATE\s+COMMITTEE', re.I),
     'Member of Republican State Committee'),
    (re.compile(r'DEMOCRATIC\s+COMMITTEE\s+PERSON', re.I),
     'Democratic Committee Person'),
]

# Spelling fixes applied to candidates as printed (title-cased afterwards).
NAME_FIXES = {
    'ALEX KHALIL': 'Alex Khalil',
    'CONOR LAMB': 'Conor Lamb',
    'JOHN FETTERMAN': 'John Fetterman',
    'KATHY BARNETTE': 'Kathy Barnette',
    'MEHMET OZ': 'Mehmet Oz',
    'GEORGE BOCHETTO': 'George Bochetto',
    'JEFF BARTOS': 'Jeff Bartos',
    'DAVE MCCORMICK': 'Dave McCormick',
    'SEAN GALE': 'Sean Gale',
    'CARLA SANDS': 'Carla Sands',
    'LOU BARLETTA': 'Lou Barletta',
    'DOUGLAS V. MASTRIANO': 'Douglas V. Mastriano',
    'NCHE ZAMA': 'Nche Zama',
    'DAVE WHITE': 'Dave White',
    'MELISSA HART': 'Melissa Hart',
    'BILL MCSWAIN': 'Bill McSwain',
    'CHARLIE GEROW': 'Charlie Gerow',
    'JOE GALE': 'Joe Gale',
    'JAKE CORMAN': 'Jake Corman',
    'AUSTIN DAVIS': 'Austin Davis',
    'RAY SOSA': 'Ray Sosa',
    'BRIAN SIMS': 'Brian Sims',
    'CLARICE SCHILLINGER': 'Clarice Schillinger',
    'JAMES EARL JONES': 'James Earl Jones',
    'RICK SACCONE': 'Rick Saccone',
    'JOHN BROWN': 'John Brown',
    'CHRIS FRYE': 'Chris Frye',
    'JEFF COLEMAN': 'Jeff Coleman',
    'RUSS DIAMOND': 'Russ Diamond',
    'CARRIE LEWIS DELROSSO': 'Carrie Lewis Delrosso',
    'TEDDY DANIELS': 'Teddy Daniels',
    'AMANDA R. WALDMAN': 'Amanda R. Waldman',
    'DAN MEUSER': 'Dan Meuser',
    'JACKIE BAKER': 'Jackie Baker',
    'LISA BAKER': 'Lisa Baker',
    'TINA PICKETT': 'Tina Pickett',
    'WILLIAM EGGLESTON': 'William Eggleston',
    'MICK CRONIN': 'Mick Cronin',
    'JENNIFER CRONIN': 'Jennifer Cronin',
    'CYNTHIA C. STEVENS': 'Cynthia C. Stevens',
    'HOWARD FISHER': 'Howard Fisher',
    'PATRICK J. MAHONEY': 'Patrick J. Mahoney',
    'JUDITH POTTS': 'Judith Potts',
    'DAVID MARTIN': 'David Martin',
    'NANCY THALER': 'Nancy Thaler',
    'MICHAEL KLEES': 'Michael Klees',
    'MICHAEL STABINSKY': 'Michael Stabinsky',
    'JANET SHAW': 'Janet Shaw',
    'WILLIAM SHAW': 'William Shaw',
    'BARBARA REEL': 'Barbara Reel',
}

# Countywide post-recount totals printed on
# "Wyoming PA 2022 Primary Recount Totals.pdf" (Statement of Votes Cast by
# Geography, 2022-06-01): candidate -> (votes, ED, MI, PR).
RECOUNT_US_SENATE_REP = {
    'Kathy Barnette': (971, 903, 68, 0),
    'Mehmet Oz': (1330, 1227, 103, 0),
    'George Bochetto': (36, 30, 6, 0),
    'Jeff Bartos': (271, 220, 51, 0),
    'Dave McCormick': (1682, 1571, 111, 0),
    'Sean Gale': (26, 20, 6, 0),
    'Carla Sands': (263, 205, 58, 0),
    'Write Ins': (37, 20, 17, 0),
}

# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

PAGE_BANNER_SKIP = (
    'Statement of Votes Cast by Geography',
    ' wyoming county ',
    'general primary 2022',
    'all precincts, all districts,',
    'total ballots cast:',
    'precincts reported out of',
    'overall turnout',
)

PRECINCT_RE = re.compile(r'^\s*Precinct\s+(.+?)\s*$')
CONTEST_RE = re.compile(r'^\s*([A-Z][A-Z0-9 #]+?)\s*\((DEM|REP)\)\s*\(\s*Vote for (\d+)\s*\)\s*$')
BALLOTS_RE = re.compile(
    r'^\s*([\d,]+)\s+ballots?\s*\(.*\)\s*,\s*([\d,]+)\s+registered voters')
CAND_RE = re.compile(
    r'^\s+(\S.*?)\s{2,}([\d,]+)\s+[\d.]+%\s+([\d,]+)\s+([\d,]+)\s+([\d,]+)\s*$')
TOTAL_RE = re.compile(r'^\s*Total\s+([\d,]+)\s+[\d.]+%\s+([\d,]+)\s+([\d,]+)\s+([\d,]+)\s*$')
UNCONTESTED_RE = re.compile(r'^\s*(Overvotes|Undervotes)\s+([\d,]+)\s*$')


def canon_precinct(name):
    name = name.upper().strip()
    if name in PRECINCT_FIXES:
        return PRECINCT_FIXES[name]
    words = []
    for w in name.split():
        words.append(w.capitalize() if (w.isupper() or w.islower()) and len(w) > 3
                     else w.capitalize() if w.lower() in ('r', 'nor', 'fac', 'tun')
                     else w)
    return ' '.join(words)


def fix_name(name):
    name = re.sub(r'\s+', ' ', name.strip())
    if name.upper() in NAME_FIXES:
        return NAME_FIXES[name.upper()]
    return name


def normalize_office(title):
    """Contest title -> (office, district) or (None, None)."""
    for rx, office in OFFICE_MAP:
        if rx.search(title):
            if office == 'Democratic Committee Person':
                m = re.search(r'PERSON\s+(\d+)', title, re.I)
                district = m.group(1).zfill(2) if m else ''
                return office, district
            return office, DISTRICTS.get(office, '')
    return None, None


def parse_text(text, problems, notes):
    precinct = None         # current precinct (canonical) or 'SUMMARY'
    contests = {}           # (precinct, office, district, party) -> block
    order = []
    ballots_seen = {}       # (precinct, party) -> ballots value
    rv_seen = {}            # (precinct, party) -> registered voters
    summary_totals = {}     # (office, district, party) -> {cand: (v, ed, mi, pr)}

    def block_key():
        return (precinct, office, district, party)

    for raw in text.splitlines():
        line = raw.rstrip()
        stripped = line.strip()
        if not stripped:
            continue
        low = ' ' + stripped.lower() + ' '
        if any(s in low for s in PAGE_BANNER_SKIP):
            continue
        if re.fullmatch(r'(choice\s+)?votes\s+vote\s+%\s+ed\s+mi\s+pr',
                        ' '.join(stripped.lower().split())):
            continue

        m = PRECINCT_RE.match(line)
        if m:
            precinct = canon_precinct(m.group(1))
            office = None      # no stray candidate rows before a contest header
            if precinct not in CANONICAL_PRECINCTS:
                problems.append(f'unknown precinct name {precinct!r}')
            continue
        if stripped == 'All Precincts':
            precinct = 'SUMMARY'
            office = None
            continue

        cm = CONTEST_RE.match(line)
        if cm:
            title, party, vote_for = cm.group(1).strip(), cm.group(2), int(cm.group(3))
            office, district = normalize_office(title)
            if office is None:
                problems.append(f'unrecognized contest title {title!r} '
                                f'(precinct={precinct})')
                office = district = None
                continue
            key = block_key()
            if key in contests:
                problems.append(f'duplicate contest block {key}')
            if vote_for != 1 and office != 'Democratic Committee Person':
                problems.append(f'{key}: unexpected Vote-for {vote_for}')
            contests[key] = {'rows': [], 'total': None, 'totals': None,
                             'vote_for': vote_for}
            order.append(key)
            continue

        bm = BALLOTS_RE.match(line)
        if bm and precinct and party:
            ballots = int(bm.group(1).replace(',', ''))
            rv = int(bm.group(2).replace(',', ''))
            pk = (precinct, party)
            if pk in ballots_seen:
                # in the All-Precincts summary the per-precinct Democratic
                # Committee Person contests legitimately print differing
                # ballot counts; only flag real precincts
                if ballots_seen[pk] != ballots and precinct != 'SUMMARY':
                    notes.append(f'{pk}: ballots differ across contests '
                                 f'({ballots_seen[pk]} vs {ballots})')
            else:
                ballots_seen[pk] = ballots
                rv_seen[pk] = rv
            continue

        tm = TOTAL_RE.match(line)
        if tm:
            key = block_key()
            if key in contests:
                contests[key]['total'] = int(tm.group(1).replace(',', ''))
                contests[key]['totals'] = tuple(
                    int(x.replace(',', '')) for x in tm.groups()[1:])
            continue

        cm2 = CAND_RE.match(line)
        if cm2 and precinct and office:
            name = cm2.group(1).strip()
            votes = int(cm2.group(2).replace(',', ''))
            ed = int(cm2.group(3).replace(',', ''))
            mi = int(cm2.group(4).replace(',', ''))
            pr = int(cm2.group(5).replace(',', ''))
            key = block_key()
            if key not in contests:
                problems.append(f'candidate row outside a contest: '
                                f'{precinct=} {name!r}')
                continue
            contests[key]['rows'].append({
                'name': name, 'votes': votes, 'ed': ed, 'mi': mi, 'pr': pr})
            continue

        um = UNCONTESTED_RE.match(line)
        if um:
            continue

    return contests, order, ballots_seen, rv_seen, summary_totals


def collect(contests, order, ballots_seen, rv_seen, summary_totals, problems,
            notes):
    """Emit rows and run internal validation."""
    results = []

    # RV / BC rows (party-level, as printed by the source)
    for (precinct, party) in sorted(ballots_seen, key=lambda k: (k[0], k[1])):
        if precinct == 'SUMMARY':
            continue
        results.append([COUNTY, precinct, 'Registered Voters', '', party, '',
                        str(rv_seen[(precinct, party)]), '', '', ''])
        results.append([COUNTY, precinct, 'Ballots Cast', '', party, '',
                        str(ballots_seen[(precinct, party)]), '', '', ''])

    for key in order:
        precinct, office, district, party = key
        b = contests[key]
        rows = b['rows']

        # combine multiple Write-in slot rows (Vote for 2)
        writeins = defaultdict(lambda: [0, 0, 0, 0])
        named = []
        for r in rows:
            if r['name'].lower().startswith('write-in'):
                writeins[r['name']][0] += r['votes']
                writeins[r['name']][1] += r['ed']
                writeins[r['name']][2] += r['mi']
                writeins[r['name']][3] += r['pr']
            else:
                named.append(r)

        if precinct == 'SUMMARY':
            summary_totals.setdefault((office, district, party), {})
            for r in named:
                summary_totals[(office, district, party)][fix_name(r['name'])] = \
                    (r['votes'], r['ed'], r['mi'], r['pr'])
            summary_totals[(office, district, party)]['Write Ins'] = \
                (sum(w[0] for w in writeins.values()),
                 sum(w[1] for w in writeins.values()),
                 sum(w[2] for w in writeins.values()),
                 sum(w[3] for w in writeins.values()))
            continue

        for r in named:
            results.append([COUNTY, precinct, office, district, party,
                            fix_name(r['name']), str(r['votes']),
                            str(r['ed']), str(r['mi']), str(r['pr'])])
        for w in writeins.values():
            results.append([COUNTY, precinct, office, district, party,
                            'Write Ins', str(w[0]), str(w[1]), str(w[2]),
                            str(w[3])])

        # internal validation vs printed Total row
        if b['total'] is None:
            problems.append(f'{key}: no Total row printed')
            continue
        cand_sum = sum(r['votes'] for r in rows)
        if cand_sum != b['total']:
            problems.append(f'{key}: candidate sum {cand_sum} != Total '
                            f'{b["total"]}')
        for idx, label in ((1, 'ED'), (2, 'MI'), (3, 'PR')):
            s = sum((r['ed'], r['mi'], r['pr'])[idx - 1] for r in rows)
            if s != b['totals'][idx - 1]:
                problems.append(f'{key}: {label} sum {s} != Total '
                                f'{b["totals"][idx - 1]}')

    # per contest key: countywide offices appear once per precinct (28) + once
    # in the All Precincts summary (29); each Democratic Committee Person
    # district maps to exactly one precinct + 1 summary (2)
    counts = defaultdict(int)
    for key in order:
        counts[(key[1], key[2], key[3])] += 1
    for ck, n in counts.items():
        expected = 29 if ck[0] != 'Democratic Committee Person' else 2
        if n != expected:
            problems.append(f'contest {ck} appears {n} times (expected '
                            f'{expected})')
    cm_precinct_blocks = sum(1 for key in order
                             if key[1] == 'Democratic Committee Person'
                             and key[0] != 'SUMMARY')
    if cm_precinct_blocks != 28:
        problems.append(f'{cm_precinct_blocks} Democratic Committee Person '
                        f'precinct blocks (expected 28)')

    # countywide: precinct sums vs All Precincts summary
    sums = defaultdict(lambda: [0, 0, 0, 0])
    for _, precinct, office, district, party, cand, votes, ed, mi, pr in results:
        if office in ('Registered Voters', 'Ballots Cast'):
            continue
        k = (office, district, party, cand)
        sums[k][0] += int(votes)
        sums[k][1] += int(ed) if ed else 0
        sums[k][2] += int(mi) if mi else 0
        sums[k][3] += int(pr) if pr else 0
    for (office, district, party), cands in summary_totals.items():
        for cand, (tv, ted, tmi, tpr) in cands.items():
            s = sums.get((office, district, party, cand), [0, 0, 0, 0])
            if list(s) != [tv, ted, tmi, tpr]:
                problems.append(
                    f'COUNTYWIDEMISMATCH {office}/{district}/{party} '
                    f'{cand}: precinct sum {list(s)} vs summary '
                    f'[{tv}, {ted}, {tmi}, {tpr}]')
    return results, summary_totals


def write_csv(path, rows):
    with open(path, 'w', newline='', encoding='utf-8') as fh:
        w = csv.writer(fh)
        w.writerow(['county', 'precinct', 'office', 'district', 'party',
                    'candidate', 'votes', 'election_day', 'mail',
                    'provisional'])
        w.writerows(rows)


def extract_text(input_path):
    if not input_path.lower().endswith('.pdf'):
        with open(input_path, encoding='utf-8') as fh:
            return fh.read()
    with tempfile.NamedTemporaryFile(suffix='.txt', delete=False) as tmp:
        tmp_path = tmp.name
    subprocess.run(['pdftotext', '-layout', input_path, tmp_path],
                   check=True, capture_output=True)
    with open(tmp_path, encoding='utf-8') as fh:
        return fh.read()


def run(input_path, output_path):
    problems, notes = [], []
    text = extract_text(input_path)
    contests, order, ballots_seen, rv_seen, summary_totals = \
        parse_text(text, problems, notes)
    results, summary_totals = collect(contests, order, ballots_seen, rv_seen,
                                      summary_totals, problems, notes)
    write_csv(output_path, results)

    # recount check: countywide U.S. Senate REP vs the recount statement
    recount_ok = True
    for cand, (tv, ted, tmi, tpr) in RECOUNT_US_SENATE_REP.items():
        s = summary_totals.get(('U.S. Senate', '', 'REP'), {}).get(cand)
        if s is None or list(s) != list((tv, ted, tmi, tpr)):
            problems.append(f'RECOUNTDIFF U.S. Senate REP {cand}: parsed '
                            f'{list(s) if s else None} vs recount '
                            f'[{tv}, {ted}, {tmi}, {tpr}]')
            recount_ok = False

    print(f'wrote {len(results)} rows to {output_path}')
    contests_emitted = sorted({(r[2], r[3], r[4]) for r in results})
    print(f'contest keys ({len(contests_emitted)}):')
    for o, d, p in contests_emitted:
        print(f'  {o} | {d} | {p}')
    print(f'recount check (US Senate REP vs 2022-06-01 recount totals): '
          f'{"PASS" if recount_ok else "FAIL"}')
    print(f'\nPROBLEMS ({len(problems)}):')
    for p in problems:
        print(f'  ! {p}')
    print(f'\nNOTES ({len(notes)}):')
    for n in notes:
        print(f'  - {n}')


def main(argv):
    if len(argv) != 3:
        print(__doc__)
        return 2
    run(argv[1], argv[2])
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))