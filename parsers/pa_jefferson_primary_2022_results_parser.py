#!/usr/bin/env python3
"""
Jefferson County, PA 2022 Primary Election parser.

Source: "Jefferson PA 2022 Primary Precinct Results.pdf"
(Hart SOVC crosstab, 747 pages, dated 5/26/2022 — pre-recount for the GOP
U.S. Senate contest; no amended per-precinct recount file exists for
Jefferson, so these rows are the primary-precinct record).

Adapted from pa_jefferson_general_2022_results_parser.py: identical layout,
but closed-primary contest titles carry a (DEM)/(REP) suffix, and write-in
rows must carry the contest's party (party-empty write-ins fail
duplicate_entries in primaries).

Uses the shared sovc_crosstab_pp engine (vote_type_rows=True, the Jefferson
code path, treat_redacted_as_zero=True). The engine skips the per-candidate
"Qualified Write-In <name>" / "Unresolved Write-In" columns AND the contest's
plain "Write-in" total column, so a second pass re-parses those columns:

- "Qualified/Unresolved Write-In" columns are aggregated per precinct;
- the plain "Write-in" column (contest write-in total, on the last candidate
  table) is captured separately and used as the authoritative value when
  present, so the two column groups never double-count.

Usage: python pa_jefferson_primary_2022_results_parser.py <input_pdf> <output_csv>
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


def contest_party(office_raw):
    """(DEM|REP) from the contest title. Closed-primary statewide titles carry
    a (DEM)/(REP) suffix; the ~111 local 'Democratic/Republican Party
    <office> - <municipality>' committee contests carry the party in the
    office name (they are write-in-only races with no (DEM) suffix)."""
    text = office_raw or ''
    m = re.search(r'\((DEM|REP|LIB|GRN)\)', text)
    if m:
        return m.group(1)
    if re.search(r'\bDemocratic\b', text):
        return 'DEM'
    if re.search(r'\bRepublican\b', text):
        return 'REP'
    return ''


def parse_writeins(pdf_path, config):
    """Second pass over the write-in columns.

    - 'Qualified Write-In <name>' / 'Unresolved Write-In' columns: summed per
      (precinct, contest) with their own vote-type breakdown (-> 'writeins').
    - the plain 'Write-in' column (the contest's own write-in total, on the
      last candidate table): captured separately per (precinct, contest) with
      its vote-type breakdown (-> 'wi_totals').

    Where the two disagree (e.g. unresolved write-ins reported only in the
    total column) parse() prefers the source's own Write-in column.
    Mirrors the engine's vote_type_rows table loop."""
    import pdfplumber

    clean_votes = make_clean_votes(config)
    writeins = defaultdict(lambda: {'votes': 0, 'ed': 0, 'mi': 0, 'pr': 0})
    wi_totals = {}

    def classify_writein_columns(header):
        """Returns (qualified_cols, total_col) for a table header."""
        qualified, total = [], None
        for col_idx in range(1, len(header)):
            raw = header[col_idx]
            if raw is None:
                continue
            decoded = decode_header(raw)
            if not decoded:
                continue
            low = decoded.lower()
            if 'write' not in low:
                continue
            if 'qualified' in low or 'unresolved' in low:
                qualified.append(col_idx)
            else:
                # plain contest 'Write-in' total column
                total = col_idx
        return qualified, total

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
                qualified_cols, total_col = classify_writein_columns(header)
                if not qualified_cols and total_col is None:
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
                            sub_data[label] = {
                                'q': {c: 0 for c in qualified_cols},
                                't': 0}
                        for col_idx in qualified_cols:
                            if col_idx >= len(row):
                                continue
                            try:
                                sub_data[label]['q'][col_idx] += int(
                                    clean_votes(row[col_idx]) or '0')
                            except ValueError:
                                pass
                        if total_col is not None and total_col < len(row):
                            try:
                                # overwrite: repeated totals across tables
                                # of a contest repeat the same value
                                sub_data[label]['t'] = int(
                                    clean_votes(row[total_col]) or '0')
                            except ValueError:
                                pass

                        if label == 'Total':
                            key = (current_precinct, current_office,
                                   current_district)
                            q = sum(sub_data['Total']['q'].values())
                            agg = writeins[key]
                            agg['votes'] += q
                            agg['ed'] += sum(
                                sub_data.get('Election Day', {}).get('q', {}).values())
                            agg['mi'] += sum(
                                sub_data.get('Mail-In', {}).get('q', {}).values())
                            agg['pr'] += sum(
                                sub_data.get('Provisional', {}).get('q', {}).values())
                            if total_col is not None:
                                # contest's own write-in total column: take
                                # votes from the Total sub-row and breakdown
                                # from the vote-type sub-rows
                                t = sub_data['Total']['t']
                                t_ed = sub_data.get('Election Day', {}).get('t', 0)
                                t_mi = sub_data.get('Mail-In', {}).get('t', 0)
                                t_pr = sub_data.get('Provisional', {}).get('t', 0)
                                wi_totals[key] = {
                                    'votes': t, 'ed': t_ed, 'mi': t_mi,
                                    'pr': t_pr,
                                    'bd_ok': t_ed + t_mi + t_pr == t,
                                }
                            sub_data = {}
                    else:
                        current_precinct = clean_precinct(row[0])
                        sub_data = {}

                precinct_state['name'] = current_precinct
                precinct_state['sub_data'] = sub_data

            if (page_idx + 1) % 200 == 0:
                print(f'  writein pass: {page_idx + 1} pages...', flush=True)

    # Reconcile: both column groups SHOULD hold the same contest write-in
    # total; where the plain Write-in column has votes the Qualified list
    # misses (e.g. unresolved write-ins absent from the qualified tables),
    # the source's own Write-in column is authoritative. Log any remaining
    # gaps for review.
    mismatches = []
    for key, agg in list(writeins.items()):
        t = wi_totals.get(key)
        if agg['votes'] == 0 and (t is None or t['votes'] == 0):
            del writeins[key]
            continue
        if t is not None and t['votes'] != agg['votes']:
            mismatches.append((key, agg['votes'], t['votes'],
                               t.get('bd_ok', False)))
    if mismatches:
        n_ok = sum(1 for m in mismatches if m[3])
        print(f'  writein reconcile: {len(mismatches)} (precinct,contest) '
              f'keys where Write-in total col != Qualified/Unresolved sum '
              f'({n_ok} with complete total-column breakdown)', flush=True)
    parse_writeins.mismatches = mismatches  # read back in parse()
    parse_writeins.wi_totals = wi_totals
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


