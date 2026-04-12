#!/usr/bin/env python3
"""
Parser for Indiana County, PA 2025 General Election Results (Electionware format)
Uses NaturalPDF to extract precinct-level results from Electionware Precinct Summary PDF.

This format has:
- Precinct name as a header on each page
- Statistics section with Registered Voters and Ballots Cast
- Contests with candidates listed as "PARTY Name  TOTAL ED MAIL PROV"
- Individual write-in lines (skipped) and Write-In Totals (kept)
- Summary rows: Total Votes Cast, Overvotes, Undervotes, Contest Totals

Usage:
    uv run python parsers/pa_indiana_general_2025_results_parser.py <input_pdf> <output_csv>
"""

import csv
import re
import sys
from pathlib import Path

from natural_pdf import PDF


# Patterns for data lines (all have 4 trailing numbers: TOTAL ED MAIL PROV)
CANDIDATE_RE = re.compile(
    r'^(DEM|REP|LBR|LIB|GRE|IND|NF|CON|D/R)\s+(.+?)\s+(\d[\d,]*)\s+(\d[\d,]*)\s+(\d[\d,]*)\s+(\d[\d,]*)$'
)
YESNO_RE = re.compile(
    r'^(Yes|No)\s+(\d[\d,]*)\s+(\d[\d,]*)\s+(\d[\d,]*)\s+(\d[\d,]*)$'
)
WRITE_IN_TOTALS_RE = re.compile(
    r'^Write-In Totals\s+(\d[\d,]*)\s+(\d[\d,]*)\s+(\d[\d,]*)\s+(\d[\d,]*)$'
)
STATS_LINE_RE = re.compile(
    r'^(Registered Voters - Total|Ballots Cast - Total|Ballots Cast - Blank)\s+([\d,]+)\s*([\d,]*)\s*([\d,]*)\s*([\d,]*)$'
)
VOTE_FOR_RE = re.compile(r'^Vote For\s+(\d+)$')

# Lines to always skip
SKIP_PREFIXES = (
    'Summary Results Report', '2025 Municipal Election', 'November 4, 2025',
    'Precinct Summary', 'Report generated', 'OFFICIAL RESULTS',
    'TOTAL', 'Day', 'Statistics', 'Voter Turnout', 'Write-In:',
    'Not Assigned', 'Total Votes Cast', 'Overvotes', 'Undervotes',
    'Contest Totals',
)

# Office keywords — if an ALL-CAPS line contains any of these, it's an office
OFFICE_KEYWORDS = [
    'JUDGE', 'COURT', 'CORONER', 'REGISTER', 'SHERIFF', 'CONTROLLER',
    'TREASURER', 'COMMISSIONER', 'AUDITOR', 'SUPERVISOR', 'MAYOR',
    'COUNCIL', 'CONSTABLE', 'TAX COLLECTOR', 'SCHOOL DIRECTOR',
    'INSPECTOR', 'RETENTION', 'REFERENDUM', 'PROTHONOTARY',
    'DISTRICT ATTORNEY', 'QUESTION', 'BOROUGH COUNCIL',
    'TOWNSHIP SUPERVISOR', 'CLERK', 'MEMBER OF COUNCIL',
    'MAGISTERIAL',
]


def clean_votes(val):
    if not val:
        return '0'
    return val.replace(',', '').strip()


def extract_precinct_from_page(lines):
    """
    Extract precinct name from the page header lines.
    Format is:
        Summary Results Report  OFFICIAL RESULTS
        2025 Municipal Election
        November 4, 2025        Indiana County
        <PRECINCT NAME>
        Statistics / or contest continues...
    """
    for i, line in enumerate(lines):
        line = line.strip()
        if 'Indiana County' in line:
            # Next non-empty, non-header line is the precinct
            for j in range(i + 1, min(i + 4, len(lines))):
                candidate = lines[j].strip()
                if candidate and not candidate.startswith('Statistics') \
                   and not candidate.startswith('TOTAL') \
                   and not candidate.startswith('OFFICIAL'):
                    return candidate
    return None


