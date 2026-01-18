#!/usr/bin/env python3
"""
Bucks County, PA 2025 General Election Results Parser

This parser processes the Bucks County EMS Report PDF for the
November 4, 2025 municipal election. It uses pdftotext to extract text and
then parses it into OpenElections standardized format.

The PDF format is a county-level summary showing:
- Contest name (office) with term length and vote count
- Candidate names with vote totals
- Vote breakdowns: ED (Election Day), MI (Mail-In), PR (Provisional)

Output format is county-level (not precinct-level).

Usage:
    python pa_bucks_general_2025_results_parser.py <input_pdf> <output_csv>
"""

import sys
import csv
import subprocess
import tempfile
import re
from pathlib import Path


def extract_text_from_pdf(pdf_path):
    """Extract text from PDF using pdftotext with -layout option."""
    with tempfile.NamedTemporaryFile(mode='w+', suffix='.txt', delete=False) as tmp:
        tmp_path = tmp.name

    try:
        subprocess.run(
            ['pdftotext', '-layout', pdf_path, tmp_path],
            check=True,
            capture_output=True
        )
        with open(tmp_path, 'r', encoding='utf-8') as f:
            text = f.read()
        return text
    finally:
        Path(tmp_path).unlink(missing_ok=True)


def normalize_office(office_text):
    """
    Normalize office names to OpenElections standard format.

    Examples:
        "JUDGE OF THE SUPERIOR COURT" -> "Judge of the Superior Court"
        "BEDMINSTER TOWNSHIP SUPERVISOR - 6 YEAR" -> "Supervisor 6 Year Term - Bedminster Township"
        "BRISTOL BOROUGH TAX COLLECTOR - 4 YEAR" -> "Tax Collector 4 Year Term - Bristol Borough"
        "BENSALEM TOWNSHIP SCHOOL DIRECTOR - 4 YEAR" -> "Bensalem Township School Director 4 Year Term"
    """
    office = office_text.strip()

    # Extract term length if present (e.g., "- 6 YEAR", "- 4 YEAR", "- 2 YEAR")
    term_match = re.search(r'\s*-\s*([0-9]+)\s*YEAR', office)
    term_length = None
    if term_match:
        term_length = term_match.group(1)
        office = re.sub(r'\s*-\s*[0-9]+\s*YEAR', '', office)

    # Detect local offices by checking for township/borough name at the beginning
    # Pattern: "MUNICIPALITY_NAME OFFICE_TITLE"
    # Municipality names can be multi-word (e.g., "EAST ROCKHILL TOWNSHIP")
    local_office_match = re.match(
        r'^([A-Z][A-Z\s]+?(?:TOWNSHIP|BOROUGH))\s+(SUPERVISOR|AUDITOR|TAX COLLECTOR|CONSTABLE|COUNCIL|COUNCILMAN|JUDGE OF ELECTION|INSPECTOR OF ELECTION|COMMISSIONER)(.*)$',
        office
    )

    if local_office_match:
        municipality = local_office_match.group(1).strip()
        office_title = local_office_match.group(2).strip()
        remainder = local_office_match.group(3).strip()

        # Title case municipality name
        municipality = municipality.title()

        # Title case office
        office_title = office_title.title()

        # Format as "Office Title [Term] - Municipality"
        if term_length:
            office_title = f"{office_title} {term_length} Year Term"

        if remainder:
            # For things like "CONSTABLE 6TH WARD"
            return f"{office_title} {remainder.title()} - {municipality}"
        else:
            return f"{office_title} - {municipality}"

    # Title case for other offices (judicial, school board, etc.)
    words = office.split()
    normalized = []
    for word in words:
        # Keep short words lowercase in the middle
        if word.lower() in ['of', 'the', 'for', 'and']:
            normalized.append(word.lower())
        else:
            normalized.append(word.capitalize())

    result = ' '.join(normalized)

    # Add term length for non-local offices if present
    if term_length:
        result = f"{result} {term_length} Year Term"

    return result


def extract_district(office_text):
    """Extract district number from office name if present."""
    # Match patterns like "07-1-07", "REGION 1", or "WARD"
    match = re.search(r'(\d+-\d+-\d+|REGION\s+(\d+)|(\d+)(?:ST|ND|RD|TH)\s+WARD)', office_text, re.IGNORECASE)
    if match:
        if match.group(2):  # REGION N
            return match.group(2)
        elif match.group(3):  # Nth WARD
            return match.group(3)
        else:  # Full magisterial district or just the full match
            return match.group(1)
    return ''


