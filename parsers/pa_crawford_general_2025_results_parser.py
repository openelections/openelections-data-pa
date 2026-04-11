#!/usr/bin/env python3
"""
Parser for Crawford County 2025 General Election Results

Processes two Excel files from Crawford County to produce county-level results
in OpenElections standardized format.

Files:
1. Fed, State and County.xlsx - Contains judicial retention questions and state offices
2. Local Races.xlsx - Contains local municipal offices

Usage:
    python parsers/pa_crawford_general_2025_results_parser.py

Input:
    /Users/dwillis/Downloads/Crawford PA Seeley - Fed, State and County.xlsx
    /Users/dwillis/Downloads/Crawford PA Seeley - Local Races.xlsx

Output:
    2025/counties/20251104__pa__general__crawford__county.csv
"""

import pandas as pd
import csv
import os
import re
from collections import defaultdict


def parse_state_county_excel(file_path):
    """
    Parse the Fed, State and County Excel file.

    Structure:
    - Contests are stacked vertically
    - Each contest block has:
      - Row N: Office name in column 1
      - Row N+1: First candidate with party in column 1
      - Row N+2: Vote type header (Machine, Hand, Total) in columns
      - Row N+3+: Precinct data rows
    - Multiple candidates for same office are spread across columns

    Returns list of result dicts aggregated to county level.
    """
    df = pd.read_excel(file_path, sheet_name='Table 1', header=None)

    results = []

    # Find all contest starting rows by looking for office names in column 1
    contest_rows = []
    for i in range(len(df)):
        val = df.iloc[i, 1]
        if pd.notna(val):
            val_str = str(val).strip()
            # Office names are all-caps, long, and don't have commas
            if val_str and val_str.isupper() and len(val_str) > 5 and ',' not in val_str:
                contest_rows.append(i)

    # Process each contest block
    for contest_idx, start_row in enumerate(contest_rows):
        # Determine end of this contest block (start of next contest or end of file)
        end_row = contest_rows[contest_idx + 1] if contest_idx + 1 < len(contest_rows) else len(df)

        office = df.iloc[start_row, 1].strip()

        # Extract district if present in office name
        district = None
        district_match = re.search(r'(\d+)(?:TH|ST|ND|RD)', office, re.IGNORECASE)
        if district_match:
            district = district_match.group(1)

        # Find candidates in row start_row+1 across all columns
        candidate_row_idx = start_row + 1
        vote_type_row_idx = start_row + 2

        # Track candidates and their Total column indices
        candidates_info = []  # [(col_idx, candidate, party), ...]

        for col_idx in range(1, len(df.columns)):
            candidate_val = df.iloc[candidate_row_idx, col_idx]
            if pd.notna(candidate_val):
                candidate_text = str(candidate_val).strip()

                # Parse candidate name and party
                candidate = candidate_text
                party = None

                # Check for party after comma (e.g., "BRANDON NEUMAN, DEM")
                if ', ' in candidate_text:
                    parts = candidate_text.split(', ')
                    candidate = parts[0].strip()
                    if len(parts) > 1:
                        party = parts[1].strip()

                # Special handling for YES/NO (retention questions)
                if candidate in ['YES', 'NO']:
                    party = None

                # Handle SCATTERED -> Write-In
                if candidate.upper() == 'SCATTERED':
                    candidate = 'Write-In'

                # Get vote type for this column
                vote_type = df.iloc[vote_type_row_idx, col_idx]
                if pd.notna(vote_type):
                    vote_type_str = str(vote_type).strip()
                    # Only track "Total" columns to avoid double-counting
                    if vote_type_str == 'Total':
                        candidates_info.append((col_idx, candidate, party))

        # Sum votes across all precincts for each candidate
        for col_idx, candidate, party in candidates_info:
            total_votes = 0
            # Start from data rows (skip office, candidate, vote type rows)
            for row_idx in range(start_row + 3, end_row):
                precinct_name = df.iloc[row_idx, 0]
                # Skip rows without precinct names or with page markers
                if pd.isna(precinct_name):
                    continue
                precinct_str = str(precinct_name).upper()
                if 'PAGE' in precinct_str or 'TOTALS' in precinct_str:
                    continue

                vote_value = df.iloc[row_idx, col_idx]
                if pd.notna(vote_value) and str(vote_value).strip() != '':
                    try:
                        total_votes += int(float(vote_value))
                    except (ValueError, TypeError):
                        pass

            results.append({
                'county': 'Crawford',
                'office': office,
                'district': district,
                'party': party,
                'candidate': candidate,
                'votes': total_votes
            })

    return results


