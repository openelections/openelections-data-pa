#!/usr/bin/env python3
"""
Parse Lehigh County CSV election results and produce OpenElections format.
Handles both county-level (summary) and precinct-level results.
"""

import csv
import sys
import re


def parse_lehigh_precinct_results(input_csv, output_csv, county_name="Lehigh"):
    """Parse Lehigh County precinct CSV and write to OpenElections format."""
    
    results = []
    
    with open(input_csv, 'r', encoding='utf-8') as f:
        # Skip first two header lines
        next(f)  # "Unofficial Election Results"
        next(f)  # "LEHIGH"
        
        reader = csv.DictReader(f)
        
        for row in reader:
            precinct = (row.get('Precinct') or '').strip()
            contest_name = (row.get('Contest Name') or '').strip()
            candidate_name = (row.get('Candidate Name') or '').strip()
            
            # Skip if missing essential data
            if not precinct or not contest_name or not candidate_name:
                continue
            
            # Extract vote count
            votes = (row.get('Votes') or '0').replace(',', '')
            
            # Normalize office names and extract districts
            office, district = normalize_office(contest_name)
            
            # Parse candidate name and extract party
            candidate, party = parse_candidate_name(candidate_name)
            
            results.append([county_name, precinct, office, district, party, 
                          candidate, votes])
    
    # Write to CSV
    with open(output_csv, 'w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        writer.writerow(['county', 'precinct', 'office', 'district', 'party', 
                        'candidate', 'votes'])
        writer.writerows(results)
    
    print(f"Results written to {output_csv}")
    print(f"Total rows: {len(results)}")


def parse_lehigh_county_results(input_csv, output_csv, county_name="Lehigh"):
    """Parse Lehigh County summary CSV and write to OpenElections county-level format."""
    
    results = []
    
    with open(input_csv, 'r', encoding='utf-8') as f:
        # Skip first two header lines
        next(f)  # "Unofficial Election Results"
        next(f)  # "LEHIGH"
        
        reader = csv.DictReader(f)
        
        for row in reader:
            contest_name = (row.get('Contest Name') or '').strip()
            candidate_name = (row.get('Candidate Name') or '').strip()
            
            # Skip if missing essential data
            if not contest_name or not candidate_name:
                continue
            
            # Extract vote counts
            total_votes = (row.get('Total Votes') or '0').replace(',', '')
            election_day = (row.get('Election Day Votes') or '0').replace(',', '')
            mail = (row.get('Mail Ballots Votes') or '0').replace(',', '')
            provisional = (row.get('Provisional Votes') or '0').replace(',', '')
            
            # Normalize office names and extract districts
            office, district = normalize_office(contest_name)
            
            # Parse candidate name and extract party
            candidate, party = parse_candidate_name(candidate_name)
            
            results.append([county_name, office, district, party, candidate, 
                          total_votes, election_day, mail, provisional])
    
    # Write to CSV
    with open(output_csv, 'w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        writer.writerow(['county', 'office', 'district', 'party', 'candidate',
                        'votes', 'election_day', 'mail', 'provisional'])
        writer.writerows(results)
    
    print(f"Results written to {output_csv}")
    print(f"Total rows: {len(results)}")


