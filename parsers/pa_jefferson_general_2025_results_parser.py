#!/usr/bin/env python3
"""
Parser for Jefferson County, PA 2025 General Election Results (SOVC format with vote types)
Uses pdfplumber to extract precinct-level results from Statement of Votes Cast PDF.

Like Bedford's SOVC format but each precinct has 4 sub-rows:
  Election Day, Mail-In, Provisional, Total
Column headers are reversed strings due to rotated text.

Usage:
    uv run python parsers/pa_jefferson_general_2025_results_parser.py <input_pdf> <output_csv>
"""

import csv
import re
import sys
from pathlib import Path

import pdfplumber


VOTE_TYPES = {'Election Day', 'Mail-In', 'Provisional', 'Total'}


def decode_header(raw):
    """Decode reversed/rotated column headers from SOVC PDF."""
    if not raw:
        return ''
    lines = raw.split('\n')
    parts = [line[::-1].strip() for line in lines if line.strip()]
    parts.reverse()
    return ' '.join(parts).strip()


def parse_candidate_header(decoded):
    """Parse decoded header into (candidate_name, party) or (None, None) to skip."""
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

    # Normalize cross-filed parties: DEMREP -> DEM/REP
    if party and len(party) > 3 and '/' not in party:
        party_parts = [party[i:i+3] for i in range(0, len(party), 3)]
        party = '/'.join(party_parts)

    name = re.sub(r'\s*\([A-Z/]+\)\s*', ' ', decoded).strip()
    name = re.sub(r'\s+', ' ', name)
    return name, party


def parse_contest_title(text):
    """Parse contest title. Returns (office, district, vote_for) or None."""
    for line in text.split('\n')[:6]:
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


def is_skip_row(precinct):
    """Check if row should be skipped (county headers, cumulative, totals)."""
    if not precinct:
        return True
    p = precinct.replace('\n', ' ').strip()
    if p in ('Jefferson County', 'Cumulative', ''):
        return True
    if 'Cumulative' in p:
        return True
    if 'Total' in p and 'County' in p:
        return True
    return False


def clean_precinct(precinct):
    return precinct.replace('\n', ' ').strip()


def clean_votes(val):
    if not val or val.strip() == '' or '****' in str(val):
        return '0'
    return val.replace(',', '').strip()


def is_times_cast_table(header):
    if len(header) < 2 or not header[1]:
        return False
    return 'tsaC' in str(header[1]) or 'semiT' in str(header[1])


def parse_turnout(pdf):
    """
    Parse voter turnout from first pages.
    Each precinct has sub-rows: Election Day, Mail-In, Provisional, Total.
    We want: registered_voters (from Total row), ballots_cast (Total),
    plus election_day, mail_in, provisional breakdowns.
    """
    turnout = {}
    current_precinct = None

    # Turnout table may span several pages
    for page_idx in range(min(7, len(pdf.pages))):
        page = pdf.pages[page_idx]
        text = page.extract_text() or ''
        # Stop if we hit a contest page
        if 'Vote for' in text:
            break

        tables = page.extract_tables()
        for table in tables:
            if not table or len(table) < 2:
                continue
            header = table[0]
            # Must be the turnout table
            if not header or 'Registered' not in str(header[1] or ''):
                continue

            for row in table[1:]:
                if not row or not row[0]:
                    continue
                label = row[0].replace('\n', ' ').strip()

                if is_skip_row(label):
                    if label not in VOTE_TYPES:
                        continue

                # Is this a precinct name or a vote-type sub-row?
                if label in VOTE_TYPES:
                    if current_precinct and label == 'Total':
                        reg = clean_votes(row[1] if len(row) > 1 else '0')
                        ballots = clean_votes(row[2] if len(row) > 2 else '0')
                        if current_precinct not in turnout:
                            turnout[current_precinct] = {}
                        turnout[current_precinct]['registered_voters'] = reg
                        turnout[current_precinct]['ballots_cast'] = ballots
                    elif current_precinct and label == 'Election Day':
                        if current_precinct not in turnout:
                            turnout[current_precinct] = {}
                        turnout[current_precinct]['election_day'] = clean_votes(row[2] if len(row) > 2 else '0')
                    elif current_precinct and label == 'Mail-In':
                        if current_precinct not in turnout:
                            turnout[current_precinct] = {}
                        turnout[current_precinct]['mail'] = clean_votes(row[2] if len(row) > 2 else '0')
                    elif current_precinct and label == 'Provisional':
                        if current_precinct not in turnout:
                            turnout[current_precinct] = {}
                        turnout[current_precinct]['provisional'] = clean_votes(row[2] if len(row) > 2 else '0')
                else:
                    # This is a precinct name
                    current_precinct = label

    return turnout


