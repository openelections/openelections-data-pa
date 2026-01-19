#!/usr/bin/env python3
"""
Parser for Philadelphia County 2025 General Election Results

Processes Excel files from Philadelphia's election results website to produce
county-level results in OpenElections standardized format.

Each Excel file contains county-level results for one or more offices.
File structure:
- Row 1-5: Header information (election name, city, reporting status, timestamp)
- Row 6 onwards: Empty row, then office name with candidates, then Philadelphia data row

Usage:
    python parsers/pa_philadelphia_general_2025_results_parser.py

Output:
    2025/counties/20251104__pa__general__philadelphia__county.csv
"""

import pandas as pd
import csv
import os
import re
from glob import glob


def parse_candidate_name_party(candidate_str):
    """
    Extract candidate name and party from strings like 'LARRY KRASNER DEM' or 'Yes' or 'Write-In'

    Returns:
        tuple: (candidate_name, party) where party may be None
    """
    if pd.isna(candidate_str) or candidate_str in ['County', 'Yes', 'No', 'Write-In']:
        # Special cases: retention votes or write-ins
        if candidate_str == 'Write-In':
            return ('Write-In', None)
        elif candidate_str in ['Yes', 'No']:
            return (candidate_str, None)
        else:
            return (None, None)

    # Try to extract party from end of string
    parts = candidate_str.strip().split()
    if len(parts) > 1:
        # Check if last part is a party code (3 letters, all caps)
        potential_party = parts[-1]
        if len(potential_party) == 3 and potential_party.isupper():
            candidate = ' '.join(parts[:-1])
            party = potential_party
            return (candidate, party)

    # No party found
    return (candidate_str, None)


def parse_office_name(office_str):
    """
    Parse office name and extract district if present.
    Also normalize office names to standard OpenElections format.

    Examples:
        'DISTRICT ATTORNEY (VOTE FOR 1)' -> ('District Attorney', None)
        'JUDGE OF THE SUPERIOR COURT (VOTE FOR 1)' -> ('Judge of the Superior Court', None)
        'SUPREME COURT RETENTION - CHRISTINE DONOHUE (VOTE FOR 1)' -> ('Supreme Court Retention', None)

    Returns:
        tuple: (office_name, district)
    """
    # Remove vote count instructions
    office = re.sub(r'\s*\(VOTE FOR \d+\)', '', office_str).strip()

    # Normalize to title case for consistency
    office = office.title()

    # Check for retention elections
    if 'RETENTION' in office_str:
        # Extract judge name if present
        match = re.search(r'RETENTION\s*-\s*(.+?)(?:\s*\(|$)', office_str)
        if match:
            judge_name = match.group(1).strip().title()
            office = office.split(' - ')[0]  # Keep "Supreme Court Retention" or similar
            # We'll store the judge name as the base office
            return (f"{office} - {judge_name}", None)

    # Extract district number if present
    district = None
    district_match = re.search(r'(?:DISTRICT|DIVISION)\s+(\d+)', office_str, re.IGNORECASE)
    if district_match:
        district = district_match.group(1)

    return (office, district)


