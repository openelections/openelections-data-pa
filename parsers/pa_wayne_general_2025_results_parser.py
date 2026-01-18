#!/usr/bin/env python3
"""
Wayne County, PA 2025 General Election Results Parser

This parser processes the Wayne County Statement of Votes Cast PDF for the
November 4, 2025 municipal election. It uses pdftotext to extract text and
then parses it into OpenElections standardized format.

The PDF format is a county-level summary showing:
- Contest name (office)
- Candidate names with vote totals
- Vote breakdowns: ED (Election Day), MI (Mail-In), PR (Provisional)
- Metadata: Ballots Cast, Registered Voters, Turnout

Output format is county-level (not precinct-level).

Usage:
    python pa_wayne_general_2025_results_parser.py <input_pdf> <output_csv>
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
        "MAGISTERIAL DISTRICT JUDGE 22-3-02" -> "Magisterial District Judge 22-3-02"
        "SUPERVISOR - BERLIN TOWNSHIP" -> "Supervisor - Berlin Township"
        "JUSTICE OF THE SUPREME COURT - DONOHUE RETENTION" -> "Justice of the Supreme Court - Donohue Retention"
    """
    office = office_text.strip()

    # Title case while preserving acronyms
    words = office.split()
    normalized = []
    for word in words:
        if word.isupper() and len(word) > 1:
            # Keep short words like "OF", "THE" lowercase
            if word in ['OF', 'THE', 'FOR', 'AND']:
                normalized.append(word.lower())
            else:
                normalized.append(word.title())
        else:
            normalized.append(word)

    return ' '.join(normalized)


def extract_district(office_text):
    """Extract district number from office name if present."""
    # Match patterns like "22-3-02" or "REGION #1" or "#1"
    match = re.search(r'(\d+-\d+-\d+|REGION #(\d+)|#(\d+))', office_text, re.IGNORECASE)
    if match:
        if match.group(2):  # REGION #N
            return match.group(2)
        elif match.group(3):  # #N
            return match.group(3)
        else:  # Full magisterial district
            return match.group(1)
    return ''


def parse_results(text):
    """
    Parse Wayne County election results text into structured records.

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
        if not line_stripped or 'Statement of Votes Cast' in line or 'WAYNE COUNTY' in line:
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

        # Check for contest header (office name)
        # Contest headers are indented and in ALL CAPS, often followed by "(Vote for N)"
        # Example: "    JUDGE OF THE SUPERIOR COURT (Vote for 1)"
        if line_stripped and re.match(r'^[A-Z][A-Z\s\-#0-9]+(?:\s*\(Vote for \d+\))?$', line_stripped):
            # Additional check: must contain common office keywords or patterns
            if any(keyword in line_stripped for keyword in [
                'JUDGE', 'COURT', 'DIRECTOR', 'AUDITOR', 'SUPERVISOR',
                'COUNCIL', 'COMMISSIONER', 'JUSTICE', 'RETENTION', 'INSPECTOR'
            ]) or re.search(r'\(Vote for \d+\)', line_stripped):

                # Extract office name (before the "(Vote for" part)
                office_match = re.match(r'^(.+?)(?:\s*\(Vote for \d+\))?$', line_stripped)
                if office_match:
                    office_text = office_match.group(1).strip()
                    current_office = normalize_office(office_text)
                    current_district = extract_district(office_text)
                    in_contest = True

                    # Look ahead for the ballot count line and add metadata only once
                    if not metadata_added and i + 1 < len(lines):
                        next_line = lines[i + 1].strip()
                        # Extract registered voters and ballots cast
                        ballot_match = re.search(r'(\d+)\s+ballots.*?(\d+)\s+registered voters', next_line)
                        if ballot_match:
                            ballots_cast = ballot_match.group(1)
                            registered_voters = ballot_match.group(2)

                            # Add metadata rows (only once at the beginning)
                            results.append({
                                'county': 'Wayne',
                                'office': 'Registered Voters',
                                'district': '',
                                'party': '',
                                'candidate': '',
                                'votes': registered_voters,
                                'election_day': '',
                                'mail': '',
                                'provisional': ''
                            })
                            results.append({
                                'county': 'Wayne',
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

        # Skip contest metadata lines (ballot info, overvotes, undervotes)
        if re.match(r'^\d+\s+ballots\s+\(', line_stripped):
            i += 1
            continue

        if line_stripped.startswith('Overvotes') or line_stripped.startswith('Undervotes'):
            i += 1
            continue

        if line_stripped.startswith('Total') and '%' in line:
            i += 1
            continue

        # Parse candidate lines
        # Format: "        CANDIDATE NAME                           votes    pct%    ed_votes  mi_votes  pr_votes"
        # Example: "        BRANDON NEUMAN                           5229       38.12%      3178      2034         17"
        if in_contest and current_office:
            # Match candidate lines with vote data (with flexible whitespace)
            candidate_match = re.match(
                r'^\s+([A-Z][A-Za-z\s\.\'-]+?)\s+(\d+)\s+(\d+\.\d+%)\s+(\d+)\s+(\d+)\s+(\d+)\s*$',
                line
            )

            # Also match Write-in lines
            writein_match = re.match(
                r'^\s+(Write-in)\s+(\d+)\s+(\d+\.\d+%)\s+(\d+)\s+(\d+)\s+(\d+)\s*$',
                line
            )

            match = candidate_match or writein_match

            if match:
                candidate = match.group(1).strip()
                votes = match.group(2)
                # Skip percentage
                ed_votes = match.group(4)
                mail_votes = match.group(5)
                prov_votes = match.group(6)

                results.append({
                    'county': 'Wayne',
                    'office': current_office,
                    'district': current_district,
                    'party': '',
                    'candidate': candidate,
                    'votes': votes,
                    'election_day': ed_votes,
                    'mail': mail_votes,
                    'provisional': prov_votes
                })

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
        print("Usage: python pa_wayne_general_2025_results_parser.py <input_pdf> <output_csv>")
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