def parse_candidate_table(table, candidates, current_precinct_state):
    """
    Parse a candidate results table with sub-rows per precinct.
    Returns list of result dicts and updated precinct state.

    Each precinct block:
      Precinct Name row (empty vote cells)
      Election Day row
      Mail-In row
      Provisional row
      Total row
    """
    rows_out = []
    current_precinct = current_precinct_state['name']
    sub_data = current_precinct_state.get('sub_data', {})

    for row in table[1:]:
        if not row or not row[0]:
            continue
        label = row[0].replace('\n', ' ').strip()

        if is_skip_row(label) and label not in VOTE_TYPES:
            # Could be "Cumulative" or county total - skip
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
                    sub_data[cand_name] = {'party': party, 'election_day': '0', 'mail': '0', 'provisional': '0', 'total': '0'}

                if label == 'Election Day':
                    sub_data[cand_name]['election_day'] = val
                elif label == 'Mail-In':
                    sub_data[cand_name]['mail'] = val
                elif label == 'Provisional':
                    sub_data[cand_name]['provisional'] = val
                elif label == 'Total':
                    sub_data[cand_name]['total'] = val

            # When we hit Total, emit all candidate rows for this precinct
            if label == 'Total':
                for cand_name in sub_data:
                    d = sub_data[cand_name]
                    rows_out.append({
                        'precinct': current_precinct,
                        'candidate': cand_name,
                        'party': d['party'],
                        'votes': d['total'],
                        'election_day': d['election_day'],
                        'mail': d['mail'],
                        'provisional': d['provisional'],
                    })
                sub_data = {}
        else:
            # New precinct name
            current_precinct = label
            sub_data = {}

    current_precinct_state['name'] = current_precinct
    current_precinct_state['sub_data'] = sub_data
    return rows_out


def parse_jefferson_results(pdf_path):
    """Parse Jefferson County SOVC election results PDF."""
    results = []

    with pdfplumber.open(pdf_path) as pdf:
        print(f"Total pages: {len(pdf.pages)}")

        # Step 1: Parse turnout
        turnout = parse_turnout(pdf)
        print(f"Found {len(turnout)} precincts in turnout table")

        for precinct in sorted(turnout.keys()):
            t = turnout[precinct]
            results.append({
                'county': 'Jefferson', 'precinct': precinct,
                'office': '', 'district': '', 'party': '',
                'candidate': 'Registered Voters', 'vote_for': '',
                'votes': t.get('registered_voters', '0'),
                'election_day': '', 'mail': '', 'provisional': '',
            })
            results.append({
                'county': 'Jefferson', 'precinct': precinct,
                'office': '', 'district': '', 'party': '',
                'candidate': 'Ballots Cast', 'vote_for': '',
                'votes': t.get('ballots_cast', '0'),
                'election_day': t.get('election_day', '0'),
                'mail': t.get('mail', '0'),
                'provisional': t.get('provisional', '0'),
            })

        # Step 2: Parse contests
        current_office = None
        current_district = ''
        current_vote_for = '1'
        # Track precinct state across pages (a precinct block can span page boundary)
        precinct_state = {'name': None, 'sub_data': {}}

        for page_idx in range(len(pdf.pages)):
            page = pdf.pages[page_idx]
            text = page.extract_text() or ''

            contest_info = parse_contest_title(text)
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
                if not header:
                    continue
                if is_times_cast_table(header):
                    continue

                # Decode candidate columns
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

                if not candidates:
                    continue

                row_results = parse_candidate_table(table, candidates, precinct_state)
                for r in row_results:
                    results.append({
                        'county': 'Jefferson',
                        'precinct': r['precinct'],
                        'office': current_office,
                        'district': current_district,
                        'party': r['party'],
                        'candidate': r['candidate'],
                        'vote_for': current_vote_for,
                        'votes': r['votes'],
                        'election_day': r['election_day'],
                        'mail': r['mail'],
                        'provisional': r['provisional'],
                    })

            if (page_idx + 1) % 100 == 0:
                print(f"  Processed {page_idx + 1} pages...")

    return results


def write_csv(results, output_path):
    fieldnames = ['county', 'precinct', 'office', 'district', 'party',
                  'candidate', 'vote_for', 'votes', 'election_day', 'mail', 'provisional']

    with open(output_path, 'w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(results)

    print(f"Wrote {len(results)} results to {output_path}")


def main():
    if len(sys.argv) != 3:
        print("Usage: uv run python parsers/pa_jefferson_general_2025_results_parser.py <input_pdf> <output_csv>")
        sys.exit(1)

    pdf_path = sys.argv[1]
    output_path = sys.argv[2]

    if not Path(pdf_path).exists():
        print(f"Error: PDF file not found: {pdf_path}", file=sys.stderr)
        sys.exit(1)

    print(f"Parsing {pdf_path}...")
    results = parse_jefferson_results(pdf_path)
    write_csv(results, output_path)


if __name__ == '__main__':
    main()
