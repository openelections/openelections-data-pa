#!/usr/bin/env python3
"""
Jefferson County, PA 2023 General Election parser.

Source: "Jefferson County Official Statement of Votes Cast 2023 General.pdf"
(Hart SOVC crosstab, 694 pages: precincts as rows, rotated/reversed candidate
column headers, each precinct block has Election Day / Mail-In / Provisional /
Total sub-rows; '****' redaction markers in sparse cells).

Uses the shared sovc_crosstab_pp engine (vote_type_rows=True, the Jefferson
code path, treat_redacted_as_zero=True). As with Bedford, the engine skips the
per-candidate "Qualified Write-In <name>" / "Unresolved Write-In" columns, so a
second pass re-parses those columns and aggregates them per precinct into a
single "Write-ins" candidate row, keeping candidate totals equal to the
official "Total Votes".

Usage: python pa_jefferson_general_2023_results_parser.py <input_pdf> <output_csv>
"""

import csv
import re
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from sovc_crosstab_pp import (  # noqa: E402
    SovcCrosstabConfig, VOTE_TYPES, decode_header, is_times_cast_table,
    clean_precinct, make_clean_votes, parse_candidate_header,
    parse_contest_title, parse_sovc_crosstab_results,
)

COUNTY = 'Jefferson'


def is_skip_row(label):
    """County/section summary rows to skip (never real precinct rows)."""
    if not label:
        return True
    if label in VOTE_TYPES:
        return False
    if 'Jefferson County' in label:
        return True
    if label.startswith('Cumulative'):
        return True
    if label in ('State', 'State - Total', 'Precinct', 'Precinct Portion'):
        return True
    if 'Total' in label and 'County' in label:
        return True
    return False


CONFIG = SovcCrosstabConfig(
    county=COUNTY,
    vote_type_rows=True,
    is_skip_row=is_skip_row,
    treat_redacted_as_zero=True,
    contest_title_raw_lines=True,
    turnout_requires_registered_header=True,
    turnout_max_pages=7,
)


def parse_writeins(pdf_path, config):
    """Second pass: aggregate the Qualified/Unresolved Write-In columns of
    every candidate table into one 'Write-ins' total per (precinct, contest).
    Mirrors the engine's vote_type_rows candidate-table loop."""
    import pdfplumber

    clean_votes = make_clean_votes(config)
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
            # Reversed rotated headers decode with word order scrambled
            # (e.g. 'BETTY Qualified Write PHILLIPS In'), so match loosely.
            if 'write' in decoded.lower():
                cols.append(col_idx)
        return cols

    with pdfplumber.open(pdf_path) as pdf:
        current_office, current_district = None, ''
        precinct_state = {'name': None, 'sub_data': {}}

        for page_idx, page in enumerate(pdf.pages):
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

            if (page_idx + 1) % 200 == 0:
                print(f'  writein pass: {page_idx + 1} pages...', flush=True)

    return writeins


def build_name_map(pdf_path):
    """Geometry-based decode of candidate column headers.

    pdfplumber merges chars from different rotated header lines into one
    y-band, which scrambles some candidate names at the word level
    (decode_header then yields e.g. 'Kougher Douglas Edward (REP)' for
    'Douglas Edward Kougher (REP)'). Rotated text has one x position per
    visual line, so grouping the cell's chars into x-strips, reading each
    strip bottom-to-top and joining the strips left-to-right recovers the
    true text. Returns {engine_decoded: fixed_decoded}; only entries whose
    token multiset differs from the engine decode are corrections.
    """
    import pdfplumber

    name_map = {}
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            try:
                tables = page.find_tables()
            except Exception:
                continue
            for t in tables:
                if not t.rows:
                    continue
                data = t.extract()
                if not data or len(data) < 2 or len(data[0]) < 3:
                    continue
                header_cells = t.rows[0].cells
                for ci, raw in enumerate(data[0]):
                    if ci == 0 or raw is None or ci >= len(header_cells):
                        continue
                    decoded = decode_header(raw)
                    if not decoded or decoded in name_map:
                        continue
                    low = decoded.lower()
                    if any(s in low for s in (
                            'times cast', 'registered', 'total', 'write',
                            'unresolved')):
                        continue
                    bbox = header_cells[ci]
                    chars = [
                        c for c in page.chars
                        if c['x0'] >= bbox[0] - 1.0 and
                        c['x1'] <= bbox[2] + 1.0 and
                        c['top'] >= bbox[1] - 1.0 and
                        c['bottom'] <= bbox[3] + 1.0
                    ]
                    if not chars:
                        continue
                    fixed = _strip_decode(chars)
                    # only usable when the geometry decode yields the same
                    # tokens (reordered); anything else is a cell this
                    # heuristic cannot decode (e.g. single-line turnout
                    # headers) and must be left alone
                    if fixed and sorted(fixed.split()) == sorted(decoded.split()):
                        # key by the engine-visible candidate name (party
                        # stripped), since that is what fix_candidate sees
                        eng_name, _ = parse_candidate_header(decoded)
                        fixed_name, fixed_party = parse_candidate_header(fixed)
                        if eng_name and fixed_name:
                            name_map[eng_name] = (fixed_name, fixed_party)
        print(f'  name map: {len(name_map)} distinct headers', flush=True)
    return name_map


