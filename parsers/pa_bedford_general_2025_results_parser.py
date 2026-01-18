#!/usr/bin/env python3
"""
Parser for Bedford County, PA 2025 General Election Results.
Converts county-level PDF summary report to OpenElections CSV format.

Input: PDF file (ElectionSummaryReportRPT)
Output: CSV file with county-level results

Usage:
    python pa_bedford_general_2025_results_parser.py input.pdf output.csv
"""

import csv
import re
import subprocess
import sys
from typing import List, Dict, Optional


class BedfordCountyParser:
    """Parse Bedford County election summary report."""

    def __init__(self):
        self.county = "Bedford"
        self.results = []
        self.current_office = None
        self.current_district = None
        self.in_write_in_section = False

    def clean_text(self, text: str) -> str:
        """Remove extra whitespace and clean text."""
        return ' '.join(text.split())

    def parse_office_header(self, line: str) -> Optional[Dict[str, str]]:
        """
        Parse office header line.
        Examples:
            "Judge of the Superior Court (Vote for 1)"
            "Magisterial District Judge Magisterial District 57-03-01 (Vote for 1)"
            "Township Supervisor (6 Year Term) Bedford Township (Vote for 1)"
        """
        line = self.clean_text(line)

        # Skip non-office lines
        if not line or line.startswith('Precincts Reported:') or line.startswith('Registered Voters:'):
            return None

        # Check if this is an office header (ends with "Vote for X)")
        vote_pattern = r'\(Vote for \d+\)'
        if not re.search(vote_pattern, line):
            return None

        # Remove the (Vote for X) part
        office_text = re.sub(vote_pattern, '', line).strip()

        # Extract district if present
        district = None

        # Check for magisterial district
        district_match = re.search(r'Magisterial District (\d+-\d+-\d+)', office_text)
        if district_match:
            district = district_match.group(1)
            office_text = re.sub(r'Magisterial District \d+-\d+-\d+', '', office_text).strip()

        # Check for municipality (Borough/Township) - it's part of the office name
        # Keep the full office text including municipality

        return {
            'office': office_text,
            'district': district
        }

    def is_candidate_row(self, parts: List[str]) -> bool:
        """Check if row contains candidate data."""
        if len(parts) < 5:
            return False

        # Check if last 4 columns are numbers
        try:
            for i in range(-4, 0):
                int(parts[i].replace(',', ''))
            return True
        except (ValueError, IndexError):
            return False

    def is_summary_row(self, parts: List[str]) -> bool:
        """Check if row is Times Cast, Undervotes, or Overvotes."""
        if not parts:
            return False
        first = parts[0]
        return first in ['Times Cast', 'Undervotes', 'Overvotes']

    def parse_candidate_row(self, parts: List[str]) -> Optional[Dict[str, any]]:
        """
        Parse candidate result row.
        Format: Candidate | Party | Election Day | Mail-In | Provisional | Total
        Or: Candidate | Election Day | Mail-In | Provisional | Total (for write-ins without party)
        """
        if not self.is_candidate_row(parts):
            return None

        # Extract vote columns (last 4 values)
        total = int(parts[-1].replace(',', ''))
        provisional = int(parts[-2].replace(',', ''))
        mail_in = int(parts[-3].replace(',', ''))
        election_day = int(parts[-4].replace(',', ''))

        # Determine if there's a party column
        # Party should be 2nd column if present (DEM, REP, LBR, etc.)
        has_party = len(parts) >= 6 and parts[1] in ['DEM', 'REP', 'LBR', 'DEMREP', 'WRITE-IN']

        if has_party:
            candidate = parts[0]
            party = parts[1]
        else:
            # Join all parts except the last 4 (vote counts)
            candidate = ' '.join(parts[:-4])
            party = ''

        return {
            'candidate': candidate,
            'party': party,
            'election_day': election_day,
            'mail_in': mail_in,
            'provisional': provisional,
            'votes': total
        }

    def parse_summary_row(self, parts: List[str], row_type: str) -> Optional[Dict[str, any]]:
        """
        Parse Times Cast, Undervotes, or Overvotes row.
        These become special candidate entries.
        """
        if row_type == 'Times Cast':
            candidate = 'Ballots Cast'
        elif row_type == 'Undervotes':
            candidate = 'Undervotes'
        elif row_type == 'Overvotes':
            candidate = 'Overvotes'
        else:
            return None

        # Extract vote counts
        try:
            # Format can be: Times Cast | ED | Mail | Prov | Total / Registered | Turnout%
            # We want the 4 values before the "/" if present

            # Find numeric values
            values = []
            for part in parts[1:]:
                # Stop at "/" which indicates Registered Voters
                if '/' in part:
                    break
                try:
                    values.append(int(part.replace(',', '')))
                except ValueError:
                    continue

            if len(values) >= 4:
                return {
                    'candidate': candidate,
                    'party': '',
                    'election_day': values[0],
                    'mail_in': values[1],
                    'provisional': values[2],
                    'votes': values[3]
                }
        except (ValueError, IndexError):
            pass

        return None

    def extract_text_from_pdf(self, input_pdf: str) -> str:
        """Extract text from PDF using pdftotext -layout."""
        result = subprocess.run(
            ["pdftotext", "-layout", input_pdf, "-"],
            capture_output=True,
            text=True,
            check=True
        )
        return result.stdout

    def parse_text(self, text: str):
        """Parse the extracted text content."""
        registered_voters = self.extract_registered_voters(text)
        if registered_voters is not None:
            self.add_registered_voters(registered_voters)

        ballots_cast = self.extract_ballots_cast(text)
        if ballots_cast is not None:
            self.add_ballots_cast(ballots_cast)

        lines = self.normalize_lines(text)

        for i, line in enumerate(lines):
            line = line.strip()

            if not line:
                continue

            # Check for office header
            office_info = self.parse_office_header(line)
            if office_info:
                self.current_office = office_info['office']
                self.current_district = office_info['district']
                self.in_write_in_section = False
                continue

            # Skip header rows
            if line.startswith('Candidate') or line.startswith('Election Day'):
                continue

            # Check if entering write-in section
            if 'WRITE-IN' in line and self.current_office:
                self.in_write_in_section = True
                # This might also be a candidate row, so continue processing

            # Skip "Unresolved Write-In" rows
            if 'Unresolved Write-In' in line:
                continue

            # Split line into parts (tab or multiple spaces)
            parts = re.split(r'\t+|\s{2,}', line)
            parts = [p.strip() for p in parts if p.strip()]

            if not parts:
                continue

            # Check for summary rows (Times Cast, Undervotes, Overvotes)
            if self.is_summary_row(parts) and self.current_office:
                result = self.parse_summary_row(parts, parts[0])
                if result:
                    self.add_result(result)
                continue

            # Parse candidate row
            if self.current_office:
                result = self.parse_candidate_row(parts)
                if result:
                    self.add_result(result)

    def add_result(self, result: Dict[str, any]):
        """Add a parsed result to the results list."""
        if result.get('candidate') == 'Total Votes':
            return
        if result.get('party') == 'WRITE-IN':
            return
        row = {
            'county': self.county,
            'precinct': '',  # County-level has no precinct
            'office': self.current_office,
            'district': self.current_district or '',
            'party': result.get('party', ''),
            'candidate': result['candidate'],
            'votes': result['votes'],
            'election_day': result.get('election_day', ''),
            'mail': result.get('mail_in', ''),
            'provisional': result.get('provisional', '')
        }
        self.results.append(row)

    def add_registered_voters(self, total: int):
        """Add registered voters as a special row."""
        row = {
            'county': self.county,
            'precinct': '',
            'office': '',
            'district': '',
            'party': '',
            'candidate': 'Registered Voters',
            'votes': total,
            'election_day': '',
            'mail': '',
            'provisional': ''
        }
        self.results.append(row)

    def add_ballots_cast(self, counts: Dict[str, int]):
        """Add ballots cast as a special row with counting group splits."""
        row = {
            'county': self.county,
            'precinct': '',
            'office': '',
            'district': '',
            'party': '',
            'candidate': 'Ballots Cast',
            'votes': counts.get('total', ''),
            'election_day': counts.get('election_day', ''),
            'mail': counts.get('mail', ''),
            'provisional': counts.get('provisional', '')
        }
        self.results.append(row)

    def normalize_lines(self, text: str) -> List[str]:
        """Normalize text lines and merge split office headers."""
        raw_lines = text.split('\n')
        merged_lines = []
        i = 0
        while i < len(raw_lines):
            line = raw_lines[i].rstrip()
            next_line = raw_lines[i + 1].strip() if i + 1 < len(raw_lines) else ''

            if re.search(r'\(Vote\s*$', line.strip()) and next_line.startswith('for '):
                merged_lines.append(f"{line.strip()} {next_line}")
                i += 2
                continue

            if re.search(r'\(Vote for\s*$', line.strip()) and next_line.endswith(')'):
                merged_lines.append(f"{line.strip()} {next_line}")
                i += 2
                continue

            merged_lines.append(line)
            i += 1

        return merged_lines

    def extract_registered_voters(self, text: str) -> Optional[int]:
        """Extract registered voters total from summary section."""
        match = re.search(r'Registered Voters:\s*[\d,]+\s+of\s+([\d,]+)', text)
        if match:
            return int(match.group(1).replace(',', ''))

        match = re.search(r'Registered Voters:\s*([\d,]+)', text)
        if match:
            return int(match.group(1).replace(',', ''))

        return None

    def extract_ballots_cast(self, text: str) -> Optional[Dict[str, int]]:
        """Extract ballots cast totals by counting group from first page summary."""
        header_section = text.split('Precincts Reported:')[0]

        def find_count(label: str) -> Optional[int]:
            match = re.search(rf'{label}\s+([\d,]+)', header_section)
            if match:
                return int(match.group(1).replace(',', ''))
            return None

        election_day = find_count('Election Day')
        mail = find_count('Mail-In')
        provisional = find_count('Provisional')
        total = find_count('Total')

        if total is None:
            match = re.search(r'Ballots Cast:\s*([\d,]+)', text)
            if match:
                total = int(match.group(1).replace(',', ''))

        if total is None and election_day is None and mail is None and provisional is None:
            return None

        return {
            'election_day': election_day or '',
            'mail': mail or '',
            'provisional': provisional or '',
            'total': total or ''
        }

    def write_csv(self, output_file: str):
        """Write results to CSV file."""
        fieldnames = ['county', 'precinct', 'office', 'district', 'party',
                     'candidate', 'votes', 'election_day', 'mail', 'provisional']

        with open(output_file, 'w', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(self.results)

        print(f"Wrote {len(self.results)} rows to {output_file}")


def main():
    if len(sys.argv) != 3:
        print("Usage: python pa_bedford_general_2025_results_parser.py input.pdf output.csv")
        sys.exit(1)

    input_file = sys.argv[1]
    output_file = sys.argv[2]

    parser = BedfordCountyParser()

    # Extract text from PDF and parse
    text = parser.extract_text_from_pdf(input_file)
    parser.parse_text(text)

    # Write output
    parser.write_csv(output_file)


if __name__ == '__main__':
    main()