def parse_local_races_excel(file_path):
    """
    Parse the Local Races Excel file.

    Structure:
    - Each municipality's races are stacked vertically
    - Office name in column 0
    - Candidate names in column 1
    - Vote data in subsequent columns (Machine, Hand, Total)

    Returns list of result dicts aggregated to county level.
    """
    df = pd.read_excel(file_path, sheet_name='Table 1', header=None)

    # Aggregate votes by office and candidate
    vote_totals = defaultdict(lambda: defaultdict(int))  # {office: {candidate: total_votes}}

    current_office = None
    office_term = None

    for row_idx in range(len(df)):
        col0 = df.iloc[row_idx, 0]
        col1 = df.iloc[row_idx, 1]

        # Skip empty rows
        if pd.isna(col0) and pd.isna(col1):
            continue

        col0_str = str(col0).strip() if pd.notna(col0) else ''
        col1_str = str(col1).strip() if pd.notna(col1) else ''

        # Check if this is an office name
        if col0_str and col0_str.upper() in [
            'SUPERVISOR', 'TAX COLLECTOR', 'AUDITOR', 'JUDGE OF ELECTION',
            'INSPECTOR OF ELECTION', 'CONSTABLE', 'SCHOOL DIRECTOR',
            'MAYOR', 'COUNCIL MEMBER', 'TAX ASSESSOR', 'BOROUGH COUNCIL'
        ]:
            current_office = col0_str.title()
            office_term = None
            continue

        # Check if this is term info (e.g., "SIX YEAR TERM")
        if col0_str and 'YEAR TERM' in col0_str.upper():
            office_term = col0_str
            continue

        # Check if this is vote instruction (e.g., "VOTE FOR ONE")
        if col0_str and 'VOTE FOR' in col0_str.upper():
            continue

        # Check if this is a candidate name in column 1
        if col1_str and current_office:
            candidate = col1_str

            # Look for party indicator (single letter R or D)
            party = None
            # Check column 2 for party
            if len(df.columns) > 2 and pd.notna(df.iloc[row_idx, 2]):
                party_val = str(df.iloc[row_idx, 2]).strip()
                if party_val in ['R', 'D', 'REP', 'DEM']:
                    party = 'Republican' if party_val in ['R', 'REP'] else 'Democratic'

            # Special handling for SCATTERED/Write-in
            if candidate.upper() == 'SCATTERED':
                candidate = 'Write-In'

            # Look for vote totals - typically in columns after candidate name
            # Find "Total" or "TOTALS" column
            total_votes = 0
            for col_idx in range(2, min(len(df.columns), 10)):  # Check first several columns
                cell_value = df.iloc[row_idx, col_idx]
                if pd.notna(cell_value):
                    try:
                        vote_val = int(float(cell_value))
                        # Use the rightmost numeric value as total
                        total_votes = vote_val
                    except (ValueError, TypeError):
                        pass

            # Create full office name with term if available
            full_office = current_office
            if office_term:
                full_office = f"{current_office} {office_term}"

            vote_totals[full_office][(candidate, party)] += total_votes

    # Convert to results list
    results = []
    for office, candidates in vote_totals.items():
        for (candidate, party), votes in candidates.items():
            results.append({
                'county': 'Crawford',
                'office': office,
                'district': None,
                'party': party,
                'candidate': candidate,
                'votes': votes
            })

    return results


def main():
    """Main function to parse both Crawford County Excel files and output CSV."""
    state_county_path = '/Users/dwillis/Downloads/Crawford PA Seeley - Fed, State and County.xlsx'
    local_races_path = '/Users/dwillis/Downloads/Crawford PA Seeley - Local Races.xlsx'
    output_dir = '2025/counties'
    output_file = '20251104__pa__general__crawford__county.csv'

    # Create output directory
    os.makedirs(output_dir, exist_ok=True)

    all_results = []

    # Parse state/county file
    print("Parsing Fed, State and County file...")
    state_results = parse_state_county_excel(state_county_path)
    print(f"  Found {len(state_results)} state/county results")
    all_results.extend(state_results)

    # Parse local races file
    print("Parsing Local Races file...")
    local_results = parse_local_races_excel(local_races_path)
    print(f"  Found {len(local_results)} local race results")
    all_results.extend(local_results)

    print(f"\nTotal results: {len(all_results)}")

    # Write to CSV
    output_path = os.path.join(output_dir, output_file)
    fieldnames = ['county', 'office', 'district', 'party', 'candidate', 'votes']

    with open(output_path, 'w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for result in all_results:
            writer.writerow(result)

    print(f"\nOutput written to: {output_path}")


if __name__ == '__main__':
    main()