def _strip_decode(chars):
    """Decode a rotated header cell from raw chars: one x-strip per rotated
    line, read bottom-to-top, strips in ascending x order."""
    ordered = sorted(chars, key=lambda c: c['x0'])
    clusters = []   # [(center_x, [chars])]
    for ch in ordered:
        if clusters and ch['x0'] - clusters[-1][0] <= 1.0:
            clusters[-1][1].append(ch)
        else:
            clusters.append((ch['x0'], [ch]))
    parts = []
    for _, group in clusters:
        line = ''.join(c['text'] for c in
                       sorted(group, key=lambda c: -c['top']))
        if line.strip():
            parts.append(line.strip())
    return ' '.join(parts)


def _titlecase(s):
    return ' '.join(
        w.lower() if w.lower() in ('of', 'the', 'and', 'for', '&') else w.title()
        for w in s.split())


def normalize_office(office_text):
    """(office, district) from a contest title like
    'Borough Council (4 Year Term) - Big Run Borough'."""
    office = re.sub(r'\s+', ' ', office_text.strip())

    # Magisterial District Judge - 54-3-01
    mdj = re.match(r'^(.*?)\s*-\s*([\d]+-[\d]+-[\d]+)\s*$', office)
    if mdj and 'judge' in mdj.group(1).lower():
        return 'Magisterial District Judge', mdj.group(2)

    # "Superior Court - Jack Panella Retention Question"
    # (both 2023 statewide retention questions were Superior Court races)
    ret = re.match(r'^(Superior|Supreme|Commonwealth) Court\s*-\s*(.+?)\s+'
                   r'Retention Question$', office)
    if ret:
        name = ret.group(2).strip()
        if name == 'Victor P Stabile':
            name = 'Victor P. Stabile'
        return f'{ret.group(1)} Court Retention - {name}', ''

    # "<Muni> Referendum Question"
    ref = re.match(r'^(.+?)\s+Referendum Question$', office)
    if ref:
        return 'Referendum', _titlecase(ref.group(1))

    # "<OFFICE> (N [Yy]ear Term) - <JURISDICTION>" (source has a '(4 Tear Term)'
    # typo for McCalmont Township; normalized to Year)
    m = re.match(r'^(.+?)\s*\((\d+)\s*[YyTt]ear Term\)\s*-\s*(.+)$', office)
    if m:
        off = m.group(1).strip()
        juris = m.group(3).strip()
        return f'{_titlecase(off)} {m.group(2)} Year Term', _titlecase(juris)

    # "<OFFICE> (N [Yy]ear Term)" with no jurisdiction
    m = re.match(r'^(.+?)\s*\((\d+)\s*[Yy]ear Term\)\s*$', office)
    if m:
        return f'{_titlecase(m.group(1).strip())} {m.group(2)} Year Term', ''

    return _titlecase(office), ''


def parse(pdf_path):
    engine_results = parse_sovc_crosstab_results(pdf_path, CONFIG)
    writeins = parse_writeins(pdf_path, CONFIG)
    name_map = build_name_map(pdf_path)

    def fix_candidate(cand, party):
        """Correct scrambled rotated-header names via the geometry decode."""
        fixed = name_map.get(cand)
        if not fixed:
            return cand, party
        return fixed[0], (fixed[1] or party)

    results = []
    for r in engine_results:
        office_raw = r['office']
        if office_raw in ('Registered Voters', 'Ballots Cast'):
            office, district = office_raw, ''
        else:
            office, district = normalize_office(office_raw)
        cand, party = fix_candidate(r['candidate'], r['party'])
        cand = {'Harry F Smail Jr': 'Harry F. Smail, Jr.'}.get(cand, cand)
        results.append({
            'county': COUNTY, 'precinct': r['precinct'], 'office': office,
            'district': district, 'party': party,
            'candidate': cand, 'votes': r['votes'],
            'election_day': r.get('election_day', ''),
            'mail': r.get('mail', ''), 'provisional': r.get('provisional', ''),
        })

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
        print('Usage: python pa_jefferson_general_2023_results_parser.py <input_pdf> <output_csv>')
        sys.exit(1)
    results = parse(sys.argv[1])
    write_csv(results, sys.argv[2])


if __name__ == '__main__':
    main()