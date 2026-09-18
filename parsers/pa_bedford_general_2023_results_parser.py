#!/usr/bin/env python3
"""
Bedford County, PA 2023 General Election parser.

Source: "Bedford County Precinct Statement of Votes Cast 2023 General.pdf"
(Hart "Statement of Votes Cast" crosstab: precincts as rows, candidates as
rotated/reversed column headers, each precinct block has Election Day /
Mail-In / Provisional / Total sub-rows).

Uses the shared sovc_crosstab_pp engine (vote_type_rows=True, the Jefferson
code path). The engine skips the per-candidate "Qualified Write-In <name>" and
"Unresolved Write-In" columns (they are not separate candidates), so a second
pass over the same tables re-parses exactly those columns and aggregates them
per precinct into a single "Write-ins" candidate row (with ED/mail/provisional
breakdowns), keeping candidate totals equal to the official "Total Votes".

Usage: python pa_bedford_general_2023_results_parser.py <input_pdf> <output_csv>
"""

import csv
import re
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from sovc_crosstab_pp import (  # noqa: E402
    SovcCrosstabConfig, VOTE_TYPES, decode_header, is_times_cast_table,
    clean_precinct, make_clean_votes, parse_contest_title,
    parse_sovc_crosstab_results,
)

COUNTY = 'Bedford'


def is_skip_row(label):
    """Section/summary rows to skip (never real precinct rows)."""
    if not label:
        return True
    if label in VOTE_TYPES:
        return False
    if label in ('State', 'State - Total'):
        return True
    if label.startswith('Cumulative'):
        return True
    if 'Precinct Portion' in label:
        return True
    if 'Total' in label and 'County' in label:
        return True
    return False


CONFIG = SovcCrosstabConfig(
    county=COUNTY,
    vote_type_rows=True,
    is_skip_row=is_skip_row,
    treat_redacted_as_zero=False,
    contest_title_raw_lines=False,
    turnout_requires_registered_header=True,
    turnout_max_pages=7,
)


def parse_writeins(pdf_path, config):
    """Second pass: aggregate the Qualified/Unresolved Write-In columns of
    every candidate table into one 'Write-ins' total per (precinct, contest).
    Mirrors the engine's vote_type_rows candidate-table loop."""
    import pdfplumber

    clean_votes = make_clean_votes(config)
    # (precinct, office_raw, district_raw) -> {votes, ed, mi, pr}
    writeins = defaultdict(lambda: {'votes': 0, 'ed': 0, 'mi': 0, 'pr': 0})

    def decode_writein_columns(header):
        cols = []
        for col_idx in range(1, len(header)):
            raw = header[col_idx]
            if raw is None:
                continue
            decoded = decode_header(raw)
            if not decoded:
                continue
            # Reversed rotated headers can decode with word order scrambled
            # (e.g. 'Write <name> In'), so match loosely on 'write'.
            if 'write' in decoded.lower():
                cols.append(col_idx)
        return cols

    with pdfplumber.open(pdf_path) as pdf:
        current_office, current_district = None, ''
        precinct_state = {'name': None, 'sub_data': {}}

        for page in pdf.pages:
            text = page.extract_text() or ''
            contest_info = parse_contest_title(text, config)
            if contest_info:
                current_office, current_district, _ = contest_info
                precinct_state = {'name': None, 'sub_data': {}}

            if not current_office:
                continue

            for table in page.extract_tables():
                if not table or len(table) < 2:
                    continue
                header = table[0]
                if not header or is_times_cast_table(header):
                    continue
                wi_cols = decode_writein_columns(header)
                if not wi_cols:
                    continue

                current_precinct = precinct_state['name']
                sub_data = precinct_state.get('sub_data', {})

                for row in table[1:]:
                    if not row or not row[0]:
                        continue
                    label = row[0].replace('\n', ' ').strip()

                    if config.is_skip_row(label) and label not in VOTE_TYPES:
                        if label.startswith('Cumulative'):
                            current_precinct = None
                            sub_data = {}
                        continue

                    if label in VOTE_TYPES:
                        if not current_precinct:
                            continue
                        if label not in sub_data:
                            sub_data[label] = {c: 0 for c in wi_cols}
                        for col_idx in wi_cols:
                            if col_idx >= len(row):
                                continue
                            try:
                                sub_data[label][col_idx] += int(
                                    clean_votes(row[col_idx]) or '0')
                            except ValueError:
                                pass

                        if label == 'Total':
                            votes = sum(sub_data['Total'].values())
                            ed = sum(sub_data.get('Election Day', {}).values())
                            mi = sum(sub_data.get('Mail-In', {}).values())
                            pr = sum(sub_data.get('Provisional', {}).values())
                            agg = writeins[(current_precinct, current_office, current_district)]
                            agg['votes'] += votes
                            agg['ed'] += ed
                            agg['mi'] += mi
                            agg['pr'] += pr
                            sub_data = {}
                    else:
                        current_precinct = clean_precinct(row[0])
                        sub_data = {}

                precinct_state['name'] = current_precinct
                precinct_state['sub_data'] = sub_data

    return writeins