def parse_results(text):
    """
    Parse Bucks County election results text into structured records.

    Returns list of dicts with keys: county, office, district,
    party, candidate, votes, election_day, mail, provisional
    """
    results = []
    lines = text.split('\n')

    current_office = None
    current_district = ''
    in_contest = False
    metadata_added = False  # Track if we've added the initial metadata rows

    i = 0
    while i < len(lines):
        line = lines[i]
        line_stripped = line.strip()

        # Skip header lines and page breaks
        if not line_stripped or '11.24.25 - Second Signing' in line or 'Jurisdiction Wide' in line:
            i += 1
            continue

        if 'Page:' in line or 'All Districts, All Counter Groups' in line:
            i += 1
            continue

        if line_stripped.startswith('Choice') and 'Votes' in line:
            i += 1
            continue

        if line_stripped == 'All Precincts':
            i += 1
            continue

        # Extract total ballots cast for metadata (only once)
        if not metadata_added and 'Total Ballots Cast:' in line:
            ballot_match = re.search(r'Total Ballots Cast:\s*(\d+)', line)
            if ballot_match:
                ballots_cast = ballot_match.group(1)

                # Add metadata rows
                results.append({
                    'county': 'Bucks',
                    'office': 'Ballots Cast',
                    'district': '',
                    'party': '',
                    'candidate': '',
                    'votes': ballots_cast,
                    'election_day': '',
                    'mail': '',
                    'provisional': ''
                })
                metadata_added = True
            i += 1
            continue

        # Check for contest header (office name)
        # Pattern: "    OFFICE NAME (Vote for N)"
        # Example: "    JUDGE OF THE SUPERIOR COURT (Vote for 1)"
        office_match = re.match(r'^([A-Z][A-Z\s\-0-9\,\.]+?)\s*\(Vote for \d+\)\s*$', line_stripped)

        if office_match:
            office_text = office_match.group(1).strip()

            # End previous contest if we're starting a new one
            if in_contest:
                in_contest = False

            current_office = normalize_office(office_text)
            current_district = extract_district(office_text)
            in_contest = True

            i += 1
            continue

        # Parse candidate lines
        # Format: "        Candidate Name                  votes    ed_votes  mi_votes  pr_votes"
        # Example: "        Brandon Neuman                  131929     75877      55500       552"
        if in_contest and current_office:
            # Match candidate lines with vote data (with flexible whitespace)
            candidate_match = re.match(
                r'^\s+([A-Z][A-Za-z\s\.\,\'\-]+?)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s*$',
                line
            )

            # Also match Write-in lines
            writein_match = re.match(
                r'^\s+(Write-in)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s*$',
                line
            )

            # Match Total lines to know when contest ends (but don't save them)
            total_match = re.match(
                r'^\s*Total\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s*$',
                line_stripped
            )

            match = candidate_match or writein_match

            if match:
                candidate = match.group(1).strip()
                votes = match.group(2)
                ed_votes = match.group(3)
                mail_votes = match.group(4)
                prov_votes = match.group(5)

                # Skip "Total" candidates
                if candidate.lower() != 'total':
                    results.append({
                        'county': 'Bucks',
                        'office': current_office,
                        'district': current_district,
                        'party': '',
                        'candidate': candidate,
                        'votes': votes,
                        'election_day': ed_votes,
                        'mail': mail_votes,
                        'provisional': prov_votes
                    })
            elif total_match:
                # End of contest
                in_contest = False

        i += 1

    return results


def write_csv(results, output_path):
    """Write results to CSV in OpenElections format."""
    fieldnames = [
        'county', 'office', 'district', 'party',
        'candidate', 'votes', 'election_day', 'mail', 'provisional'
    ]

    with open(output_path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(results)


def main():
    if len(sys.argv) != 3:
        print("Usage: python pa_bucks_general_2025_results_parser.py <input_pdf> <output_csv>")
        sys.exit(1)

    input_pdf = sys.argv[1]
    output_csv = sys.argv[2]

    print(f"Extracting text from {input_pdf}...")
    text = extract_text_from_pdf(input_pdf)

    print("Parsing results...")
    results = parse_results(text)

    print(f"Writing {len(results)} records to {output_csv}...")
    write_csv(results, output_csv)

    print("Done!")


if __name__ == '__main__':
    main()
