#!/usr/bin/env python3
import re
import csv
import sys
from typing import List, Dict, Optional, TextIO

class ElectionResultsParser:
    def __init__(self):
        self.current_precinct = None
        self.current_office = None
        self.current_district = None
        self.registered_voters = None
        self.ballots_cast = None
        self.ballots_cast_blank = None
        
    def parse_district(self, office: str) -> Optional[str]:
        """Extract district number from office name if present."""
        # Look for district number after keywords
        for keyword in ['DISTRICT', 'DIST', 'Dist']:
            if keyword in office:
                parts = office.split(keyword)
                if parts[0]:  # Look before the keyword
                    match = re.search(r'(\d+)(?:st|nd|rd|th)?', parts[0])
                    if match:
                        return match.group(1)
                if len(parts) > 1:  # Look after the keyword
                    match = re.search(r'(\d+)(?:st|nd|rd|th)?', parts[1])
                    if match:
                        return match.group(1)
        
        # Look for district number in ASSEMBLY references
        if 'ASSEMBLY' in office:
            match = re.search(r'ASSEMBLY[^0-9]*(\d+)(?:st|nd|rd|th)?', office)
            if match:
                return match.group(1)
        
        return None
        
    def is_precinct_header(self, line: str) -> bool:
        """Check if line is a precinct header."""
        # Split by tabs to check for multiple columns
        parts = line.strip().split('\t')
        if len(parts) > 1:
            return False
            
        # Skip any row containing certain office terms
        exclude_terms = [
            "PRESIDENT",
            "SENATE",
            "SENATOR",
            "ATTORNEY GENERAL",
            "AUDITOR GENERAL",
            "STATE TREASURER",
            "CONGRESS",
            "GENERAL ASSEMBLY",
            "NORTHAMPTON COUNTY HOME RULE CHARTER AMENDMENT"
        ]
        
        text = parts[0].strip()
        if any(term in text.upper() for term in exclude_terms):
            return False
            
        # Make sure there are no integer columns
