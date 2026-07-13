"""Shared pdfplumber engine for rotated-header "Statement of Votes Cast"
crosstab reports (Bedford, Jefferson): precincts as rows, candidates as
columns, with column headers stored as reversed/rotated text.

Both counties share: ``decode_header`` (un-reversing rotated column text),
``parse_candidate_header`` (party/candidate extraction, cross-filed party
normalization), ``is_times_cast_table`` (skipping the registered-voters
crosstab), and the same two-phase shape (turnout table(s) first, then
per-contest candidate tables). They differ in one structural way that drives
everything else:

  - Bedford: one row per precinct per table, one vote total per candidate.
  - Jefferson: each precinct is FOUR sub-rows (Election Day, Mail-In,
    Provisional, Total), so turnout parsing and candidate-table parsing both
    need to track a running sub-row state and only emit once the "Total"
    sub-row is seen. This also changes what counts as a "skip" row: Bedford
    skips any row containing "Total" (it has no per-precinct Total sub-row
    to protect); Jefferson must NOT skip its own "Total" sub-rows, only
    county-wide "... Total ... County" summary rows.

``config.vote_type_rows`` selects between the two turnout/table-parsing code
paths below; each path is preserved close to verbatim from the original
per-county scripts since there's no source PDF in the repo to golden-test
the merge against.
"""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from typing import Callable, Optional

VOTE_TYPES = {'Election Day', 'Mail-In', 'Provisional', 'Total'}


@dataclass(frozen=True)
class SovcCrosstabConfig:
    county: str
    vote_type_rows: bool  # Jefferson: precinct blocks have ED/Mail-In/Provisional/Total sub-rows
    is_skip_row: Callable  # predicate(cleaned_label) -> bool
    treat_redacted_as_zero: bool = False  # Jefferson: '****' redaction markers -> '0'
    contest_title_raw_lines: bool = False  # Jefferson scans first 6 raw lines; Bedford first 6 non-empty
    turnout_requires_registered_header: bool = False  # Jefferson only trusts tables headed "...Registered..."
    turnout_max_pages: int = 2  # Bedford: fixed 2; Jefferson: up to 7, stops at first contest page


def decode_header(raw):
    """Decode reversed column headers from a rotated-text SOVC PDF."""
    if not raw:
        return ''
    lines = raw.split('\n')
    parts = [line[::-1].strip() for line in lines if line.strip()]
    parts.reverse()
    return ' '.join(parts).strip()


def parse_candidate_header(decoded):
    """Parse a decoded header into (candidate_name, party), or (None, None) to skip."""
    if not decoded:
        return None, None

    skip_lower = decoded.lower()
    if any(s in skip_lower for s in ['times cast', 'registered voters', 'votes total',
                                      'total votes', 'unresolved', 'write-in']):
        return None, None
    if 'write' in skip_lower and 'qualified' in skip_lower:
        return None, None
    if 'qualified write' in skip_lower:
        return None, None

    if decoded.lower() in ('yes', 'no'):
        return decoded.capitalize(), ''

    party_match = re.search(r'\(([A-Z/]+)\)', decoded)
    party = party_match.group(1) if party_match else ''

    if party and len(party) > 3 and '/' not in party:
        party_parts = [party[i:i + 3] for i in range(0, len(party), 3)]
        party = '/'.join(party_parts)

    name = re.sub(r'\s*\([A-Z/]+\)\s*', ' ', decoded).strip()
    name = re.sub(r'\s+', ' ', name)
    return name, party


def parse_contest_title(text, config: SovcCrosstabConfig):
    """Parse contest title from page text. Returns (office, district, vote_for) or None."""
    if config.contest_title_raw_lines:
        candidate_lines = text.split('\n')[:6]
    else:
        candidate_lines = [l.strip() for l in text.split('\n') if l.strip()][:6]

    for line in candidate_lines:
        line = line.strip()
        if 'Vote for' in line:
            vote_for_match = re.search(r'\(Vote for\s+(\d+)\)', line)
            vote_for = vote_for_match.group(1) if vote_for_match else '1'
            office = re.sub(r'\s*\(Vote for\s+\d+\)', '', line).strip()
            district = ''
            dist_match = re.search(r'District\s+([\d-]+)', office)
            if dist_match:
                district = dist_match.group(1)
            return office, district, vote_for
    return None