def normalize_office(office_text):
    """(office, district) from a PRIMARY contest title, e.g.
    'Representative in Congress - 15th District (DEM)'."""
    office = re.sub(r'\s+', ' ', office_text.strip())
    office = re.sub(r'\s*\((DEM|REP|LIB|GRN|IND)\)\s*$', '', office).strip()

    # local party-committee contests: keep the printed title verbatim
    # (titlecasing would mangle e.g. 'McCalmont' -> 'Mccalmont')
    if re.match(r'^(Democratic|Republican) Party ', office):
        return office, ''

    if office == 'United States Senator':
        return 'U.S. Senate', ''
    if office in ('Registered Voters', 'Ballots Cast'):
        return office, ''

    m = re.match(r'^Representative in Congress - (\d+)(?:st|nd|rd|th) District$',
                 office, re.I)
    if m:
        return 'U.S. House', m.group(1)

    m = re.match(r'^Representative in the General Assembly - '
                 r'(\d+)(?:st|nd|rd|th) District$', office, re.I)
    if m:
        return 'State House', m.group(1)

    if office == 'Member of the Democratic State Committee':
        return 'Member of Democratic State Committee', ''
    if office == 'Member of the Republican State Committee':
        return 'Member of Republican State Committee', ''

    return ' '.join(
        w.lower() if w.lower() in ('of', 'the', 'and', 'for', '&') else w.title()
        for w in office.split()), ''


def parse(pdf_path):
    engine_results = parse_sovc_crosstab_results(pdf_path, CONFIG)
    writeins_raw = parse_writeins(pdf_path, CONFIG)
    wi_totals = parse_writeins.wi_totals
    name_map = build_name_map(pdf_path)

    def fix_candidate(cand, party):
        """Correct scrambled rotated-header names via the geometry decode."""
        fixed = name_map.get(cand)
        if not fixed:
            return cand, party
        return fixed[0], (fixed[1] or party)

    results = []
    engine_wi = defaultdict(lambda: {'votes': 0, 'election_day': 0, 'mail': 0,
                                     'provisional': 0})
    for r in engine_results:
        office_raw = r['office']
        office, district = normalize_office(office_raw)
        cand, party = fix_candidate(r['candidate'], r['party'])
        if re.fullmatch(r'[-\s]*write\s*-?\s*ins?', (cand or '').lower().strip()):
            # plain 'Write-in' contest-total column leaked through the engine
            # as a pseudocandidate: capture as the authoritative write-in
            # total for reconciliation, never as a row of its own
            key = (r['precinct'], office_raw, district)
            engine_wi[key]['votes'] += int(r['votes'] or 0)
            engine_wi[key]['election_day'] += int(r['election_day'] or 0)
            engine_wi[key]['mail'] += int(r['mail'] or 0)
            engine_wi[key]['provisional'] += int(r['provisional'] or 0)
            continue
        # closed primary: contest rows carry the contest's ballot party
        if not party:
            party = contest_party(office_raw)
        results.append({
            'county': COUNTY, 'precinct': r['precinct'], 'office': office,
            'district': district, 'party': party,
            'candidate': cand, 'votes': r['votes'],
            'election_day': r.get('election_day', ''),
            'mail': r.get('mail', ''), 'provisional': r.get('provisional', ''),
        })

    for (precinct, office_raw, district_raw), agg in writeins_raw.items():
        if office_raw in ('Registered Voters', 'Ballots Cast'):
            continue
        office, district = normalize_office(office_raw)
        party = contest_party(office_raw)
        votes, ed, mi, pr = (agg['votes'], agg['ed'], agg['mi'], agg['pr'])
        t = wi_totals.get((precinct, office_raw, district_raw))
        if t is not None and t['votes'] != votes:
            if t.get('bd_ok'):
                # The source's plain 'Write-in' column is the contest's own
                # write-in total; where it disagrees with the Qualified/
                # Unresolved column sums (e.g. unresolved write-ins reported
                # only in the total column), it is authoritative - and only
                # usable when its own vote-type breakdown is complete.
                votes, ed, mi, pr = (t['votes'], t['ed'], t['mi'], t['pr'])
            else:
                print(f'  writein reconcile gap: {precinct} / {office_raw}: '
                      f'Write-in column={t["votes"]} (breakdown incomplete), '
                      f'Qualified/Unresolved sum={votes}', flush=True)
        if votes:
            results.append({
                'county': COUNTY, 'precinct': precinct, 'office': office,
                'district': district, 'party': party, 'candidate': 'Write Ins',
                'votes': str(votes), 'election_day': str(ed),
                'mail': str(mi), 'provisional': str(pr),
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
        print('Usage: python pa_jefferson_primary_2022_results_parser.py '
              '<input_pdf> <output_csv>')
        sys.exit(1)
    results = parse(sys.argv[1])
    write_csv(results, sys.argv[2])


if __name__ == '__main__':
    main()