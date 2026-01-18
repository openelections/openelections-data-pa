#!/usr/bin/env python3
"""
Parser for Bradford County, PA 2025 General Election PDF results
Converts PDF election summary to OpenElections CSV format

This PDF has a two-column layout, so we need to extract each column separately.
"""

import pdfplumber
import csv
import re
import sys


def parse_candidate_line(line):
    """
    Parse a candidate line to extract name, party, votes
    Example: "BRANDON NEUMAN (DEM) 4,430 32.06%"
    """
    line = line.strip()

    # Match pattern: NAME (PARTY) VOTES PERCENTAGE or NAME VOTES PERCENTAGE
    match = re.match(r'^(.+?)\s+(\d{1,3}(?:,\d{3})*)\s+([\d.]+)%\s*$', line)
    if not match:
        return None

    name_part = match.group(1).strip()
    votes = match.group(2).replace(',', '')

    # Extract party from parentheses
    party_match = re.search(r'\(([A-Z]{3}|WI|DEM|REP|LBR)\)\s*$', name_part)
    if party_match:
        party = party_match.group(1)
        candidate = name_part[:party_match.start()].strip()
    else:
        party = ''
        candidate = name_part

    return {
        'candidate': candidate,
        'party': party,
        'votes': votes
    }


def extract_municipality(office_name):
    """Extract municipality name from office title"""
    match = re.search(r'([A-Z\s]+(?:BOROUGH|TOWNSHIP|AREA|DISTRICT))', office_name.upper())
    if match:
        return match.group(1).strip().title()
    return None


def parse_column_text(text):
    """Parse a single column of text and extract all races"""
    lines = text.split('\n')
    results = []

    current_office = None
    office_buffer = []  # To handle multi-line office names
    in_candidates = False

    for i, line in enumerate(lines):
        line = line.strip()
        if not line:
            continue

        # Skip header/footer
        if any(skip in line for skip in [
            'Election Summary Report', 'BRADFORD COUNTY', 'PENNSYLVANIA',
            'MUNICIPAL ELECTION', 'NOVEMBER 4, 2025', 'FINAL RESULTS',
            'Registered Voters', 'Page ', 'Date:', 'Time:', 'Bradford Election Day'
        ]):
            office_buffer = []
            continue

        # Check for office header by looking for "Number of Precincts" ahead
        # Office names are all caps, multiple words, and appear 1-3 lines before "Number of Precincts"
        if 'Number of Precincts' in line:
            # Look backward for office name
            potential_office = []
            for j in range(i-1, max(-1, i-4), -1):
                if j < 0:
                    break
                prev = lines[j].strip()
                if not prev:
                    continue
                # Skip single-letter fragments
                if len(prev) <= 2:
                    continue
                # If all caps and has letters, likely part of office name
                if prev.isupper() and re.search(r'[A-Z]{2,}', prev):
                    potential_office.insert(0, prev)
                else:
                    break

            if potential_office:
                current_office = ' '.join(potential_office)
                in_candidates = False
            office_buffer = []
            continue

        # Skip metadata
        if 'Precincts Reporting' in line:
            continue

        if 'Vote For' in line:
            continue

        # In this PDF, candidate section starts AFTER "Total Votes" line
        if 'Total Votes' in line and current_office:
            in_candidates = True
            continue

        # Parse candidate lines (after Total Votes, before next office)
        if current_office and in_candidates:
            candidate_data = parse_candidate_line(line)
            if candidate_data:
                # Extract municipality for precinct
                municipality = extract_municipality(current_office)
                precinct = municipality if municipality else 'Bradford'

                # Clean office name
                office_clean = current_office
                office_clean = re.sub(r'\s+\d+\s+Year\s+Term.*$', '', office_clean, flags=re.IGNORECASE)
                office_clean = office_clean.strip()

                result = {
                    'county': 'Bradford',
                    'precinct': precinct,
                    'office': office_clean,
                    'district': '',
                    'party': candidate_data['party'],
                    'candidate': candidate_data['candidate'],
                    'votes': candidate_data['votes']
                }
                results.append(result)

    return results


def parse_bradford_pdf(pdf_path):
    """Parse Bradford County PDF with two-column layout"""
    all_results = []

    with pdfplumber.open(pdf_path) as pdf:
        for page_num, page in enumerate(pdf.pages, 1):
            # Get page dimensions
            width = page.width
            height = page.height

            # Define bounding boxes for left and right columns
            # Assuming equal columns with a small gap in the middle
            mid_x = width / 2
            margin = 20  # Small overlap to catch text on the border

            # Left column
            left_bbox = (0, 0, mid_x + margin, height)
            # Right column
            right_bbox = (mid_x - margin, 0, width, height)

            # Extract text from each column
            left_text = page.within_bbox(left_bbox).extract_text()
            right_text = page.within_bbox(right_bbox).extract_text()

            # Parse each column
            if left_text:
                left_results = parse_column_text(left_text)
                all_results.extend(left_results)
                print(f"Page {page_num} Left: {len(left_results)} results")

            if right_text:
                right_results = parse_column_text(right_text)
                all_results.extend(right_results)
                print(f"Page {page_num} Right: {len(right_results)} results")

    return all_results


def write_csv(results, output_path):
    """Write results to CSV in OpenElections format"""
    fieldnames = ['county', 'precinct', 'office', 'district', 'party', 'candidate', 'votes']

    with open(output_path, 'w', newline='') as csvfile:
        writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(results)

    print(f"\nWrote {len(results)} results to {output_path}")


def main():
    if len(sys.argv) < 2:
        print("Usage: python pa_bradford_general_2025_results_parser.py <pdf_path> [output_csv]")
        sys.exit(1)

    pdf_path = sys.argv[1]
    output_path = sys.argv[2] if len(sys.argv) > 2 else 'bradford_2025_general.csv'

    print(f"Parsing {pdf_path}...")
    results = parse_bradford_pdf(pdf_path)

    print(f"\nTotal candidate results: {len(results)}")

    write_csv(results, output_path)
    print("Done!")


if __name__ == '__main__':
    main()
