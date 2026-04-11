#!/usr/bin/env python3
"""
Parser for PA 2025 General Election Results (county summary PDFs)
Extracts text from PDF using pdftotext -layout and parses into OpenElections CSV format
"""

import subprocess
import csv
import re
import sys
from pathlib import Path


def extract_text_from_pdf(pdf_path):
    """Extract text from PDF using pdftotext -layout"""
    result = subprocess.run(
        ['pdftotext', '-layout', pdf_path, '-'],
        capture_output=True,
        text=True
    )
    return result.stdout


def clean_number(text):
    """Remove commas from numbers and convert to int"""
    if not text or text.strip() == '':
        return 0
    cleaned = text.replace(',', '').strip()
    if not cleaned.isdigit():
        return 0
    return int(cleaned)


def normalize_party(party_text):
    if not party_text:
        return ''
    return party_text.strip().upper()


def parse_election_results(text, county_name):
    """Parse the extracted text and return structured results"""
    lines = text.split('\n')
    results = []

    current_contest = None
    current_district = None
    current_term = None
    reading_candidates = False
    vote_columns = None  # "total" or "breakout"

    i = 0
    while i < len(lines):
        line = lines[i]

        # Skip empty lines and page headers
        if not line.strip() or line.strip().startswith('Page:') or 'Election Summary' in line:
            i += 1
            continue

        # Check if this is a contest header
        # Look for lines with contest keywords and (Vote for N) or (X Year Term)
        if any(keyword in line for keyword in ['Judge of', 'Justice of the Supreme Court',
                     'Supreme Court', 'Superior Court', 'Commonwealth Court',
                     'District Attorney', 'Coroner', 'Prothonotary',
                     'School Director', 'Mayor', 'Council',
                     'Judge of Election', 'Inspector of Election',
                     'Tax Collector', 'Auditor', 'Supervisor', 'Constable',
                     'Retention', 'Retain', 'Referendum', 'Ballot Question',
                     'Magisterial District Judge', 'District Judge']):
            # This is a new contest
            reading_candidates = False
            vote_columns = None

            # Extract contest name and district/jurisdiction
            # Format could be: "Office (Vote for N) District (Vote for N)" or just "Office (Vote for N)"
            contest_line = line.strip()

            # Check if the contest continues on the next line (wrapped text)
            # Look ahead for continuation like "for 2)" which completes "(Vote for 2)"
            if i + 1 < len(lines):
                next_line = lines[i + 1].strip()
                if next_line.startswith('for ') and next_line.endswith(')'):
                    contest_line = contest_line + ' ' + next_line
                    i += 1  # Skip the continuation line

            # Normalize contest line: drop trailing (Vote for N)
            contest_line = re.sub(r'\s+\(Vote for \d+\)\s*$', '', contest_line, flags=re.IGNORECASE)

            # Extract term, if present
            term_match = re.search(r'\((\d+ Year Term)\)', contest_line, flags=re.IGNORECASE)
            current_term = term_match.group(1) if term_match else None
            if term_match:
                contest_line = contest_line.replace(term_match.group(0), '').strip()

            # Extract district for Magisterial District Judge
            if contest_line.lower().startswith('magisterial district judge'):
                current_contest = 'Magisterial District Judge'
                current_district = contest_line[len('Magisterial District Judge'):].strip() or None
            else:
                current_contest = contest_line.strip()
                current_district = None

        # Check for candidate section header
        elif 'Candidate' in line and 'Party' in line:
            reading_candidates = True
            vote_columns = 'breakout' if 'Election Day' in line else 'total'

        # Check for end of candidate section
        elif line.strip().startswith('Unresolved Write-In'):
            # Skip unresolved write-in rows but keep reading candidates
            i += 1
            continue

        # Parse candidate lines
        elif reading_candidates and current_contest:
            # Skip metadata lines
            if any(keyword in line for keyword in ['Times Cast', 'Undervotes', 'Overvotes',
                                                     'Precincts Reported:', 'Registered Voters:',
                                                     'Total Votes', 'Contest Totals']):
                i += 1
                continue
            if 'Election Day' in line and 'Mail-In' in line and 'Provisional' in line:
                i += 1
                continue
            if line.strip().startswith('Candidate') or line.strip().startswith('Party'):
                i += 1
                continue
            if line.strip() == 'Total':
                i += 1
                continue

            # Parse candidate data
            # The line format is either:
            # - CANDIDATE_NAME   PARTY   ELECTION_DAY   MAIL_IN   PROVISIONAL   TOTAL
            # - CANDIDATE_NAME   PARTY   TOTAL
            # Extract all the fields using regex to find numbers and text

            # Look for candidate name (starts at beginning, all caps or Write-in or YES/NO)
            columns = re.split(r'\s{2,}', line.strip())
            candidate = None
            party = ''
            remaining_text = ''

            if len(columns) >= 2:
                candidate = columns[0].strip()
                if re.fullmatch(r'[\d,]+', columns[1].strip()):
                    party = ''
                    remaining_text = ' '.join(columns[1:])
                else:
                    party = normalize_party(columns[1])
                    remaining_text = ' '.join(columns[2:])
            else:
                party_match = re.match(r'^(.+?)\s+(WRITE-IN|DEM/REP|REP/DEM|DEM|REP|LIB|IND|GRE|LBR|CST|FWD|ASP|DAR)\s+(.+)$', line.strip(), re.IGNORECASE)
                if party_match:
                    candidate = party_match.group(1).strip()
                    party = normalize_party(party_match.group(2))
                    remaining_text = party_match.group(3)

            if candidate:
                if candidate.upper() in {'ELECTION DAY', 'MAIL-IN', 'PROVISIONAL', 'TOTAL', 'CANDIDATE'}:
                    i += 1
                    continue
                if party in {'ELECTION', 'MAIL-IN', 'PROVISIONAL', 'TOTAL', 'CANDIDATE', 'PARTY'}:
                    i += 1
                    continue
                numbers = re.findall(r'[\d,]+', remaining_text) if remaining_text else re.findall(r'[\d,]+', line)

                # If numbers wrapped to the next line, pull them in (breakout columns only)
                if vote_columns == 'breakout':
                    while len(numbers) < 4 and i + 1 < len(lines):
                        next_line = lines[i + 1]
                        if re.search(r'\d', next_line) and not any(k in next_line for k in ['Candidate', 'Precincts Reported', 'Election Summary']):
                            numbers.extend(re.findall(r'[\d,]+', next_line))
                            i += 1
                        else:
                            break

                if vote_columns == 'breakout' and len(numbers) >= 4:
                    election_day = clean_number(numbers[0])
                    mail_in = clean_number(numbers[1])
                    provisional = clean_number(numbers[2])
                    total = clean_number(numbers[3])
                elif vote_columns == 'total' and len(numbers) >= 1:
                    total = clean_number(numbers[-1])
                    election_day = ''
                    mail_in = ''
                    provisional = ''
                else:
                    i += 1
                    continue

                # Build office and district values
                office_name = current_contest
                district_value = current_district if current_district else ''

                # Append term to office name if present and not already included
                if current_term and current_term.lower() not in office_name.lower():
                    office_name = f"{office_name} {current_term}"

                # Create result record
                result = {
                    'county': county_name,
                    'office': office_name,
                    'district': district_value,
                    'party': party,
                    'candidate': candidate,
                    'votes': total,
                    'election_day': election_day,
                    'mail': mail_in,
                    'provisional': provisional
                }
                results.append(result)

        i += 1

    return results


def write_csv(results, output_path):
    """Write results to CSV in OpenElections format"""
    fieldnames = ['county', 'office', 'district', 'party', 'candidate',
                  'votes', 'election_day', 'mail', 'provisional']

    with open(output_path, 'w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(results)


def main():
    if len(sys.argv) < 2:
        print("Usage: python pa_warren_general_2025_results_parser.py <pdf_path> [output_csv] [county_name]")
        sys.exit(1)

    pdf_path = sys.argv[1]
    output_path = sys.argv[2] if len(sys.argv) > 2 else None
    county_name = sys.argv[3] if len(sys.argv) > 3 else 'Warren'
    if not output_path:
        output_path = f"20251104__pa__general__{county_name.lower()}__county.csv"

    print(f"Extracting text from {pdf_path}...")
    text = extract_text_from_pdf(pdf_path)

    print("Parsing election results...")
    results = parse_election_results(text, county_name)

    print(f"Found {len(results)} result records")
    print(f"Writing to {output_path}...")
    write_csv(results, output_path)

    print("Done!")


if __name__ == '__main__':
    main()
