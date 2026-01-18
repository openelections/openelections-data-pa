#!/usr/bin/env python3
"""
Parser for Warren County, PA 2025 General Election Results
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
    return int(text.replace(',', '').strip())


def parse_election_results(text):
    """Parse the extracted text and return structured results"""
    lines = text.split('\n')
    results = []

    current_contest = None
    current_district = None
    reading_candidates = False

    i = 0
    while i < len(lines):
        line = lines[i]

        # Skip empty lines and page headers
        if not line.strip() or line.strip().startswith('Page:') or 'Election Summary' in line:
            i += 1
            continue

        # Check if this is a contest header
        # Look for lines with contest keywords and (Vote for N) or (X Year Term)
        if any(keyword in line for keyword in ['Judge of', 'District Attorney', 'Coroner',
                                                 'Prothonotary', 'School Director', 'Mayor',
                                                 'Council', 'Judge of Election', 'Inspector of Election',
                                                 'Tax Collector', 'Auditor', 'Supervisor', 'Constable',
                                                 'Retention']):
            # This is a new contest
            reading_candidates = False

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

            # Try to extract district from patterns like "School Director (4 Year Term) Warren County Region 1 School District (Vote for 2)"
            # or "Judge of Election (4 Year Term) Warren Central (Vote for 1)"
            if '(Vote for' in contest_line or '(4 Year Term)' in contest_line or '(2 Year Term)' in contest_line:
                # Remove the trailing (Vote for N)
                contest_line = re.sub(r'\s+\(Vote for \d+\)\s*$', '', contest_line)

                # Check if there's a district/jurisdiction after the term
                # Pattern: Office (Term) District
                match = re.match(r'^(.+?)\s+\((?:4 Year Term|2 Year Term)\)\s+(.+)$', contest_line)
                if match:
                    current_contest = match.group(1).strip()
                    current_district = match.group(2).strip()
                else:
                    # No district, just clean up the office name
                    current_contest = re.sub(r'\s+\((?:4 Year Term|2 Year Term)\)', '', contest_line).strip()
                    current_district = None
            else:
                current_contest = contest_line
                current_district = None

        # Check for candidate section header
        elif 'Candidate' in line and 'Party' in line and 'Election Day' in line:
            reading_candidates = True

        # Check for end of candidate section
        elif line.strip().startswith('Unresolved Write-In') or line.strip().startswith('Total Votes'):
            reading_candidates = False

        # Parse candidate lines
        elif reading_candidates and current_contest:
            # Skip metadata lines
            if any(keyword in line for keyword in ['Times Cast', 'Undervotes', 'Overvotes',
                                                     'Precincts Reported:', 'Registered Voters:']):
                i += 1
                continue

            # Parse candidate data
            # The line format is: CANDIDATE_NAME   PARTY   ELECTION_DAY   MAIL_IN   PROVISIONAL   TOTAL
            # Extract all the fields using regex to find numbers and text

            # Look for candidate name (starts at beginning, all caps or Write-in or YES/NO)
            candidate_match = re.match(r'^([A-Z\s\'.]+?|YES|NO)(?:\s{2,})', line)
            if candidate_match:
                candidate = candidate_match.group(1).strip()

                # Extract party (3-7 letter code like REP, DEM, LIB, IND, DEM/REP)
                # Try to match combined parties first (DEM/REP), then single parties
                party_match = re.search(r'\b(DEM/REP|REP/DEM|DEM|REP|LIB|IND|GRE)\b', line)
                party = party_match.group(1) if party_match else ''

                # Extract all numbers from the line
                numbers = re.findall(r'[\d,]+', line)

                # We expect 4 numbers: election_day, mail_in, provisional, total
                # Sometimes there's a percentage at the end, so take first 4
                if len(numbers) >= 4:
                    election_day = clean_number(numbers[0])
                    mail_in = clean_number(numbers[1])
                    provisional = clean_number(numbers[2])
                    total = clean_number(numbers[3])

                    # For local township/borough offices and school district offices,
                    # combine office and district into office column
                    # These are contests like "Tax Collector (4 Year Term) Watson Township"
                    # or "School Director (4 Year Term) Warren County Region 1 School District"
                    if current_district and any(keyword in current_contest for keyword in
                                                ['Tax Collector', 'Auditor', 'Supervisor',
                                                 'Judge of Election', 'Inspector of Election',
                                                 'Constable', 'School Director']):
                        office_name = f"{current_contest} {current_district}"
                        district_value = ''
                    else:
                        office_name = current_contest
                        district_value = current_district if current_district else ''

                    # Create result record
                    result = {
                        'county': 'Warren',
                        'office': office_name,
                        'district': district_value,
                        'party': party,
                        'candidate': candidate,
                        'election_day': election_day,
                        'mail': mail_in,
                        'provisional': provisional,
                        'votes': total
                    }
                    results.append(result)

        i += 1

    return results


def write_csv(results, output_path):
    """Write results to CSV in OpenElections format"""
    fieldnames = ['county', 'office', 'district', 'party', 'candidate',
                  'election_day', 'mail', 'provisional', 'votes']

    with open(output_path, 'w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(results)


def main():
    if len(sys.argv) < 2:
        print("Usage: python pa_warren_general_2025_results_parser.py <pdf_path> [output_csv]")
        sys.exit(1)

    pdf_path = sys.argv[1]
    output_path = sys.argv[2] if len(sys.argv) > 2 else '20251104__pa__general__warren__precinct.csv'

    print(f"Extracting text from {pdf_path}...")
    text = extract_text_from_pdf(pdf_path)

    print("Parsing election results...")
    results = parse_election_results(text)

    print(f"Found {len(results)} result records")
    print(f"Writing to {output_path}...")
    write_csv(results, output_path)

    print("Done!")


if __name__ == '__main__':
    main()