def clean_precinct(precinct):
    return precinct.replace('\n', ' ').strip()


def make_clean_votes(config: SovcCrosstabConfig):
    def clean_votes(val):
        if not val or str(val).strip() == '':
            return '0'
        if config.treat_redacted_as_zero and '****' in str(val):
            return '0'
        return val.replace(',', '').strip()
    return clean_votes


def is_times_cast_table(header):
    if len(header) < 2 or not header[1]:
        return False
    return 'tsaC' in str(header[1]) or 'semiT' in str(header[1])


def decode_candidates(header, config: SovcCrosstabConfig):
    """Decode a table header row into [(col_idx, candidate_name, party), ...]."""
    candidates = []
    for col_idx in range(1, len(header)):
        raw = header[col_idx]
        if raw is None:
            continue
        decoded = decode_header(raw)
        if not decoded:
            continue
        name, party = parse_candidate_header(decoded)
        if name is not None:
            candidates.append((col_idx, name, party))
    return candidates


# --- Bedford-style: one row per precinct, no vote-type sub-rows ---

def _parse_turnout_simple(pages_tables, config: SovcCrosstabConfig, clean_votes):
    """pages_tables: list of list-of-tables (one entry per page)."""
    turnout = {}
    for tables in pages_tables[:config.turnout_max_pages]:
        for table in tables:
            if not table or len(table) < 2:
                continue
            for row in table[1:]:
                if not row or len(row) < 3:
                    continue
                precinct = row[0]
                if not precinct or config.is_skip_row(clean_precinct(precinct)):
                    continue
                precinct = clean_precinct(precinct)
                reg_voters = clean_votes(row[1] if len(row) > 1 else '0')
                ballots = clean_votes(row[2] if len(row) > 2 else '0')
                turnout[precinct] = (reg_voters, ballots)
    return turnout


def _parse_candidate_table_simple(table, candidates, config: SovcCrosstabConfig, clean_votes):
    rows_out = []
    for row in table[1:]:
        if not row or not row[0]:
            continue
        precinct = row[0]
        if config.is_skip_row(clean_precinct(precinct)):
            continue
        precinct = clean_precinct(precinct)

        for col_idx, candidate_name, party in candidates:
            if col_idx >= len(row):
                continue
            votes = clean_votes(row[col_idx])
            rows_out.append({
                'precinct': precinct, 'candidate': candidate_name, 'party': party, 'votes': votes,
            })
    return rows_out


# --- Jefferson-style: precinct blocks with Election Day/Mail-In/Provisional/Total sub-rows ---

def _parse_turnout_vote_types(pdf_pages, config: SovcCrosstabConfig, clean_votes):
    """pdf_pages: list of (page_text, page_tables) tuples for the pages to scan."""
    turnout = {}
    current_precinct = None

    for text, tables in pdf_pages[:7]:
        if 'Vote for' in text:
            break

        for table in tables:
            if not table or len(table) < 2:
                continue
            header = table[0]
            if not header or 'Registered' not in str(header[1] or ''):
                continue

            for row in table[1:]:
                if not row or not row[0]:
                    continue
                label = row[0].replace('\n', ' ').strip()

                if config.is_skip_row(label) and label not in VOTE_TYPES:
                    # A skip-worthy label here is always a section/county-total
                    # boundary (e.g. "Cumulative", "Jefferson County - Total"),
                    # never a real precinct row. Clear current_precinct so the
                    # cumulative vote-method breakdown rows that follow aren't
                    # misattributed to the last real precinct seen (see the
                    # identical reset in _parse_candidate_table_vote_types).
                    current_precinct = None
                    continue

                if label in VOTE_TYPES:
                    if current_precinct and label == 'Total':
                        reg = clean_votes(row[1] if len(row) > 1 else '0')
                        ballots = clean_votes(row[2] if len(row) > 2 else '0')
                        turnout.setdefault(current_precinct, {})
                        turnout[current_precinct]['registered_voters'] = reg
                        turnout[current_precinct]['ballots_cast'] = ballots
                    elif current_precinct and label == 'Election Day':
                        turnout.setdefault(current_precinct, {})
                        turnout[current_precinct]['election_day'] = clean_votes(row[2] if len(row) > 2 else '0')
                    elif current_precinct and label == 'Mail-In':
                        turnout.setdefault(current_precinct, {})
                        turnout[current_precinct]['mail'] = clean_votes(row[2] if len(row) > 2 else '0')
                    elif current_precinct and label == 'Provisional':
                        turnout.setdefault(current_precinct, {})
                        turnout[current_precinct]['provisional'] = clean_votes(row[2] if len(row) > 2 else '0')
                else:
                    current_precinct = label

    return turnout


