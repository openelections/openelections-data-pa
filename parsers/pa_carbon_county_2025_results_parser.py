#!/usr/bin/env python3
"""
Parser for Carbon County, PA 2025 General Election Results (County-Level)
Extracts county-level results from PDF text with vote type breakdowns.

Usage:
    python pa_carbon_county_2025_results_parser.py <input_file> <output_file>
"""

import csv
import re
import sys


def parse_carbon_2025(input_file, output_file):
    """
    Parse Carbon County 2025 election results from text file.
    Extracts county-wide totals for statewide/county offices only.

    Args:
        input_file: Path to input text file extracted from PDF
        output_file: Path to output CSV file
    """
    results = []
    processed_offices = set()  # Track which offices we've already parsed

    with open(input_file, 'r', encoding='utf-8') as f:
        lines = f.readlines()

    i = 0
    while i < len(lines):
        line = lines[i].strip()

        # Look for office headers (e.g., "Judge of the Superior Court (Vote for 1)")
        # Stop when we hit precinct-specific races (containing "Township" or "Borough" or "Boro" in office name)
        office_match = re.match(r'^(.+?)\s+\(Vote for \d+\)$', line)

        if office_match:
            office_name = office_match.group(1).strip()

            # Use the office name as-is (we want all offices, including local ones)
            office = office_name

            # Skip if we've already processed this office
            if office in processed_offices:
                i += 1
                continue

            processed_offices.add(office)
            i += 1

            # Skip "Precincts Reported" line
            while i < len(lines) and 'Precincts Reported:' in lines[i]:
                i += 1

            # Look for Times Cast line and parse as "Ballots Cast"
            while i < len(lines):
                line = lines[i].strip()

                if 'Times Cast' in line:
                    # Parse Times Cast as "Ballots Cast"
                    # Format: Times Cast    election_day    mail    provisional    total / registered    pct%
                    parts = line.split()
                    if len(parts) >= 6:
                        try:
                            election_day = parts[2].replace(',', '')
                            mail = parts[3].replace(',', '')
                            provisional = parts[4].replace(',', '')
                            total = parts[5].replace(',', '').split('/')[0]

                            results.append({
                                'county': 'Carbon',
                                'office': office,
                                'district': '',
                                'party': '',
                                'candidate': 'Ballots Cast',
                                'election_day': election_day,
                                'mail': mail,
                                'provisional': provisional,
                                'votes': total
                            })
                        except (ValueError, IndexError):
                            pass
                    break
                i += 1

            # Now parse candidates - look for "Candidate" header line
            while i < len(lines):
                line = lines[i].strip()

                # Skip until we find the Candidate header
                if line.startswith('Candidate') and 'Party' in line:
                    i += 1
                    break

                # Stop if we hit another office
                if 'Vote for' in line:
                    break

                i += 1

            # Parse candidate lines
            while i < len(lines):
                line = lines[i].strip()

                # Stop if we hit another office, page break, or empty section
                if not line or 'Vote for' in line or line.startswith('Page:'):
                    break

                # Skip write-in detail lines (individual write-in candidates marked with all-caps WRITE-IN)
                # But keep the "Write-in" summary line (which has lowercase 'in')
                if 'WRITE-IN' in line:
                    i += 1
                    continue

                # Skip "Total Votes" line
                if line.startswith('Total Votes'):
                    i += 1
                    continue

                # Skip header lines
                if 'Election Day' in line and 'Mail-In' in line:
                    i += 1
                    continue

                # Parse regular candidate lines
                # Format: CANDIDATE NAME    PARTY    election_day    mail    provisional    total
                parts = line.split()

                if len(parts) >= 5:
                    # Find where the numbers start (votes)
                    vote_start_idx = -1
                    for idx, part in enumerate(parts):
                        cleaned = part.replace(',', '')
                        if cleaned.isdigit():
                            vote_start_idx = idx
                            break

                    if vote_start_idx >= 1:
                        # Everything from vote_start_idx onwards are numbers
                        vote_parts = parts[vote_start_idx:]

                        # Check if the part before votes is a party code
                        potential_party = parts[vote_start_idx - 1]
                        if potential_party.upper() in ['DEM', 'REP', 'LIB', 'GRN', 'DEM/REP', 'REP/DEM']:
                            party = potential_party.upper()
                            candidate_parts = parts[:vote_start_idx - 1]
                        else:
                            party = ''
                            candidate_parts = parts[:vote_start_idx]

                        candidate = ' '.join(candidate_parts)

                        # Handle "Write-in" candidates (summary line)
                        if candidate == 'Write-in':
                            candidate = 'Write-In'

                        # Parse vote counts
                        if len(vote_parts) >= 4:
                            try:
                                election_day = vote_parts[0].replace(',', '')
                                mail = vote_parts[1].replace(',', '')
                                provisional = vote_parts[2].replace(',', '')
                                total = vote_parts[3].replace(',', '')

                                results.append({
                                    'county': 'Carbon',
                                    'office': office,
                                    'district': '',
                                    'party': party,
                                    'candidate': candidate,
                                    'election_day': election_day,
                                    'mail': mail,
                                    'provisional': provisional,
                                    'votes': total
                                })
                            except (ValueError, IndexError):
                                pass

                i += 1

            continue

        i += 1

    # Write results to CSV
    with open(output_file, 'w', newline='') as f:
        fieldnames = ['county', 'office', 'district', 'party', 'candidate',
                      'election_day', 'mail', 'provisional', 'votes']
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(results)

    print(f"Parsed {len(results)} rows")
    print(f"Output written to {output_file}")


def normalize_office(office_name):
    """Normalize office names to standard OpenElections format."""
    office_lower = office_name.lower()

    if 'superior court' in office_lower and 'retention' not in office_lower:
        return 'Superior Court'
    elif 'commonwealth court' in office_lower:
        return 'Commonwealth Court'
    elif 'supreme court' in office_lower and 'retention' in office_lower:
        return 'Supreme Court Retention'
    elif 'superior court' in office_lower and 'retention' in office_lower:
        return 'Superior Court Retention'
    elif 'treasurer' in office_lower and 'year' in office_lower:
        return 'County Treasurer'
    elif 'register of wills' in office_lower or 'clerk of orphans' in office_lower:
        return 'Register of Wills'
    elif 'clerk of courts' in office_lower:
        return 'Clerk of Courts'
    elif 'district attorney' in office_lower:
        return 'District Attorney'
    elif 'prothonotary' in office_lower:
        return 'Prothonotary'
    elif 'recorder of deeds' in office_lower:
        return 'Recorder of Deeds'
    elif 'sheriff' in office_lower:
        return 'Sheriff'
    elif 'coroner' in office_lower:
        return 'Coroner'
    elif 'county commissioner' in office_lower:
        return 'County Commissioner'
    elif 'county controller' in office_lower:
        return 'County Controller'
    elif 'school directors' in office_lower:
        # Extract school district name
        district_match = re.search(r'school directors.+?([\w\s]+area school district)', office_lower)
        if district_match:
            district = district_match.group(1).title()
            return f'School Director - {district}'
        return 'School Director'

    return None  # Skip offices we don't recognize as county-wide


if __name__ == '__main__':
    if len(sys.argv) != 3:
        print("Usage: python pa_carbon_county_2025_results_parser.py <input_file> <output_file>")
        sys.exit(1)

    input_file = sys.argv[1]
    output_file = sys.argv[2]

    parse_carbon_2025(input_file, output_file)
