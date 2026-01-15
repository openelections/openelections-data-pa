#!/usr/bin/env python3
"""
Parser for Carbon County, PA 2025 General Election Results

This parser handles the CSV format from Carbon County's Statement of Votes Cast.
The format has:
- Initial rows with registration and turnout statistics
- Contest sections with candidates in columns and precincts in rows
- Candidate columns include vote counts and percentages
- Multiple contests throughout the file

Usage:
    python pa_carbon_general_2025_results_parser.py <input_file> <output_file>
"""

import re
import csv
import sys
from typing import List, Dict, Optional, Tuple


class CarbonCounty2025Parser:
    def __init__(self):
        self.results = []
        self.current_office = None
        self.current_district = None
        self.found_first_office = False

    def normalize_precinct_name(self, precinct: str) -> str:
        """Normalize precinct names to match expected format."""
        # Remove extra whitespace
        precinct = ' '.join(precinct.split())
        return precinct.strip()

    def parse_office_header(self, row: List[str]) -> Optional[str]:
        """Extract office name from header row."""
        # Office headers appear in the first column
        first_col = row[0].strip() if row else ''

        # Look for vote count pattern: (Vote for N)
        if '(Vote for' in first_col:
            # Remove the vote count and privacy notice
            office = re.sub(r'\(Vote for\s+\d+\)', '', first_col)
            office = re.sub(r'\*+\s*-\s*Insufficient Turnout.*$', '', office)
            return office.strip()

        return None

    def parse_district(self, office: str) -> Optional[str]:
        """Extract district number from office name if present."""
        patterns = [
            r'District\s+(\d+)',
            r'DISTRICT\s+(\d+)',
            r'Dist\.\s+(\d+)',
            r'(\d+)(?:st|nd|rd|th)\s+District',
        ]

        for pattern in patterns:
            match = re.search(pattern, office, re.IGNORECASE)
            if match:
                return match.group(1)

        return None

    def extract_candidates_from_row(self, row: List[str]) -> List[Tuple[str, str]]:
        """
        Extract candidate names and parties from header row.
        Returns list of (candidate_name, party) tuples.

        Candidates appear as column headers with party codes in parentheses.
        Format: "CANDIDATE NAME (PARTY)"
        """
        candidates = []

        for i, cell in enumerate(row):
            cell = cell.strip()
            if not cell:
                continue

            # Skip common non-candidate headers
            if cell in ['Precinct', 'Times Cast', 'Registered Voters', 'Total Votes',
                       'Unresolved Write-In', '% Turnout', 'Voters Cast']:
                continue

            # Look for candidate with party code: NAME (PARTY)
            party_match = re.search(r'\(([A-Z]{3}(?:/[A-Z]{3})?)\)', cell)
            if party_match:
                party = party_match.group(1)
                # Remove party code to get candidate name
                candidate = re.sub(r'\s*\([A-Z]{3}(?:/[A-Z]{3})?\)', '', cell).strip()
                candidates.append((candidate, party, i))
            # Look for qualified write-ins
            elif 'Qualified Write In' in cell or 'Write-In' in cell:
                # Extract the name before "Qualified Write In"
                candidate_match = re.search(r'(.+?)\s+Qualified Write In', cell)
                if candidate_match:
                    candidate = candidate_match.group(1).strip()
                    candidates.append((candidate, 'WRI', i))
                else:
                    candidates.append((cell, 'WRI', i))

        return candidates

    def is_valid_precinct(self, precinct: str) -> bool:
        """Check if this is a valid precinct name (not a total or header)."""
        precinct_lower = precinct.lower()

        # Skip various totals and non-precinct rows
        if any(term in precinct_lower for term in [
            'total', 'cumulative', 'carbon county', 'registered', 'voters cast',
            '% turnout', 'unresolved write-in'
        ]):
            return False

        # Skip empty precincts
        if not precinct.strip():
            return False

        return True

    def parse_file(self, input_file: str):
        """Parse the entire CSV file."""
        with open(input_file, 'r', encoding='utf-8-sig') as f:
            reader = csv.reader(f)
            rows = list(reader)

        i = 0
        while i < len(rows):
            row = rows[i]

            # Check if this is an office header
            office = self.parse_office_header(row)
            if office:
                self.found_first_office = True
                self.current_office = office
                self.current_district = self.parse_district(office)
                print(f"Found office: {office}")

                # Next row should have candidate headers
                if i + 1 < len(rows):
                    i += 1
                    candidates = self.extract_candidates_from_row(rows[i])
                    if candidates:
                        print(f"Candidates: {[(c[0], c[1]) for c in candidates]}")

                        # Now process data rows until we hit another office or end
                        i += 1
                        while i < len(rows):
                            data_row = rows[i]

                            # Check if this is a new office header
                            if self.parse_office_header(data_row):
                                # Back up one row so we process this office next
                                i -= 1
                                break

                            # Process this data row
                            if not data_row or len(data_row) == 0:
                                i += 1
                                continue

                            precinct = data_row[0].strip() if data_row[0] else ''

                            # Check if this is a valid precinct
                            if not self.is_valid_precinct(precinct):
                                i += 1
                                continue

                            precinct = self.normalize_precinct_name(precinct)

                            # Extract votes for each candidate
                            for candidate, party, col_idx in candidates:
                                if col_idx < len(data_row):
                                    vote_str = data_row[col_idx].strip().replace(',', '')

                                    # Skip percentage values and empty cells
                                    if '%' in vote_str or not vote_str:
                                        continue

                                    try:
                                        votes = int(vote_str)

                                        self.results.append({
                                            'county': 'Carbon',
                                            'precinct': precinct,
                                            'office': self.current_office,
                                            'district': self.current_district or '',
                                            'party': party,
                                            'candidate': candidate,
                                            'votes': votes
                                        })
                                    except ValueError:
                                        # Not a valid vote count
                                        continue

                            i += 1
                        continue

                i += 1
                continue

            # Check for registration/turnout data in early rows (only before first office)
            if not self.found_first_office and len(row) >= 3 and row[0].strip():
                precinct = row[0].strip()

                if self.is_valid_precinct(precinct):
                    precinct = self.normalize_precinct_name(precinct)

                    # Check if we have registration and turnout data
                    # Format: Precinct, "Registered Voters", "Voters Cast", "", "% Turnout"
                    try:
                        reg_voters_str = row[1].strip().replace(',', '') if len(row) > 1 else ''
                        voters_cast_str = row[2].strip().replace(',', '') if len(row) > 2 else ''

                        if reg_voters_str and reg_voters_str.isdigit():
                            reg_voters = int(reg_voters_str)

                            # Add Registered Voters
                            self.results.append({
                                'county': 'Carbon',
                                'precinct': precinct,
                                'office': 'Registered Voters',
                                'district': '',
                                'party': '',
                                'candidate': '',
                                'votes': reg_voters
                            })

                        if voters_cast_str and voters_cast_str.isdigit():
                            voters_cast = int(voters_cast_str)

                            # Add Ballots Cast
                            self.results.append({
                                'county': 'Carbon',
                                'precinct': precinct,
                                'office': 'Ballots Cast',
                                'district': '',
                                'party': '',
                                'candidate': '',
                                'votes': voters_cast
                            })
                    except (ValueError, IndexError):
                        pass

            i += 1

    def write_csv(self, output_file: str):
        """Write results to CSV file."""
        with open(output_file, 'w', newline='') as f:
            fieldnames = ['county', 'precinct', 'office', 'district', 'party', 'candidate', 'votes']
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(self.results)


def main():
    if len(sys.argv) != 3:
        print("Usage: python pa_carbon_general_2025_results_parser.py <input_file> <output_file>")
        sys.exit(1)

    input_file = sys.argv[1]
    output_file = sys.argv[2]

    parser = CarbonCounty2025Parser()
    parser.parse_file(input_file)
    parser.write_csv(output_file)

    print(f"Parsed {len(parser.results)} result rows")
    print(f"Output written to {output_file}")


if __name__ == '__main__':
    main()