def normalize_office(contest_name):
    """Normalize office names and extract districts."""
    
    # Remove party prefixes that may appear in contest names
    contest_name = re.sub(r'^(DEM|REP|DAR|LBR|NOF)\s+', '', contest_name)
    
    office = contest_name
    district = ""
    
    # Judicial offices
    if contest_name == "JUDGE OF THE SUPERIOR COURT":
        office = "Judge of the Superior Court"
    elif contest_name == "JUDGE OF THE COMMONWEALTH COURT":
        office = "Judge of the Commonwealth Court"
    elif contest_name == "JUDGE OF THE COURT OF COMMON PLEAS":
        office = "Judge of the Court of Common Pleas"
    
    # County offices
    elif contest_name == "COUNTY EXECUTIVE":
        office = "County Executive"
    elif "COUNTY COMMISSIONER" in contest_name:
        office = "County Commissioner"
        # Extract district (e.g., "COUNTY COMMISSIONER - DISTRICT 1")
        district_match = re.search(r'DISTRICT\s+(\d+)', contest_name)
        if district_match:
            district = district_match.group(1)
    
    # Magisterial District Judge
    elif contest_name.startswith("MAGISTERIAL DISTRICT JUDGE"):
        office = "Magisterial District Judge"
        # Extract district (e.g., "MAGISTERIAL DISTRICT JUDGE 31-1-02")
        district_match = re.search(r'(\d+-\d+-\d+)', contest_name)
        if district_match:
            district = district_match.group(1)
    
    # Municipal offices
    elif "MAYOR" in contest_name:
        office = contest_name  # Preserve full name (e.g., "ALLENTOWN MAYOR")
    elif "CITY COUNCIL" in contest_name or "BOROUGH COUNCIL" in contest_name:
        office = contest_name  # Preserve full name
    elif "CITY CONTROLLER" in contest_name:
        office = contest_name
    elif "TOWNSHIP COUNCIL" in contest_name:
        office = contest_name
    elif "TOWNSHIP COMMISSIONER" in contest_name or "TOWNSHIP SUPERVISOR" in contest_name:
        office = contest_name
    
    # Tax Collector
    elif "TAX COLLECTOR" in contest_name:
        office = contest_name
    
    # Auditor
    elif "AUDITOR" in contest_name:
        office = contest_name
    
    # School Director
    elif "SCHOOL DIRECTOR" in contest_name:
        office = contest_name
    
    # Judge/Inspector of Election
    elif "JUDGE OF ELECTION" in contest_name:
        office = "Judge of Election"
        # Extract precinct code
        precinct_match = re.search(r'JUDGE OF ELECTION - (.+)', contest_name)
        if precinct_match:
            district = precinct_match.group(1)
    elif "INSPECTOR OF ELECTION" in contest_name:
        office = "Inspector of Election"
        # Extract precinct code
        precinct_match = re.search(r'INSPECTOR OF ELECTION - (.+)', contest_name)
        if precinct_match:
            district = precinct_match.group(1)
    
    # Retention elections
    elif "SUPREME COURT" in contest_name or "SUPERIOR COURT" in contest_name or "COMMONWEALTH COURT" in contest_name:
        office = contest_name
    
    # Ballot questions
    elif "QUESTION" in contest_name:
        office = contest_name
    
    return office, district


def parse_candidate_name(candidate_name):
    """Parse candidate name and extract party prefix if present.
    
    Returns:
        tuple: (candidate_name, party)
    """
    
    # Known party codes
    known_parties = {'DEM', 'REP', 'LBR', 'GRN', 'CST', 'FWD', 'ASP', 'DAR', 
                    'DEM/REP', 'REP/DEM', 'DEM/LIB', 'REP/LIB', 'IND', 'WOR', 
                    'GRE', 'LIN', 'PIA', 'PIU', 'NON', 'NOF'}
    
    # Check for party prefix like "DEM Brandon Neuman"
    party_match = re.match(r'^([A-Z]{2,7})\s+(.+)', candidate_name)
    if party_match and party_match.group(1) in known_parties:
        party = party_match.group(1)
        candidate = party_match.group(2).strip()
        
        # Normalize party codes
        if party in ['NON', 'NOF']:
            party = ''
        
        return candidate, party
    
    # No party prefix found
    return candidate_name.strip(), ''


if __name__ == "__main__":
    if len(sys.argv) < 4:
        print("Usage: python lehigh_parser.py <precinct|county> <input_csv> <output_csv> [county_name]")
        sys.exit(1)
    
    level = sys.argv[1].lower()
    input_csv = sys.argv[2]
    output_csv = sys.argv[3]
    county_name = sys.argv[4] if len(sys.argv) > 4 else "Lehigh"
    
    if level == "precinct":
        parse_lehigh_precinct_results(input_csv, output_csv, county_name)
    elif level == "county":
        parse_lehigh_county_results(input_csv, output_csv, county_name)
    else:
        print(f"Error: Unknown level '{level}'. Use 'precinct' or 'county'")
        sys.exit(1)