def _titlecase(s):
    return ' '.join(
        w.lower() if w.lower() in ('of', 'the', 'and', 'for') else w.title()
        for w in s.split())


def normalize_office(office_text):
    """(office, district) from a contest title like
    'Borough Council (4 Year Term) Bedford Borough'."""
    office = office_text.strip()

    # Magisterial District Judge - District 57-3-03
    mdj = re.match(r'^(.*?)\s*-?\s*District\s+([\d-]+)\s*$', office)
    if mdj and 'judge' in mdj.group(1).lower():
        return 'Magisterial District Judge', mdj.group(2)

    # Retention questions: "Jack Panella Superior Court Retention Question"
    # (both 2023 statewide retention questions were Superior Court races;
    # Stabile's name gets the repo-standard period after the middle initial)
    ret = re.match(r'^(.+?)\s+(Superior Court|Supreme Court|Commonwealth Court|'
                   r'Court of Common Pleas)\s+Retention Question$', office)
    if ret:
        name = ret.group(1).strip()
        if name == 'Victor P Stabile':
            name = 'Victor P. Stabile'
        return f'{ret.group(2)} Retention ({name})', ''

    # "<OFFICE> (N Year Term) [JURISDICTION]"
    m = re.match(r'^(.+?)\s*\((\d+)\s*Year Term\)\s*(.*)$', office)
    if m:
        off = re.sub(r'\s+', ' ', m.group(1)).strip()
        juris = m.group(3).strip()
        district = _titlecase(juris) if juris else ''
        return f'{_titlecase(off)} {m.group(2)} Year Term', district

    return _titlecase(re.sub(r'\s+', ' ', office)), ''


def parse(pdf_path):
    engine_results = parse_sovc_crosstab_results(pdf_path, CONFIG)
    writeins = parse_writeins(pdf_path, CONFIG)

    # normalize the one statewide candidate whose source spelling differs
    # from the repo standard (Bucks/York: 'Harry F. Smail, Jr.')
    name_fix = {'Harry F Smail Jr': 'Harry F. Smail, Jr.'}

    results = []
    for r in engine_results:
        office_raw = r['office']
        if office_raw in ('Registered Voters', 'Ballots Cast'):
            office, district = office_raw, ''
        else:
            office, district = normalize_office(office_raw)
        results.append({
            'county': COUNTY, 'precinct': r['precinct'], 'office': office,
            'district': district, 'party': r['party'],
            'candidate': name_fix.get(r['candidate'], r['candidate']),
            'votes': r['votes'],
            'election_day': r.get('election_day', ''),
            'mail': r.get('mail', ''), 'provisional': r.get('provisional', ''),
        })

    # Merge aggregated write-in votes into the normalized results.
    # Key by normalized (office, district) so titles match engine rows.
    norm_writeins = {}
    for (precinct, office_raw, district_raw), agg in writeins.items():
        if office_raw in ('Registered Voters', 'Ballots Cast'):
            continue
        office, district = normalize_office(office_raw)
        norm_writeins[(precinct, office, district)] = agg

    for r in results:
        key = (r['precinct'], r['office'], r['district'])
        if key in norm_writeins and r['candidate'] == 'Write-ins':
            agg = norm_writeins.pop(key)
            r['votes'] = str(int(r['votes'] or 0) + agg['votes'])
            r['election_day'] = str(int(r['election_day'] or 0) + agg['ed'])
            r['mail'] = str(int(r['mail'] or 0) + agg['mi'])
            r['provisional'] = str(int(r['provisional'] or 0) + agg['pr'])
    for (precinct, office, district), agg in norm_writeins.items():
        if agg['votes']:
            results.append({
                'county': COUNTY, 'precinct': precinct, 'office': office,
                'district': district, 'party': '', 'candidate': 'Write-ins',
                'votes': str(agg['votes']), 'election_day': str(agg['ed']),
                'mail': str(agg['mi']), 'provisional': str(agg['pr']),
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
        print('Usage: python pa_bedford_general_2023_results_parser.py <input_pdf> <output_csv>')
        sys.exit(1)
    results = parse(sys.argv[1])
    write_csv(results, sys.argv[2])


if __name__ == '__main__':
    main()