def parse_indiana_results(pdf_path):
    """Parse Indiana County Electionware election results PDF."""
    pdf = PDF(pdf_path)
    results = []
    current_precinct = None
    current_office = None
    current_vote_for = '1'
    seen_stats = set()
    seen_write_in = set()  # track (precinct, office) to avoid dup Write-In Totals

    total_pages = len(pdf.pages)
    print(f"Total pages: {total_pages}")

    for page_idx, page in enumerate(pdf.pages):
        text = page.extract_text()
        lines = text.split('\n')

        # Extract precinct from page header
        page_precinct = extract_precinct_from_page(lines)
        if page_precinct and page_precinct != current_precinct:
            current_precinct = page_precinct
            current_office = None

        if not current_precinct:
            continue

        for line in lines:
            line = line.strip()
            if not line:
                continue

            # Skip known non-data lines
            if any(line.startswith(p) for p in SKIP_PREFIXES):
                continue
            if '%' in line:
                continue

            # Skip the precinct name line itself (already extracted)
            if line == current_precinct:
                continue

            # Check for statistics lines (Registered Voters, Ballots Cast)
            stats_match = STATS_LINE_RE.match(line)
            if stats_match:
                stat_type = stats_match.group(1)
                key = (current_precinct, stat_type)
                if key not in seen_stats:
                    seen_stats.add(key)
                    candidate_name = {
                        'Registered Voters - Total': 'Registered Voters',
                        'Ballots Cast - Total': 'Ballots Cast',
                        'Ballots Cast - Blank': 'Ballots Cast - Blank'
                    }[stat_type]

                    row = {
                        'county': 'Indiana',
                        'precinct': current_precinct,
                        'office': '',
                        'district': '',
                        'party': '',
                        'candidate': candidate_name,
                        'vote_for': '',
                        'votes': clean_votes(stats_match.group(2)),
                        'election_day': '',
                        'mail': '',
                        'provisional': '',
                    }
                    if stat_type != 'Registered Voters - Total':
                        row['election_day'] = clean_votes(stats_match.group(3))
                        row['mail'] = clean_votes(stats_match.group(4))
                        row['provisional'] = clean_votes(stats_match.group(5))
                    results.append(row)
                continue

            # Check for Vote For line
            vf_match = VOTE_FOR_RE.match(line)
            if vf_match:
                current_vote_for = vf_match.group(1)
                continue

            # Check for office header — either ALL CAPS with keywords,
            # or mixed case lines that are office names (local offices, retention questions)
            upper_line = line.upper()
            if any(kw in upper_line for kw in OFFICE_KEYWORDS):
                # Verify it's not a candidate or data line
                if not CANDIDATE_RE.match(line) and not YESNO_RE.match(line) \
                   and not WRITE_IN_TOTALS_RE.match(line) and not STATS_LINE_RE.match(line) \
                   and not re.match(r'^\d', line):
                    current_office = line
                    continue

            # Check for candidate line
            if current_office:
                cand_match = CANDIDATE_RE.match(line)
                if cand_match:
                    results.append({
                        'county': 'Indiana',
                        'precinct': current_precinct,
                        'office': current_office,
                        'district': '',
                        'party': cand_match.group(1),
                        'candidate': cand_match.group(2).strip(),
                        'vote_for': current_vote_for,
                        'votes': clean_votes(cand_match.group(3)),
                        'election_day': clean_votes(cand_match.group(4)),
                        'mail': clean_votes(cand_match.group(5)),
                        'provisional': clean_votes(cand_match.group(6)),
                    })
                    continue

                # Check for Yes/No (retention questions, referendums)
                yn_match = YESNO_RE.match(line)
                if yn_match:
                    results.append({
                        'county': 'Indiana',
                        'precinct': current_precinct,
                        'office': current_office,
                        'district': '',
                        'party': '',
                        'candidate': yn_match.group(1),
                        'vote_for': current_vote_for,
                        'votes': clean_votes(yn_match.group(2)),
                        'election_day': clean_votes(yn_match.group(3)),
                        'mail': clean_votes(yn_match.group(4)),
                        'provisional': clean_votes(yn_match.group(5)),
                    })
                    continue

                # Check for Write-In Totals (only first per precinct+office)
                wi_match = WRITE_IN_TOTALS_RE.match(line)
                if wi_match:
                    wi_key = (current_precinct, current_office, current_vote_for)
                    if wi_key not in seen_write_in:
                        seen_write_in.add(wi_key)
                        results.append({
                            'county': 'Indiana',
                            'precinct': current_precinct,
                            'office': current_office,
                            'district': '',
                            'party': '',
                            'candidate': 'Write-In Totals',
                            'vote_for': current_vote_for,
                            'votes': clean_votes(wi_match.group(1)),
                            'election_day': clean_votes(wi_match.group(2)),
                            'mail': clean_votes(wi_match.group(3)),
                            'provisional': clean_votes(wi_match.group(4)),
                        })
                    continue

        if (page_idx + 1) % 200 == 0:
            print(f"  Processed {page_idx + 1} of {total_pages} pages...")

    return results


def write_csv(results, output_path):
    """Write results to OpenElections CSV format."""
    fieldnames = ['county', 'precinct', 'office', 'district', 'party',
                  'candidate', 'vote_for', 'votes', 'election_day', 'mail', 'provisional']

    with open(output_path, 'w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(results)

    print(f"Wrote {len(results)} results to {output_path}")


def main():
    if len(sys.argv) != 3:
        print("Usage: uv run python parsers/pa_indiana_general_2025_results_parser.py <input_pdf> <output_csv>")
        sys.exit(1)

    pdf_path = sys.argv[1]
    output_path = sys.argv[2]

    if not Path(pdf_path).exists():
        print(f"Error: PDF file not found: {pdf_path}", file=sys.stderr)
        sys.exit(1)

    print(f"Parsing {pdf_path}...")
    results = parse_indiana_results(pdf_path)
    write_csv(results, output_path)


if __name__ == '__main__':
    main()
