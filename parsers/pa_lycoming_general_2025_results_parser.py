#!/usr/bin/env python3
"""
Lycoming County, PA 2025 General Election Results Parser

This parser processes the Lycoming County Official Results PDF for the
November 4, 2025 municipal election. It uses pdftotext to extract text and
then parses it into OpenElections standardized format.

The PDF format is a county-level summary showing:
- Contest name (office) with registered voters info on same line
- Candidate names with vote totals
- Vote breakdowns: ED (Election Day), MI (Mail-In), PR (Provisional)

Output format is county-level (not precinct-level).

Usage:
    python pa_lycoming_general_2025_results_parser.py <input_pdf> <output_csv>
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
        "Judge of the Superior Court" -> "Judge of the Superior Court"
        "East Lycoming SD 1" -> "East Lycoming SD 1"
        "Anthony Supervisor" -> "Supervisor - Anthony"
        "Armstrong Auditor (4yr)" -> "Auditor 4 Year Term - Armstrong"
    """
    office = office_text.strip()

    # Preserve term length indicators like (4yr), (2yr) but format them
    term_match = re.search(r'\s*\(([0-9]+)yr\)', office)
    term_length = None
    if term_match:
        term_length = term_match.group(1)
        office = re.sub(r'\s*\([0-9]+yr\)', '', office)

    # Detect local offices by checking for township/borough name at the beginning
    # Pattern: "TownshipName OfficeTitle"
    local_office_match = re.match(r'^([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)\s+(Supervisor|Auditor|Tax Collector|Judge of Elections|Inspector of Elections|Constable|Council Member|Mayor)(.*)$', office)

    if local_office_match:
        municipality = local_office_match.group(1)
        office_title = local_office_match.group(2)
        remainder = local_office_match.group(3).strip()

        # Format as "Office Title [Term] - Municipality"
        if term_length:
            office_title = f"{office_title} {term_length} Year Term"

        if remainder:
            return f"{office_title} {remainder} - {municipality}"
        else:
            return f"{office_title} - {municipality}"

    # Title case for other offices
    words = office.split()
    normalized = []
    for word in words:
        # Keep short words lowercase in the middle
        if word.lower() in ['of', 'the', 'for', 'and']:
            normalized.append(word.lower())
        else:
            # Capitalize first letter but preserve existing case for rest (like "SD")
            if word.isupper() and len(word) <= 3:
                normalized.append(word)
            else:
                normalized.append(word.capitalize())

    result = ' '.join(normalized)

    # Add term length for non-local offices if present
    if term_length and not local_office_match:
        result = f"{result} {term_length} Year Term"

    return result


def extract_district(office_text):
    """Extract district number from office name if present."""
    # Match patterns like "SD 1", "SD 2", or "29-1-02"
    match = re.search(r'(\d+-\d+-\d+|SD\s+(\d+)|Region\s+(\d+))', office_text, re.IGNORECASE)
    if match:
        if match.group(2):  # SD N
            return match.group(2)
        elif match.group(3):  # Region N
            return match.group(3)
        else:  # Full magisterial district
            return match.group(1)
    return ''


def parse_results(text):
    """
    Parse Lycoming County election results text into structured records.

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
        if not line_stripped or 'Official Results' in line or 'Lycoming County' in line:
            i += 1
            continue

        if 'Page:' in line or 'Total Ballots Cast:' in line or 'precincts reported' in line:
            i += 1
            continue

        if line_stripped.startswith('Choice') and 'Votes' in line:
            i += 1
            continue

        if line_stripped == 'All Precincts':
            i += 1
            continue

        # Check for contest header (office name with vote info on same line)
        # Pattern: "    Office Name (Vote for N), registered voters, turnout"
        # Example: "    Judge of the Superior Court (Vote for 1), 72295 registered voters, turnout 41.07%"
        office_match = re.match(r'^(.+?)\s*\(Vote for \d+\),\s*(\d+)\s+registered voters', line_stripped)

        if office_match:
            office_text = office_match.group(1).strip()
            registered_voters = office_match.group(2)

            current_office = normalize_office(office_text)
            current_district = extract_district(office_text)
            in_contest = True

            # Add metadata rows only once at the beginning
            if not metadata_added:
                # Get total ballots cast from the overall header
                # We'll extract it when we first encounter it
                for j in range(max(0, i-10), i):
                    ballots_line = lines[j].strip()
                    if 'Total Ballots Cast:' in ballots_line:
                        ballot_match = re.search(r'Total Ballots Cast:\s*(\d+).*?Registered Voters:\s*(\d+)', ballots_line)
                        if ballot_match:
                            ballots_cast = ballot_match.group(1)
                            total_registered = ballot_match.group(2)

                            results.append({
                                'county': 'Lycoming',
                                'office': 'Registered Voters',
                                'district': '',
                                'party': '',
                                'candidate': '',
                                'votes': total_registered,
                                'election_day': '',
                                'mail': '',
                                'provisional': ''
                            })
                            results.append({
                                'county': 'Lycoming',
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
                            break

            i += 1
            continue

        # Parse candidate lines
        # Format: "    Candidate Name                      votes    pct%      ed_votes  mi_votes  pr_votes"
        # Example: "        Brandon Neuman                       9691       33.16%       6703     2958            30"
        if in_contest and current_office:
            # Match candidate lines with vote data (with flexible whitespace)
            candidate_match = re.match(
                r'^\s*([A-Z][A-Za-z\s\.\,\'\-]+?)\s+(\d+)\s+(\d+\.\d+%)\s+(\d+)\s+(\d+)\s+(\d+)\s*$',
                line
            )

            # Also match Write-in lines
            writein_match = re.match(
                r'^\s*(Write-in)\s+(\d+)\s+(\d+\.\d+%)\s+(\d+)\s+(\d+)\s+(\d+)\s*$',
                line
            )

            # Match Total lines to know when contest ends
            total_match = re.match(
                r'^\s*Total\s+(\d+)\s+100\.00%',
                line_stripped
            )

            match = candidate_match or writein_match

            if match:
                candidate = match.group(1).strip()
                votes = match.group(2)
                # Skip percentage
                ed_votes = match.group(4)
                mail_votes = match.group(5)
                prov_votes = match.group(6)

                # Skip "Total" candidates - we don't need them
                if candidate.lower() != 'total':
                    results.append({
                        'county': 'Lycoming',
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
        print("Usage: python pa_lycoming_general_2025_results_parser.py <input_pdf> <output_csv>")
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