def _parse_candidate_table_vote_types(table, candidates, precinct_state, config: SovcCrosstabConfig, clean_votes):
    rows_out = []
    current_precinct = precinct_state['name']
    sub_data = precinct_state.get('sub_data', {})

    for row in table[1:]:
        if not row or not row[0]:
            continue
        label = row[0].replace('\n', ' ').strip()

        if config.is_skip_row(label) and label not in VOTE_TYPES:
            if label == 'Cumulative':
                current_precinct = None
                sub_data = {}
            continue

        if label in VOTE_TYPES:
            if not current_precinct:
                continue

            for col_idx, cand_name, party in candidates:
                if col_idx >= len(row):
                    continue
                val = clean_votes(row[col_idx])

                if cand_name not in sub_data:
                    sub_data[cand_name] = {'party': party, 'election_day': '0', 'mail': '0',
                                            'provisional': '0', 'total': '0'}

                if label == 'Election Day':
                    sub_data[cand_name]['election_day'] = val
                elif label == 'Mail-In':
                    sub_data[cand_name]['mail'] = val
                elif label == 'Provisional':
                    sub_data[cand_name]['provisional'] = val
                elif label == 'Total':
                    sub_data[cand_name]['total'] = val

            if label == 'Total':
                for cand_name in sub_data:
                    d = sub_data[cand_name]
                    rows_out.append({
                        'precinct': current_precinct, 'candidate': cand_name, 'party': d['party'],
                        'votes': d['total'], 'election_day': d['election_day'],
                        'mail': d['mail'], 'provisional': d['provisional'],
                    })
                sub_data = {}
        else:
            current_precinct = label
            sub_data = {}

    precinct_state['name'] = current_precinct
    precinct_state['sub_data'] = sub_data
    return rows_out


