#!/usr/bin/env python3
"""
Parser for Fulton County, PA 2025 General Election Results
Converts PDF text to OpenElections CSV format
"""

import csv
import re
import subprocess
import sys
from pathlib import Path


def extract_text_from_pdf(pdf_path):
    """Extract text from PDF using pdftotext with layout preservation"""
    try:
        result = subprocess.run(
            ['pdftotext', '-layout', pdf_path, '-'],
            capture_output=True,
            text=True,
            check=True
        )
        return result.stdout
    except subprocess.CalledProcessError as e:
        print(f"Error extracting text from PDF: {e}", file=sys.stderr)
        sys.exit(1)
    except FileNotFoundError:
        print("pdftotext not found. Please install poppler-utils", file=sys.stderr)
        sys.exit(1)


def parse_candidate_line(line):
    """
    Parse a candidate result line
    Returns: (candidate_name, party, mail, election_day, provisional, total) or None
    """
    # Skip empty lines and lines that are just whitespace
    if not line.strip():
        return None

    # Skip Cast Votes, Undervotes, Overvotes lines
    if any(x in line for x in ['Cast Votes:', 'Undervotes:', 'Overvotes:']):
        return None

    # Pattern for candidate lines with vote counts
    # Format: NAME   PARTY   mail_votes mail_pct election_votes election_pct prov_votes prov_pct total_votes total_pct
    pattern = r'^(.+?)\s{2,}([A-Z,]+)?\s+(\d{1,3}(?:,\d{3})*)\s+[\d.]+%\s+(\d{1,3}(?:,\d{3})*)\s+[\d.]+%\s+(\d+)\s+[\d.]+%\s+(\d{1,3}(?:,\d{3})*)\s+[\d.]+%'

    match = re.search(pattern, line)
    if match:
        candidate = match.group(1).strip()
        party = match.group(2).strip() if match.group(2) else ''
        mail = match.group(3).replace(',', '')
        election_day = match.group(4).replace(',', '')
        provisional = match.group(5).replace(',', '')
        total = match.group(6).replace(',', '')
        return (candidate, party, mail, election_day, provisional, total)

    return None


def normalize_office(office):
    """Normalize office names to OpenElections standards"""
    office = office.strip()

    # Remove "- (VOTE FOR...)" suffix
    office = re.sub(r'\s*-\s*\(VOTE FOR.*?\)', '', office)

    # Map to standardized names
    if 'JUDGE OF THE SUPERIOR COURT' in office:
        return 'Judge of the Superior Court'
    elif 'JUDGE OF THE COMMONWEALTH COURT' in office:
        return 'Judge of the Commonwealth Court'
    elif 'PROTHONOTARY' in office:
        return 'Prothonotary'
    elif 'SCHOOL DIRECTOR' in office:
        return office.title()
    elif 'SUPERVISOR' in office:
        return office.title()
    elif 'AUDITOR' in office:
        return office.title()
    elif 'TAX COLLECTOR' in office:
        return office.title()
    elif 'CONSTABLE' in office:
        return office.title()
    elif 'JUDGE OF ELECTIONS' in office:
        return office.title()
    elif 'INSPECTOR OF ELECTIONS' in office:
        return office.title()
    elif 'MAYOR' in office:
        return office.title()
    elif 'COUNCIL' in office:
        return office.title()
    elif 'RETENTION ELECTION QUESTION' in office:
        return office.title()
    else:
        return office.title()


def parse_fulton_results(text):
    """Parse Fulton County election results text"""
    lines = text.split('\n')
    results = []

    current_office = None
    i = 0

    while i < len(lines):
        line = lines[i]

        # Check for office header (all caps lines that end with TERM or QUESTION)
        if line.strip() and line.strip().isupper() and (
            'YEAR TERM' in line or
            'QUESTION:' in line or
            'RETENTION ELECTION' in line
        ):
            current_office = normalize_office(line.strip())
            i += 1
            # Skip the Choice/Party/Mail header line
            if i < len(lines) and 'Choice' in lines[i]:
                i += 1
            continue

        # Parse candidate lines
        if current_office:
            # Check if this is a multi-line candidate (party on next line)
            candidate_data = parse_candidate_line(line)

            if candidate_data:
                candidate, party, mail, election_day, provisional, total = candidate_data

                # Check if next line contains party (for cases like DEM,\n REP)
                if i + 1 < len(lines):
                    next_line = lines[i + 1].strip()
                    if next_line and next_line.isupper() and len(next_line) <= 10 and not re.search(r'\d', next_line):
                        if party and ',' in party:
                            party = party + ' ' + next_line
                        i += 1

                # Normalize party codes
                party = party.replace(',', '/').strip()

                results.append({
                    'county': 'Fulton',
                    'office': current_office,
                    'district': '',
                    'party': party,
                    'candidate': candidate,
                    'votes': total,
                    'mail': mail,
                    'election_day': election_day,
                    'provisional': provisional
                })

        i += 1

    return results


def write_csv(results, output_path):
    """Write results to OpenElections CSV format"""
    fieldnames = ['county', 'office', 'district', 'party',
                  'candidate', 'votes', 'mail', 'election_day', 'provisional']

    with open(output_path, 'w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(results)

    print(f"Wrote {len(results)} results to {output_path}")


def main():
    if len(sys.argv) != 3:
        print("Usage: python pa_fulton_general_2025_results_parser.py <input_pdf> <output_csv>")
        sys.exit(1)

    pdf_path = sys.argv[1]
    output_path = sys.argv[2]

    # Check if PDF exists
    if not Path(pdf_path).exists():
        print(f"Error: PDF file not found: {pdf_path}", file=sys.stderr)
        sys.exit(1)

    # Extract text from PDF
    print(f"Extracting text from {pdf_path}...")
    text = extract_text_from_pdf(pdf_path)

    # Parse results
    print("Parsing results...")
    results = parse_fulton_results(text)

    # Write CSV
    write_csv(results, output_path)


if __name__ == '__main__':
    main()
