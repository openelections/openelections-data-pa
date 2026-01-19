#!/usr/bin/env python3
"""
Parse election results PDF by extracting text with pdftotext and producing CSV output.
"""

import csv
import re
import sys
import subprocess
import tempfile
import os


def parse_election_results(pdf_path, output_csv):
    """Parse the election results PDF by extracting text with pdftotext and writing to CSV."""
    
    # Create a temporary file for the extracted text
    with tempfile.NamedTemporaryFile(mode='w+', suffix='.txt', delete=False) as temp_file:
        temp_text_path = temp_file.name
    
    try:
        # Extract text from PDF using pdftotext with -layout option
        subprocess.run(['pdftotext', '-layout', pdf_path, temp_text_path], 
                      check=True, capture_output=True, text=True)
        
        # Parse the extracted text
        _parse_text_file(temp_text_path, output_csv)
    
    finally:
        # Clean up temporary file
        if os.path.exists(temp_text_path):
            os.remove(temp_text_path)


def _parse_text_file(text_path, output_csv):
    
    results = []
    county = None
    precinct = None
    current_office = None
    current_district = None
    
    with open(text_path, 'r', encoding='utf-8') as f:
        lines = f.readlines()
    
    i = 0
    while i < len(lines):
        line = lines[i].rstrip()
        stripped = line.strip()
        
        # Extract county from header (appears after date)
        if county is None:
            date_match = re.search(r'(?:November|October|January|February|March|April|May|June|July|August|September|December)\s+\d{1,2},\s+\d{4}\s+(\w+)\s+County', line)
            if date_match:
                county = date_match.group(1)
        
        # Extract precinct name (appears on its own line, before STATISTICS or JUDGE/SCHOOL/etc.)
        # Precinct names are short lines (usually < 60 chars) that precede either STATISTICS or office names
        if stripped and len(stripped) < 60 and not re.search(r'\d{1,3},?\s+\d{1,3}', line):
            # Check if this might be a precinct name by looking at next meaningful line
            j = i + 1
            while j < len(lines) and not lines[j].strip():
                j += 1  # Skip empty lines
            
            if j < len(lines):
                next_line = lines[j].strip()
                # Precinct if followed by office or STATISTICS
                if (next_line.startswith("STATISTICS") or
                    next_line.startswith("Registered Voters") or
                    next_line.startswith("JUDGE") or
                    next_line.startswith("ATTORNEY") or
                    next_line.startswith("AUDITOR") or
                    next_line.startswith("STATE") or
                    next_line.startswith("SENATOR") or
                    next_line.startswith("REP") or
                    next_line.startswith("MAYOR") or
                    next_line.startswith("COUNCIL") or
                    next_line.startswith("SCHOOL") or
                    next_line.startswith("CONTROLLER") or
                    next_line.startswith("TAX") or
                    next_line.startswith("INSPECTOR") or
                    next_line.startswith("MAGISTERIAL") or
                    next_line.startswith("JUDGE OF") or
                    next_line.startswith("JUDICIAL") or
                    next_line.startswith("MEMBER OF") or
                    next_line.startswith("SUPERVISOR") or
                    next_line.startswith("CONSTABLE")):
                    # Make sure this doesn't look like vote numbers or column headers
                    if (not stripped.startswith("Vote For") and
                        not stripped.startswith("Election") and
                        not stripped.startswith("TOTAL") and
                        not stripped.startswith("Mail") and
                        not stripped.startswith("Provisional") and
                        not stripped.startswith("Votes") and
                        not stripped.startswith("Yes") and
                        not stripped.startswith("No") and
                        not stripped.startswith("Write-In") and
                        not stripped.startswith("Not Assigned") and
                        not stripped.startswith("Registered Voters") and
                        not stripped.startswith("Ballots Cast") and
                        not stripped.startswith("Voter Turnout")):
                        precinct = stripped
        
        # Parse STATISTICS section
        if stripped == "STATISTICS":
            i += 1
            while i < len(lines):
                line = lines[i].rstrip()
                stripped = line.strip()
                
                if not stripped or stripped.startswith("Vote For"):
                    break
                
                # Stop on office headers
                if (stripped.startswith("JUDGE") or 
                    stripped.startswith("ATTORNEY") or
                    stripped.startswith("AUDITOR") or
                    stripped.startswith("STATE") or
                    stripped.startswith("SENATOR") or
                    stripped.startswith("REP") or
                    stripped.startswith("MAYOR") or
                    stripped.startswith("COUNCIL") or
                    stripped.startswith("SCHOOL") or
                    stripped.startswith("CONTROLLER") or
                    stripped.startswith("TAX") or
                    stripped.startswith("INSPECTOR") or
                    stripped.startswith("MAGISTERIAL") or
                    stripped.startswith("MEMBER OF") or
                    stripped.startswith("SUPERVISOR") or
                    stripped.startswith("CONSTABLE")):
                    i -= 1  # Back up to process the office
                    break
                
                # Parse Registered Voters
                if stripped.startswith("Registered Voters - Total"):
                    parts = stripped.split()
                    total = parts[-1].replace(',', '')
                    results.append([county, precinct, "Registered Voters", "", "", "", total, "", "", ""])
                
                # Parse Ballots Cast - Total
                elif stripped.startswith("Ballots Cast - Total"):
                    parts = stripped.split()
                    # Format: "Ballots Cast - Total TOTAL ELECTION_DAY MAIL PROVISIONAL"
                    total = parts[-4].replace(',', '')
                    election_day = parts[-3].replace(',', '')
                    mail = parts[-2].replace(',', '')
                    provisional = parts[-1].replace(',', '')
                    results.append([county, precinct, "Ballots Cast", "", "", "", total, election_day, mail, provisional])
                
                # Parse Ballots Cast - Blank
                elif stripped.startswith("Ballots Cast - Blank"):
                    parts = stripped.split()
                    total = parts[-4].replace(',', '')
                    election_day = parts[-3].replace(',', '')
                    mail = parts[-2].replace(',', '')
                    provisional = parts[-1].replace(',', '')
                    results.append([county, precinct, "Ballots Cast Blank", "", "", "", total, election_day, mail, provisional])
                
                i += 1
            continue
        
        # Parse office headers
        # Retention election formats (must come before other JUDGE handlers)
        if stripped.endswith(" RETENTION"):
            # Handle "[JUDGE NAME] Retention" format
            judge_name = stripped.replace(" Retention", "").replace(" retention", "").strip()
            current_office = f"Judicial Retention Election - {judge_name}"
            current_district = ""
        elif stripped.startswith("SUPREME-"):
            # Handle "SUPREME-[JUDGE NAME]" shorthand format
            judge_name = stripped.replace("SUPREME-", "").replace("Supreme-", "").strip()
            current_office = f"Supreme Court of Pennsylvania Retention Election - {judge_name}"
            current_district = ""
        elif stripped.startswith("SUPERIOR-"):
            # Handle "SUPERIOR-[JUDGE NAME]" shorthand format
            judge_name = stripped.replace("SUPERIOR-", "").replace("Superior-", "").strip()
            current_office = f"Superior Court of Pennsylvania Retention Election - {judge_name}"
            current_district = ""
        elif stripped.startswith("COMMONWEALTH-"):
            # Handle "COMMONWEALTH-[JUDGE NAME]" shorthand format
            judge_name = stripped.replace("COMMONWEALTH-", "").replace("Commonwealth-", "").strip()
            current_office = f"Commonwealth Court of Pennsylvania Retention Election - {judge_name}"
            current_district = ""
        elif stripped.startswith("SUPREME COURT - RETAIN"):
            judge_name = stripped.replace("SUPREME COURT - RETAIN", "").replace("Supreme Court - Retain", "").strip()
            current_office = f"Supreme Court of Pennsylvania Retention Election - {judge_name}"
            current_district = ""
        elif stripped.startswith("SUPERIOR COURT - RETAIN"):
            judge_name = stripped.replace("SUPERIOR COURT - RETAIN", "").replace("Superior Court - Retain", "").strip()
            current_office = f"Superior Court of Pennsylvania Retention Election - {judge_name}"
            current_district = ""
        elif stripped.startswith("COMMONWEALTH COURT - RETAIN"):
            judge_name = stripped.replace("COMMONWEALTH COURT - RETAIN", "").replace("Commonwealth Court - Retain", "").strip()
            current_office = f"Commonwealth Court of Pennsylvania Retention Election - {judge_name}"
            current_district = ""
        elif stripped == "JUDGE OF THE SUPERIOR COURT":
            current_office = "Judge of the Superior Court"
            current_district = ""
        elif stripped == "JUDGE OF THE COMMONWEALTH COURT":
            current_office = "Judge of the Commonwealth Court"
            current_district = ""
        elif stripped.startswith("JUDGE OF THE COURT OF COMMON PLEAS"):
            # Preserve full office name including district and judge info
            current_office = stripped
            current_district = ""
        elif stripped == "ATTORNEY GENERAL":
            current_office = "Attorney General"
            current_district = ""
        elif stripped == "AUDITOR GENERAL":
            current_office = "Auditor General"
            current_district = ""
        elif stripped == "STATE TREASURER":
            current_office = "State Treasurer"
            current_district = ""
        elif stripped.startswith("REPRESENTATIVE IN CONGRESS"):
            current_office = "U.S. House"
            district_match = re.search(r'(\d+)(?:TH|ST|ND|RD)', stripped)
            current_district = district_match.group(1) if district_match else ""
        elif stripped.startswith("SENATOR") and "GENERAL ASSEMBLY" in stripped:
            current_office = "State Senate"
            district_match = re.search(r'(\d+)(?:TH|ST|ND|RD)', stripped)
            current_district = district_match.group(1) if district_match else ""
        elif stripped.startswith("REPRESENTATIVE IN THE GENERAL ASSEMBLY"):
            current_office = "State House"
            district_match = re.search(r'(\d+)(?:TH|ST|ND|RD)', stripped)
            current_district = district_match.group(1) if district_match else ""
        elif stripped.startswith("MAGISTERIAL DISTRICT JUDGE"):
            current_office = "Magisterial District Judge"
            # Extract district from the line
            current_district = stripped.replace("MAGISTERIAL DISTRICT JUDGE", "").strip()
        elif re.match(r'SCHOOL\s+DIRECTOR\s+T\d+V\d+', stripped):
            # Handle "SCHOOL DIRECTOR T#V# [DISTRICT]" format
            current_office = stripped
            current_district = ""
        elif " SCH DIR " in stripped:
            # Handle "[SCHOOL] SCH DIR [REGION]" format
            current_office = stripped
            current_district = ""
        elif stripped.startswith("SCHOOL DIRECTOR"):
            current_office = "School Director"
            # Extract district (e.g., "SCHOOL DIRECTOR (4-YR) ALIQUIPPA SCHOOL DISTRICT")
            match = re.search(r'\((.+?)\)\s+(.+)', stripped)
            if match:
                current_district = f"{match.group(2)} ({match.group(1)})"
            else:
                current_district = stripped.replace("SCHOOL DIRECTOR", "").strip()
        elif stripped.startswith("MEMBER OF COUNCIL"):
            current_office = "Council Member"
            current_district = stripped.replace("MEMBER OF COUNCIL", "").strip()
        elif " COUNCIL " in stripped or "COUNCIL PERSON" in stripped:
            # Capture full office name as-is from PDF
            current_office = stripped
            current_district = ""
        elif stripped.startswith("MAYOR") or "CITY MAYOR" in stripped:
            # Capture full office name as-is from PDF
            current_office = stripped
            current_district = ""
        elif stripped.startswith("AUDITOR") or " AUDITOR " in stripped or stripped.endswith(" AUDITOR"):
            # Capture full office name as-is from PDF
            current_office = stripped
            current_district = ""
        elif stripped.startswith("TAX COLLECTOR") or " TAX COLLECTOR " in stripped or stripped.endswith(" TAX COLLECTOR"):
            # Capture full office name as-is from PDF
            current_office = stripped
            current_district = ""
        elif stripped.startswith("CONTROLLER"):
            current_office = "Controller"
            current_district = stripped.replace("CONTROLLER", "").strip()
        elif stripped.startswith("JUDGE OF ELECTION"):
            current_office = "Judge of Election"
            current_district = stripped.replace("JUDGE OF ELECTION", "").strip()
        elif stripped.startswith("INSPECTOR OF ELECTIONS"):
            current_office = "Inspector of Elections"
            current_district = stripped.replace("INSPECTOR OF ELECTIONS", "").strip()
        elif stripped.startswith("JUDICIAL RETENTION"):
            current_office = stripped
            current_district = ""
        elif stripped.startswith("SUPERVISOR"):
            current_office = "Supervisor"
            current_district = stripped.replace("SUPERVISOR", "").strip()
        elif stripped.startswith("CONSTABLE") or " CONSTABLE " in stripped or stripped.endswith(" CONSTABLE"):
            # Capture full office name as-is from PDF
            current_office = stripped
            current_district = ""
        elif stripped.startswith("SUPERVISOR") or " SUPERVISOR " in stripped or stripped.endswith(" SUPERVISOR"):
            # Capture full office name as-is from PDF
            current_office = stripped
            current_district = ""
        elif stripped.startswith("COMMISSIONER") or " COMMISSIONER " in stripped:
            # Capture full office name as-is from PDF
            current_office = stripped
            current_district = ""
        elif stripped.startswith("CHARTER AMENDMENT"):
            # Ballot measures - preserve full text
            current_office = stripped
            current_district = ""
        elif stripped.startswith("REFERENDUM QUESTION"):
            # Ballot measures - preserve full text
            current_office = stripped
            current_district = ""
        elif stripped.endswith(" QUESTION") and not any(name in stripped for name in ["DONOHUE", "DOUGHERTY", "WECHT", "DUBOW", "WOJCIK"]):
            # Generic question format (not judge retention)
            current_office = stripped
            current_district = ""
        elif stripped == "RETAIN":
            # Study commission retention
            current_office = "Retain"
            current_district = ""
        
        # Parse candidate lines
        if current_office and stripped:
            # Skip header lines and office lines
            if (stripped.startswith("Vote For") or
                stripped.startswith("STATISTICS") or
                stripped.startswith("Election") or
                stripped.startswith("TOTAL") or
                stripped.startswith("Mail") or
                stripped.startswith("Provisional") or
                stripped.startswith("Day")):
                i += 1
                continue
            
            # Parse candidate/vote lines
            # Format: "PARTY CANDIDATE_NAME TOTAL ELECTION_DAY MAIL PROVISIONAL"
            # Party codes: DEM, REP, LIB, LBR, GRN, CST, FWD, ASP, DAR, DEM/REP, etc.
            
            # Check for Yes/No votes (retention elections)
            if stripped.startswith("Yes ") or stripped.startswith("No "):
                parts = stripped.split()
                if len(parts) >= 5:
                    candidate = parts[0]  # "Yes" or "No"
                    votes = [p.replace(',', '') for p in parts[-4:]]
                    results.append([county, precinct, current_office, current_district,
                                  "", candidate, votes[0], votes[1], votes[2], votes[3]])
            
            # Check for Write-In Totals (skip the nested Write-In: Scattered details)
            elif stripped.startswith("Write-In Totals"):
                parts = stripped.split()
                if len(parts) >= 5:
                    votes = [p.replace(',', '') for p in parts[-4:]]
                    results.append([county, precinct, current_office, current_district,
                                  "", "Write-ins", votes[0], votes[1], votes[2], votes[3]])
            
            # Skip nested Write-In: Scattered lines
            elif stripped.startswith("Write-In:"):
                pass
            
            # Check for Not Assigned
            elif stripped.startswith("Not Assigned"):
                parts = stripped.split()
                if len(parts) >= 1:
                    votes = [p.replace(',', '') for p in parts[-4:]]
                    if len(votes) == 4:
                        results.append([county, precinct, current_office, current_district,
                                      "", "Not Assigned", votes[0], votes[1], votes[2], votes[3]])
            
            # Check for Over Votes / Under Votes
            elif stripped.startswith("Over Votes"):
                parts = stripped.split()
                if len(parts) >= 1:
                    votes = [p.replace(',', '') for p in parts[-4:]]
                    if len(votes) == 4:
                        results.append([county, precinct, current_office, current_district,
                                      "", "Over Votes", votes[0], votes[1], votes[2], votes[3]])
            
            elif stripped.startswith("Under Votes"):
                parts = stripped.split()
                if len(parts) >= 1:
                    votes = [p.replace(',', '') for p in parts[-4:]]
                    if len(votes) == 4:
                        results.append([county, precinct, current_office, current_district,
                                      "", "Under Votes", votes[0], votes[1], votes[2], votes[3]])
            
            # Check for party-candidate lines
            else:
                # Known party codes to validate against
                known_parties = {'DEM', 'REP', 'LIB', 'LBR', 'GRN', 'CST', 'FWD', 'ASP', 'DAR', 
                                'DEM/REP', 'REP/DEM', 'DEM/LIB', 'REP/LIB', 'IND', 'WOR', 'GRE', 
                                'LIN', 'PIA', 'PIU'}
                
                # Pattern: "PARTY CANDIDATE_NAME VOTES ELECTION_DAY MAIL PROVISIONAL"
                party_match = re.match(r'^([A-Z]{2,4}(?:/[A-Z]{2,4})?)\s+(.+?)\s+(\d+(?:,\d+)?)\s+(?:\d+(?:\.\d+)?%)?\s*(\d+(?:,\d+)?)\s+(\d+(?:,\d+)?)\s+(\d+(?:,\d+)?)$', stripped)
                
                if party_match and party_match.group(1) in known_parties:
                    party = party_match.group(1)
                    candidate = party_match.group(2).strip()
                    total = party_match.group(3).replace(',', '')
                    election_day = party_match.group(4).replace(',', '')
                    mail = party_match.group(5).replace(',', '')
                    provisional = party_match.group(6).replace(',', '')
                    
                    results.append([county, precinct, current_office, current_district,
                                  party, candidate, total, election_day, mail, provisional])
                else:
                    # Try pattern without party, may include percentage
                    no_party_match = re.match(r'^(.+?)\s+(\d+(?:,\d+)?)\s+(?:\d+(?:\.\d+)?%)?\s+(\d+(?:,\d+)?)\s+(\d+(?:,\d+)?)\s+(\d+)$', stripped)
                    if no_party_match:
                        candidate = no_party_match.group(1).strip()
                        total = no_party_match.group(2).replace(',', '')
                        election_day = no_party_match.group(3).replace(',', '')
                        mail = no_party_match.group(4).replace(',', '')
                        provisional = no_party_match.group(5)
                        
                        # Skip "Not" candidate rows (duplicates of "Not Assigned")
                        if candidate.strip().upper() != "NOT":
                            results.append([county, precinct, current_office, current_district,
                                          "", candidate, total, election_day, mail, provisional])
        
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
        print("Usage: python electionware_text_precinct.py <pdf_path> <output_csv>")
        sys.exit(1)
    
    pdf_path = sys.argv[1]
    output_csv = sys.argv[2]
    
    parse_election_results(pdf_path, output_csv)
