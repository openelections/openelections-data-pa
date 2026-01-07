#!/usr/bin/env python3
"""
Parse Elk County election results PDF and produce CSV output.
"""

import csv
import re
import sys
import pdfplumber


def parse_election_results(pdf_path, output_csv):
    """Parse the election results PDF and write to CSV."""
    
    # Initialize data structure
    results = []
    county = None
    precinct = None
    
    with pdfplumber.open(pdf_path) as pdf:
        # Track current office and district
        current_office = None
        current_district = None
        
        for page in pdf.pages:
            text = page.extract_text()
            lines = text.split('\n')
            
            i = 0
            while i < len(lines):
                line = lines[i].strip()
                
                # Extract county name from first page (appears after date, before first precinct)
                if county is None:
                    # Look for pattern like "November 5, 2024 Elk" - county name comes after date
                    date_match = re.search(r'(?:November|October|January|February|March|April|May|June|July|August|September|December)\s+\d{1,2},\s+\d{4}\s+(\w+)', line)
                    if date_match:
                        county = date_match.group(1).title()
                
                # Extract precinct name (appears directly before STATISTICS)
                if line == "STATISTICS" and i > 0:
                    # Look backwards for precinct name
                    for j in range(i-1, max(0, i-5), -1):
                        prev_line = lines[j].strip()
                        # Skip empty lines and common headers
                        if prev_line and prev_line not in ["", "TOTAL", "VOTES", "ELECTION DAY", "MAIL-IN", "PROVISIONAL", "MAIL- Provisional"]:
                            # Check if it's not a date or page number
                            if not re.match(r'^\d+$', prev_line) and not re.match(r'^\d{1,2}/\d{1,2}/\d{4}', prev_line):
                                # Skip if it contains county name or common headers
                                if (county and county.upper() not in prev_line.upper() and 
                                    "OFFICIAL" not in prev_line.upper() and
                                    "RESULTS" not in prev_line.upper() and
                                    "General" not in prev_line and
                                    "Summary" not in prev_line):
                                    precinct = prev_line
                                    break
                
                # Parse statistics section
                if line == "STATISTICS":
                    i += 1
                    # Skip header line
                    i += 1
                    while i < len(lines):
                        line = lines[i].strip()
                        # Break on empty line or when we hit an office header
                        if not line or line.startswith("Vote For"):
                            break
                        # Break on any office header
                        if (line == "PRESIDENTIAL ELECTORS" or 
                            line == "UNITED STATES SENATOR" or
                            line == "ATTORNEY GENERAL" or
                            line == "AUDITOR GENERAL" or
                            line == "STATE TREASURER" or
                            line == "Judge of the Superior Court" or
                            line == "Judge of the Commonwealth Court" or
                            line == "Judge of the Court of Common Pleas" or
                            "Retention" in line or
                            line.startswith("REP CONGRESS") or
                            line.startswith("REPRESENTATIVE IN CONGRESS") or
                            (line.startswith("SENATOR") and "GENERAL ASSEMBLY" in line) or
                            line.startswith("REP IN THE GENERAL ASSEMBLY") or
                            line.startswith("REPRESENTATIVE IN THE GENERAL ASSEMBLY")):
                            break
                        
                        # Registered Voters
                        if line.startswith("Registered Voters - Total"):
                            parts = line.split()
                            total = parts[-1].replace(',', '')
                            results.append([county, precinct, "Registered Voters", "", "", "", total, "", "", ""])
                        
                        # Ballots Cast
                        elif line.startswith("Ballots Cast - Total"):
                            parts = line.split()
                            total = parts[-4].replace(',', '')
                            election_day = parts[-3].replace(',', '')
                            mail = parts[-2].replace(',', '')
                            provisional = parts[-1].replace(',', '')
                            results.append([county, precinct, "Ballots Cast", "", "", "", total, election_day, mail, provisional])
                        
                        # Ballots Cast - Blank
                        elif line.startswith("Ballots Cast - Blank"):
                            parts = line.split()
                            total = parts[-4].replace(',', '')
                            election_day = parts[-3].replace(',', '')
                            mail = parts[-2].replace(',', '')
                            provisional = parts[-1].replace(',', '')
                            results.append([county, precinct, "Ballots Cast Blank", "", "", "", total, election_day, mail, provisional])
                        
                        i += 1
                    continue
                
                # Parse office headers
                if line == "PRESIDENTIAL ELECTORS":
                    current_office = "President"
                    current_district = ""
                elif line == "UNITED STATES SENATOR":
                    current_office = "U.S. Senate"
                    current_district = ""
                elif line == "ATTORNEY GENERAL":
                    current_office = "Attorney General"
                    current_district = ""
                elif line == "AUDITOR GENERAL":
                    current_office = "Auditor General"
                    current_district = ""
                elif line == "STATE TREASURER":
                    current_office = "State Treasurer"
                    current_district = ""
                # U.S. House patterns
                elif line.startswith("REP CONGRESS") or line.startswith("REPRESENTATIVE IN CONGRESS"):
                    current_office = "U.S. House"
                    # Extract district number
                    district_match = re.search(r'(\d+)(?:TH|ST|ND|RD)', line)
                    current_district = district_match.group(1) if district_match else ""
                # State Senate patterns
                elif line.startswith("SENATOR") and "GENERAL ASSEMBLY" in line:
                    current_office = "State Senate"
                    # Extract district number
                    district_match = re.search(r'(\d+)(?:TH|ST|ND|RD)', line)
                    current_district = district_match.group(1) if district_match else ""
                # State House patterns
                elif line.startswith("REP IN THE GENERAL ASSEMBLY") or line.startswith("REPRESENTATIVE IN THE GENERAL ASSEMBLY"):
                    current_office = "State House"
                    # Extract district number
                    district_match = re.search(r'(\d+)(?:TH|ST|ND|RD)', line)
                    current_district = district_match.group(1) if district_match else ""
                # Judicial offices (2025 format)
                elif line == "Judge of the Superior Court":
                    current_office = "Judge of the Superior Court"
                    current_district = ""
                elif line == "Judge of the Commonwealth Court":
                    current_office = "Judge of the Commonwealth Court"
                    current_district = ""
                elif line == "Judge of the Court of Common Pleas":
                    current_office = "Judge of the Court of Common Pleas"
                    current_district = ""
                # Retention elections
                elif "Retention" in line:
                    # e.g., "Supreme Court Retention - Christine Donohue"
                    current_office = line
                    current_district = ""
                # Magisterial District Judge
                elif line.startswith("Magisterial District Judge "):
                    current_office = "Magisterial District Judge"
                    current_district = line.replace("Magisterial District Judge ", "")
                # County offices (2025 format)
                elif line == "Clerk of Courts":
                    current_office = "Clerk of Courts"
                    current_district = ""
                elif line == "County Treasurer":
                    current_office = "County Treasurer"
                    current_district = ""
                elif line == "Sheriff":
                    current_office = "Sheriff"
                    current_district = ""
                # Local offices with municipalities/districts in the line
                elif line.startswith("Council Member "):
                    current_office = "Council Member"
                    current_district = line.replace("Council Member ", "")
                elif line.startswith("School Director "):
                    current_office = "School Director"
                    current_district = line.replace("School Director ", "")
                elif line.startswith("Judge of Elections "):
                    current_office = "Judge of Elections"
                    current_district = line.replace("Judge of Elections ", "")
                elif line.startswith("Inspector of Elections "):
                    current_office = "Inspector of Elections"
                    current_district = line.replace("Inspector of Elections ", "")
                elif line.startswith("Mayor "):
                    current_office = "Mayor"
                    current_district = line.replace("Mayor ", "")
                elif line.startswith("Tax Collector "):
                    current_office = "Tax Collector"
                    current_district = line.replace("Tax Collector ", "")
                elif line.startswith("Supervisor "):
                    current_office = "Supervisor"
                    current_district = line.replace("Supervisor ", "")
                elif line.startswith("Auditor "):
                    current_office = "Auditor"
                    current_district = line.replace("Auditor ", "")
                elif line.startswith("Constable "):
                    current_office = "Constable"
                    current_district = line.replace("Constable ", "")
                
                # Parse candidate lines when we have an office set
                if current_office and line:
                    # Skip lines that contain office headers within them
                    if "GENERAL ASSEMBLY" in line and line.startswith("REP"):
                        i += 1
                        continue
                    
                    # Check for party abbreviations at start
                    party_match = re.match(r'^(DEM|REP|LIB|LBR|GRN|CST|FWD|ASP|DAR)\s+(.+)', line)
                    
                    if party_match:
                        party = party_match.group(1)
                        rest = party_match.group(2)
                        
                        # Split the rest to get candidate name and vote counts
                        parts = rest.split()
                        if len(parts) >= 4:
                            # Last 4 are: total, election_day, mail, provisional
                            votes = [v.replace(',', '') for v in parts[-4:]]
                            candidate = ' '.join(parts[:-4])
                            
                            # For President, remove VP/running mate (everything after " - ")
                            if current_office == "President" and " - " in candidate:
                                candidate = candidate.split(" - ")[0]
                            
                            # Skip if candidate name looks like an office header
                            if "GENERAL ASSEMBLY" not in candidate:
                                results.append([
                                    county, precinct, current_office, current_district,
                                    party, candidate, votes[0], votes[1], votes[2], votes[3]
                                ])
                    
                    # Check for Yes/No votes (retention elections)
                    elif line.startswith("Yes ") or line.startswith("No "):
                        parts = line.split()
                        if len(parts) >= 5:
                            candidate = parts[0]  # "Yes" or "No"
                            votes = [v.replace(',', '') for v in parts[-4:]]
                            results.append([
                                county, precinct, current_office, current_district,
                                "", candidate, votes[0], votes[1], votes[2], votes[3]
                            ])
                    
                    # Check for write-in totals
                    elif line.startswith("Write-In Totals"):
                        parts = line.split()
                        if len(parts) >= 5:
                            votes = [v.replace(',', '') for v in parts[-4:]]
                            results.append([
                                county, precinct, current_office, current_district,
                                "", "Write-ins", votes[0], votes[1], votes[2], votes[3]
                            ])
                    
                    # Check for not assigned
                    elif line.startswith("Not Assigned"):
                        parts = line.split()
                        if len(parts) >= 1:
                            votes = [v.replace(',', '') for v in parts[-4:]]
                            results.append([
                                county, precinct, current_office, current_district,
                                "", "Not Assigned", votes[0], votes[1], votes[2], votes[3]
                            ])
                    
                    # Check for overvotes/undervotes
                    elif line.startswith("Overvotes"):
                        parts = line.split()
                        if len(parts) >= 1:
                            votes = [v.replace(',', '') for v in parts[-4:]]
                            results.append([
                                county, precinct, current_office, current_district,
                                "", "Over Votes", votes[0], votes[1], votes[2], votes[3]
                            ])
                    elif line.startswith("Undervotes"):
                        parts = line.split()
                        if len(parts) >= 1:
                            votes = [v.replace(',', '') for v in parts[-4:]]
                            results.append([
                                county, precinct, current_office, current_district,
                                "", "Under Votes", votes[0], votes[1], votes[2], votes[3]
                            ])
                
                i += 1
    
    # Write to CSV
    with open(output_csv, 'w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        writer.writerow(['county', 'precinct', 'office', 'district', 'party', 'candidate', 
                        'votes', 'election_day', 'mail', 'provisional'])
        writer.writerows(results)
    
    print(f"Results written to {output_csv}")
    print(f"Total rows: {len(results)}")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage: python electionware_new.py <pdf_path> <output_csv>")
        sys.exit(1)
    
    pdf_path = sys.argv[1]
    output_csv = sys.argv[2]
    
    parse_election_results(pdf_path, output_csv)