#        if any(c.isdigit() for c in text):
#            return False
            
        return bool(text)  # Return True if we have non-empty text

    def parse_registered_voters(self, line: str) -> Optional[int]:
        """Parse registered voters line."""
        if line.startswith('Registered Voters'):
            parts = line.split()
            if len(parts) >= 2:
                return int(parts[-1].replace(',', ''))
        return None

    def parse_ballots_cast(self, line: str) -> Optional[Dict]:
        """Parse ballots cast line."""
        if line.startswith('Ballots Cast'):
            parts = line.strip().split('\t')
            if len(parts) >= 4:
                return {
                    'total': int(parts[1].replace(',', '')),
                    'election_day': int(parts[2].replace(',', '')),
                    'mail': int(parts[3].replace(',', '')),
                    'provisional': int(parts[4].replace(',', '')) if len(parts) > 4 else 0
                }
        return None

    def parse_ballots_cast_blank(self, line: str) -> Optional[Dict]:
        """Parse blank ballots line."""
        if line.startswith('Ballots Cast Blank'):
            parts = line.strip().split('\t')
            if len(parts) >= 4:
                return {
                    'total': int(parts[1].replace(',', '')),
                    'election_day': int(parts[2].replace(',', '')),
                    'mail': int(parts[3].replace(',', '')),
                    'provisional': int(parts[4].replace(',', '')) if len(parts) > 4 else 0
                }
        return None
    
    def normalize_office_name(self, office: str) -> str:
        """Normalize office names to standard format."""
        if office == "SENATOR IN THE GENERAL ASSEMBLY":
            return "State Senate"
        elif "REP IN CONGRESS" in office or "Representative in Congress" in office:
            return "U.S. House"
        elif "REP IN THE GENERAL ASSEMBLY" in office or "Representative in the General Assembly" in office:
            return "General Assembly"
        else:
            return office.title()
        
    def is_office_header(self, line: str) -> bool:
        """Check if line is an office title."""
        # Skip special header types
        if any(line.startswith(x) for x in ['Registered Voters', 'Ballots Cast']):
            return False
        if line.startswith('Representative in') or line.startswith('State Treasurer') or line.startswith('Attorney General') or line.startswith('Auditor General') or line.startswith('President') or line.startswith('Northampton County Home Rule Charter Amendment'):
            return True
        # First word should be capitalized and longer than 3 chars
        words = line.strip().split()
        if not words:
            return False
        first_word = words[0]
        return (len(first_word) > 3 and 
                first_word.isupper() and
                not any(c.isdigit() for c in first_word))
                
    def parse_candidate_row(self, line: str) -> Optional[Dict]:
        """Parse a candidate results row."""
        parts = line.strip().split()
        if not parts or not any(p.isdigit() for p in parts):
            return None
            
        # Find where the numeric values start
        number_start_idx = 0
        for i, part in enumerate(parts):
            if part.replace(',', '').isdigit():
                number_start_idx = i
                break
                
        # Extract candidate name and party
        name_parts = parts[:number_start_idx]
        party = None
        if len(name_parts) >= 1 and len(name_parts[0]) == 3 and name_parts[0].isupper():
            party = name_parts[0]
            name_parts = name_parts[1:]
            
        candidate = ' '.join(name_parts)
        
        # Parse vote counts, skipping percentage values
        vote_counts = []
        for part in parts[1:]:
            # Skip if part contains a percentage
            if '%' in part:
                continue
            try:
                vote_counts.append(int(part.strip().replace(',', '')))
            except ValueError:
                continue
                
        if len(vote_counts) == 0:
            return None
                      
        if len(vote_counts) == 1:
            votes = vote_counts[0]
            election_day = None
            mail = None
            provisional = None
        elif len(vote_counts) >= 4:
            votes = vote_counts[0]
            election_day = vote_counts[1]
            mail = vote_counts[2]
            provisional = vote_counts[3]
        else:
            return None
            
        return {
            'party': party,
            'candidate': candidate,
            'votes': votes,
            'election_day': election_day,
            'mail': mail,
            'provisional': provisional
        }

    def write_metadata_row(self, writer: csv.DictWriter, office: str, values: Dict, county) -> None:
        """Write a metadata row (registered voters, ballots cast) to CSV."""
        row = {
            'county': county,
            'precinct': self.current_precinct,
            'office': office,
            'district': None,
            'party': None,
            'candidate': None,
            'votes': values['total'],
            'election_day': values.get('election_day'),
            'mail': values.get('mail'),
            'provisional': values.get('provisional')
        }
        writer.writerow(row)

    def parse_file(self, input_file: TextIO, output_file: TextIO, county: str) -> None:
        """Parse the election results file and write to CSV."""
        writer = csv.DictWriter(output_file, fieldnames=[
            'county', 'precinct', 'office', 'district', 'party',
            'candidate', 'votes', 'election_day', 'mail', 'provisional'
        ])
        writer.writeheader()
        
        county = county  # Hardcoded for this specific file
        
        for line in input_file:
            line = line.strip()
            if not line:
                continue

            # Check for precinct header
            if self.is_precinct_header(line):
                self.current_precinct = line
                self.registered_voters = None
                self.ballots_cast = None
                self.ballots_cast_blank = None
                continue

            # Check for registered voters
            reg_voters = self.parse_registered_voters(line)
            if reg_voters is not None:
                self.write_metadata_row(writer, "Registered Voters", {'total': reg_voters}, county)
                continue

            # Check for ballots cast
            ballots = self.parse_ballots_cast(line)
            if ballots is not None:
                self.write_metadata_row(writer, "Ballots Cast", ballots, county)
                continue

            # Check for blank ballots
            blank_ballots = self.parse_ballots_cast_blank(line)
            if blank_ballots is not None:
                self.write_metadata_row(writer, "Ballots Cast - Blank", blank_ballots, county)
                continue
                
            # Check for office header
            if self.is_office_header(line):
                self.current_office = self.normalize_office_name(line)
                self.current_district = self.parse_district(line)
                continue
                
            # Parse candidate row
            result = self.parse_candidate_row(line)
            if result:
                row = {
                    'county': county,
                    'precinct': self.current_precinct,
                    'office': self.current_office,
                    'district': self.current_district,
                    **result
                }
                writer.writerow(row)

def main():
    if len(sys.argv) != 4:
        print(f"Usage: {sys.argv[0]} <input_file> <output_file> <county>", file=sys.stderr)
        sys.exit(1)
        
    with open(sys.argv[1], 'r', encoding='utf-8') as infile, \
         open(sys.argv[2], 'w', encoding='utf-8', newline='') as outfile:
        parser = ElectionResultsParser()
        parser.parse_file(infile, outfile, sys.argv[3])

if __name__ == '__main__':
    main()