def parse_excel_file(file_path):
    """
    Parse a single Excel file and extract election results.

    Returns:
        list of dict: Each dict represents a result row with keys:
            county, office, district, party, candidate, votes
    """
    results = []

    # Read the Excel file, skipping header rows
    df = pd.read_excel(file_path, skiprows=5, header=None)

    if df.empty or len(df) < 2:
        return results

    # Find the office and candidates row (row with office name in first column or second column)
    office_row = None
    for idx, row in df.iterrows():
        # Check if this looks like an office header row
        # (has office name and candidate names)
        if pd.notna(row.iloc[0]) and ('VOTE FOR' in str(row.iloc[0]).upper() or 'RETENTION' in str(row.iloc[0]).upper()):
            office_row = row
            office_idx = idx
            break
        elif pd.notna(row.iloc[1]) and row.iloc[1] == 'County':
            # This might be a different format - check previous row
            if idx > 0:
                prev_row = df.iloc[idx - 1]
                if pd.notna(prev_row.iloc[0]):
                    office_row = row
                    office_idx = idx
                    break

    if office_row is None:
        # Try alternate parsing: first non-empty row after blanks
        for idx, row in df.iterrows():
            if idx > 0 and pd.notna(row.iloc[1]) and row.iloc[1] == 'County':
                office_row = row
                office_idx = idx
                break

    if office_row is None:
        print(f"Warning: Could not find office row in {file_path}")
        return results

    # Extract office name (first column or infer from filename)
    office_name_raw = None
    if pd.notna(office_row.iloc[0]):
        office_name_raw = office_row.iloc[0]

    if office_name_raw is None or office_name_raw == '':
        # Try to infer from filename
        basename = os.path.basename(file_path)
        if 'District Attorney' in basename:
            office_name_raw = 'DISTRICT ATTORNEY (VOTE FOR 1)'
        elif 'STATEWIDE JUDICIAL' in basename:
            office_name_raw = 'JUDGE OF THE SUPERIOR COURT (VOTE FOR 1)'
        else:
            print(f"Warning: Could not determine office name in {file_path}")
            return results

    office_name, district = parse_office_name(office_name_raw)

    # Find candidates (all columns after 'County' column)
    county_col_idx = None
    for idx, val in enumerate(office_row):
        if val == 'County':
            county_col_idx = idx
            break

    if county_col_idx is None:
        print(f"Warning: Could not find County column in {file_path}")
        return results

    # Candidates are in columns after County
    candidates = []
    for col_idx in range(county_col_idx + 1, len(office_row)):
        candidate_str = office_row.iloc[col_idx]
        if pd.notna(candidate_str) and candidate_str not in ['TOTAL', 'Votes In Total', '']:
            candidate_name, party = parse_candidate_name_party(candidate_str)
            if candidate_name:
                candidates.append({
                    'col_idx': col_idx,
                    'candidate': candidate_name,
                    'party': party
                })

    # Find Philadelphia data row (next non-empty row after office row)
    philly_row = None
    for idx in range(office_idx + 1, len(df)):
        row = df.iloc[idx]
        if pd.notna(row.iloc[county_col_idx]) and row.iloc[county_col_idx] == 'Philadelphia':
            philly_row = row
            break

    if philly_row is None:
        print(f"Warning: Could not find Philadelphia data row in {file_path}")
        return results

    # Extract vote counts for each candidate
    for candidate_info in candidates:
        col_idx = candidate_info['col_idx']
        votes = philly_row.iloc[col_idx]

        if pd.notna(votes):
            result = {
                'county': 'Philadelphia',
                'office': office_name,
                'district': district,
                'party': candidate_info['party'],
                'candidate': candidate_info['candidate'],
                'votes': int(votes) if isinstance(votes, (int, float)) else votes
            }
            results.append(result)

    return results


def main():
    """
    Main function to process all Excel files in the Philadelphia folder
    and output a consolidated CSV file.
    """
    input_dir = '/Users/dwillis/Downloads/philly'
    output_dir = '2025/counties'
    output_file = '20251104__pa__general__philadelphia__county.csv'

    # Create output directory if it doesn't exist
    os.makedirs(output_dir, exist_ok=True)

    # Find all Excel files
    excel_files = glob(os.path.join(input_dir, '*.xlsx'))
    print(f"Found {len(excel_files)} Excel files")

    # Process each file
    all_results = []
    for file_path in sorted(excel_files):
        print(f"Processing: {os.path.basename(file_path)}")
        results = parse_excel_file(file_path)
        all_results.extend(results)
        print(f"  -> Extracted {len(results)} results")

    print(f"\nTotal results: {len(all_results)}")

    # Write to CSV
    output_path = os.path.join(output_dir, output_file)

    if all_results:
        fieldnames = ['county', 'office', 'district', 'party', 'candidate', 'votes']

        with open(output_path, 'w', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for result in all_results:
                writer.writerow(result)

        print(f"\nOutput written to: {output_path}")
    else:
        print("No results extracted!")


if __name__ == '__main__':
    main()