def parse_sovc_crosstab_results(pdf_path, config: SovcCrosstabConfig):
    import pdfplumber

    clean_votes = make_clean_votes(config)
    results = []

    with pdfplumber.open(pdf_path) as pdf:
        print(f"Total pages: {len(pdf.pages)}")

        if config.vote_type_rows:
            pdf_pages = [(p.extract_text() or '', p.extract_tables()) for p in pdf.pages[:7]]
            turnout = _parse_turnout_vote_types(pdf_pages, config, clean_votes)
            print(f"Found {len(turnout)} precincts in turnout table")

            for precinct in sorted(turnout.keys()):
                t = turnout[precinct]
                results.append({
                    'county': config.county, 'precinct': precinct, 'office': 'Registered Voters', 'district': '', 'party': '',
                    'candidate': '', 'vote_for': '', 'votes': t.get('registered_voters', '0'),
                    'election_day': '', 'mail': '', 'provisional': '',
                })
                results.append({
                    'county': config.county, 'precinct': precinct, 'office': 'Ballots Cast', 'district': '', 'party': '',
                    'candidate': '', 'vote_for': '', 'votes': t.get('ballots_cast', '0'),
                    'election_day': t.get('election_day', '0'), 'mail': t.get('mail', '0'),
                    'provisional': t.get('provisional', '0'),
                })

            current_office, current_district, current_vote_for = None, '', '1'
            precinct_state = {'name': None, 'sub_data': {}}

            for page_idx, page in enumerate(pdf.pages):
                text = page.extract_text() or ''

                contest_info = parse_contest_title(text, config)
                if contest_info:
                    current_office, current_district, current_vote_for = contest_info
                    precinct_state = {'name': None, 'sub_data': {}}

                if not current_office:
                    continue

                tables = page.extract_tables()
                if not tables:
                    continue

                for table in tables:
                    if not table or len(table) < 2:
                        continue
                    header = table[0]
                    if not header or is_times_cast_table(header):
                        continue

                    candidates = decode_candidates(header, config)
                    if not candidates:
                        continue

                    row_results = _parse_candidate_table_vote_types(table, candidates, precinct_state, config, clean_votes)
                    for r in row_results:
                        results.append({
                            'county': config.county, 'precinct': r['precinct'], 'office': current_office,
                            'district': current_district, 'party': r['party'], 'candidate': r['candidate'],
                            'vote_for': current_vote_for, 'votes': r['votes'],
                            'election_day': r['election_day'], 'mail': r['mail'], 'provisional': r['provisional'],
                        })

                if (page_idx + 1) % 100 == 0:
                    print(f"  Processed {page_idx + 1} pages...")

        else:
            pages_tables = [p.extract_tables() for p in pdf.pages[:config.turnout_max_pages]]
            turnout = _parse_turnout_simple(pages_tables, config, clean_votes)
            print(f"Found {len(turnout)} precincts in turnout table")

            for precinct, (reg, ballots) in sorted(turnout.items()):
                results.append({
                    'county': config.county, 'precinct': precinct, 'office': 'Registered Voters', 'district': '', 'party': '',
                    'candidate': '', 'vote_for': '', 'votes': reg,
                })
                results.append({
                    'county': config.county, 'precinct': precinct, 'office': 'Ballots Cast', 'district': '', 'party': '',
                    'candidate': '', 'vote_for': '', 'votes': ballots,
                })

            current_office, current_district, current_vote_for = None, '', '1'

            for page_idx in range(config.turnout_max_pages, len(pdf.pages)):
                page = pdf.pages[page_idx]
                text = page.extract_text() or ''

                contest_info = parse_contest_title(text, config)
                if contest_info:
                    current_office, current_district, current_vote_for = contest_info

                if not current_office:
                    continue

                tables = page.extract_tables()
                if not tables:
                    continue

                for table in tables:
                    if not table or len(table) < 2:
                        continue
                    header = table[0]
                    if not header or is_times_cast_table(header):
                        continue

                    candidates = decode_candidates(header, config)
                    if not candidates:
                        continue

                    row_results = _parse_candidate_table_simple(table, candidates, config, clean_votes)
                    for r in row_results:
                        results.append({
                            'county': config.county, 'precinct': r['precinct'], 'office': current_office,
                            'district': current_district, 'party': r['party'], 'candidate': r['candidate'],
                            'vote_for': current_vote_for, 'votes': r['votes'],
                        })

                if (page_idx + 1) % 100 == 0:
                    print(f"  Processed {page_idx + 1} pages...")

    return results


def write_csv(results, output_path, config: SovcCrosstabConfig):
    if config.vote_type_rows:
        fieldnames = ['county', 'precinct', 'office', 'district', 'party',
                      'candidate', 'vote_for', 'votes', 'election_day', 'mail', 'provisional']
    else:
        fieldnames = ['county', 'precinct', 'office', 'district', 'party',
                      'candidate', 'vote_for', 'votes']

    with open(output_path, 'w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(results)

    print(f"Wrote {len(results)} results to {output_path}")


def run_cli(config: SovcCrosstabConfig, argv=None):
    import sys
    from pathlib import Path

    argv = argv if argv is not None else sys.argv[1:]
    if len(argv) != 2:
        print(f"Usage: uv run python {sys.argv[0]} <input_pdf> <output_csv>")
        sys.exit(1)

    pdf_path, output_path = argv

    if not Path(pdf_path).exists():
        print(f"Error: PDF file not found: {pdf_path}", file=sys.stderr)
        sys.exit(1)

    print(f"Parsing {pdf_path}...")
    results = parse_sovc_crosstab_results(pdf_path, config)
    write_csv(results, output_path, config)
