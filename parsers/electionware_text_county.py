#!/usr/bin/env python3
"""
Parse election results PDF (county summary) by extracting text with pdftotext and producing CSV output.
"""

import csv
import re
import sys
import subprocess
import tempfile
import os


def parse_election_results(pdf_path, output_csv, county_name=None):
    """Parse the election results PDF by extracting text with pdftotext and writing to CSV.
    
    Args:
        pdf_path: Path to the PDF file
        output_csv: Path to the output CSV file
        county_name: Optional county name (titlecase). If provided, skips auto-detection.
    """
    
    # Create a temporary file for the extracted text
    with tempfile.NamedTemporaryFile(mode='w+', suffix='.txt', delete=False) as temp_file:
        temp_text_path = temp_file.name
    
    try:
        # Extract text from PDF using pdftotext with -layout option
        subprocess.run(['pdftotext', '-layout', pdf_path, temp_text_path], 
                      check=True, capture_output=True, text=True)
        
        # Parse the extracted text
        _parse_text_file(temp_text_path, output_csv, county_name)
    
    finally:
        # Clean up temporary file
        if os.path.exists(temp_text_path):
            os.remove(temp_text_path)


def _parse_text_file(text_path, output_csv, county_name=None):
    
    results = []
    county = county_name  # Use provided county or extract from PDF header
    current_office = None
    current_district = None
    office_count = {}  # Track occurrences of each office name for deduplication
    
    with open(text_path, 'r', encoding='utf-8') as f:
        lines = f.readlines()
    
    # Preprocess: join lines that were wrapped in the middle of words or numbers
    # Only join short trailing fragments that appear to be part of the previous line
    processed_lines = []
    i = 0
    while i < len(lines):
        line = lines[i].rstrip()
        # Check if next line is a small continuation (wrapped word like "CENTRE\nCOUNTY" or number fragment like "7,84\n8")
        while (i + 1 < len(lines) and 
               lines[i + 1].strip() and
               not lines[i + 1][0].isspace() and
               len(lines[i + 1].rstrip()) < 20 and  # Small line, likely wrapped
               (re.match(r'^[A-Z]+\s*$', lines[i + 1].strip()) or  # All caps word
                re.match(r'^\d+\s*$', lines[i + 1].strip()))):  # Single digit(s) - likely wrapped number
            # Join the lines
            line = line + lines[i + 1].rstrip()
            i += 1
        # Also check for continuation lines that start with whitespace and contain only numbers/commas
        # These are wrapped vote numbers like " 1,319        4"
        while (i + 1 < len(lines) and 
               lines[i + 1].strip() and
               lines[i + 1][0].isspace() and
               re.match(r'^[\d\s,]+$', lines[i + 1].strip())):  # Only digits, commas, and spaces
            # Join with a space separator to maintain column alignment
            line = line + ' ' + lines[i + 1].strip()
            i += 1
        processed_lines.append(line)
        i += 1
    
    lines = processed_lines
    
    i = 0
    while i < len(lines):
        line = lines[i].rstrip()
        stripped = line.strip()
        stripped_upper = stripped.upper()  # For case-insensitive comparisons
        
        # Extract county from header (appears after date or standalone)
        if county is None:
            # First try: date followed by county and "County" keyword on same line with flexible spacing
            date_county_match = re.search(r'(?:November|October|January|February|March|April|May|June|July|August|September|December)\s+\d{1,2},\s+\d{4}[\s\S]{0,100}?([A-Z][A-Za-z]+)(?:\s*(?:County|COUNTY)|(?:County|COUNTY))?', line, re.IGNORECASE)
            if date_county_match:
                potential_county = date_county_match.group(1).strip()
                # Verify it looks like a county name
                if re.match(r'^[A-Z][a-z]+$', potential_county) and len(potential_county) > 2:
                    county = potential_county.title()
            # Second try: "COUNTY OF" pattern
            if county is None and re.search(r'COUNTY\s+OF', stripped_upper):
                county_of_match = re.search(r'COUNTY\s+OF\s+(\w+)', stripped_upper)
                if county_of_match:
                    # Extract from original line to preserve case
                    county_name = line[county_of_match.start(1):county_of_match.end(1)]
                    county = county_name.strip().title()
            # Third try: standalone county name on its own line (common PA Electionware format)
            if county is None and len(stripped.split()) == 1 and re.match(r'^[A-Z][a-z]+$', stripped) and len(stripped) > 3:
                # Could be a county name, check if it's followed by date context
                potential_county = stripped
                # Look ahead to see if next line has election year
                if i + 1 < len(lines) and ('202' in lines[i + 1] or '2025' in lines[i + 1] or 'ELECTION' in lines[i + 1].upper()):
                    county = potential_county.title()
        
        # Parse STATISTICS section (county-level only) - case insensitive
        # "Statistics" may appear on the same line as other text
        if "STATISTICS" in stripped_upper or stripped_upper == "STATISTICS":
            i += 1
            while i < len(lines):
                line = lines[i].rstrip()
                stripped = line.strip()
                
                # Stop on office headers or blank lines followed by office
                if not stripped:
                    # Check if next non-empty line is an office
                    j = i + 1
                    while j < len(lines) and not lines[j].strip():
                        j += 1
                    if j < len(lines):
                        next_line = lines[j].strip().upper()
                        if (next_line.startswith("JUDGE") or 
                            next_line.startswith("ATTORNEY") or
                            next_line.startswith("DISTRICT ATTORNEY") or
                            next_line.startswith("SHERIFF") or
                            next_line.startswith("COUNTY") or
                            next_line.startswith("SUPREME") or
                            next_line.startswith("SUPERIOR") or
                            next_line.startswith("COMMONWEALTH")):
                            break
                    i += 1
                    continue
                
                # Stop on office headers
                stripped_upper = stripped.upper()
                if (stripped_upper.startswith("JUDGE") or 
                    stripped_upper.startswith("ATTORNEY") or
                    stripped_upper.startswith("DISTRICT ATTORNEY") or
                    stripped_upper.startswith("SHERIFF") or
                    stripped_upper.startswith("AUDITOR") or
                    stripped_upper.startswith("STATE") or
                    stripped_upper.startswith("SENATOR") or
                    stripped_upper.startswith("REP") or
                    stripped_upper.startswith("MAYOR") or
                    stripped_upper.startswith("COUNCIL") or
                    stripped_upper.startswith("SCHOOL") or
                    stripped_upper.startswith("SCH") or
                    " SCH DIR " in stripped_upper or
                    stripped_upper.startswith("CONTROLLER") or
                    stripped_upper.startswith("TAX") or
                    stripped_upper.startswith("INSPECTOR") or
                    stripped_upper.startswith("MAGISTERIAL") or
                    stripped_upper.startswith("MEMBER OF") or
                    stripped_upper.startswith("SUPERVISOR") or
                    stripped_upper.startswith("CONSTABLE") or
                    stripped_upper.startswith("COUNTY") or
                    stripped_upper.startswith("SUPREME") or
                    stripped_upper.startswith("SUPERIOR") or
                    stripped_upper.startswith("COMMONWEALTH") or
                    stripped_upper.endswith(" RETENTION")):
                    i -= 1  # Back up to process the office
                    break
                
                # Parse Registered Voters
                if "Registered Voters - Total" in stripped or "Registered Voters - Total" in stripped_upper:
                    # Extract just the number (format: "Registered Voters - Total    81,400")
                    parts = stripped.split()
                    # Find the last number-like part
                    for part in reversed(parts):
                        if part.replace(',', '').isdigit():
                            total = part.replace(',', '')
                            results.append([county, "Registered Voters", "", "", "", total, "", "", ""])
                            break
                
                # Parse Ballots Cast - Total
                elif "Ballots Cast - Total" in stripped:
                    # Extract numbers from the line (format: "Ballots Cast - Total   31,090   24,152   6,865   73")
                    parts = stripped.split()
                    # Get all numeric parts
                    numbers = [p.replace(',', '') for p in parts if p.replace(',', '').isdigit()]
                    if len(numbers) >= 4:
                        results.append([county, "Ballots Cast", "", "", "", numbers[0], numbers[1], numbers[2], numbers[3]])
                    elif len(numbers) == 1:
                        # Only total provided
                        results.append([county, "Ballots Cast", "", "", "", numbers[0], "", "", ""])
                
                # Parse Ballots Cast - Blank
                elif "Ballots Cast - Blank" in stripped:
                    parts = stripped.split()
                    numbers = [p.replace(',', '') for p in parts if p.replace(',', '').isdigit()]
                    if len(numbers) >= 4:
                        results.append([county, "Ballots Cast Blank", "", "", "", numbers[0], numbers[1], numbers[2], numbers[3]])
                
                i += 1
            continue
        
        # Parse office headers (case-insensitive comparison)
        if stripped_upper.startswith("JUSTICE OF SUPREME COURT "):
            judge_name = stripped[len("JUSTICE OF SUPREME COURT "):].strip()
            current_office = f"Supreme Court of Pennsylvania Retention Election - {judge_name.title()}" if judge_name else "Supreme Court of Pennsylvania Retention Election"
            current_district = ""
        elif stripped_upper.startswith("JUDGE OF THE SUPERIOR COURT "):
            judge_name = stripped[len("JUDGE OF THE SUPERIOR COURT "):].strip()
            current_office = f"Superior Court of Pennsylvania Retention Election - {judge_name.title()}" if judge_name else "Superior Court of Pennsylvania Retention Election"
            current_district = ""
        elif stripped_upper.startswith("JUDGE OF THE COMMONWEALTH COURT "):
            judge_name = stripped[len("JUDGE OF THE COMMONWEALTH COURT "):].strip()
            current_office = f"Commonwealth Court of Pennsylvania Retention Election - {judge_name.title()}" if judge_name else "Commonwealth Court of Pennsylvania Retention Election"
            current_district = ""
        elif stripped_upper == "JUDGE OF THE SUPERIOR COURT":
            current_office = "Judge of the Superior Court"
            current_district = ""
        elif stripped_upper == "JUDGE OF THE COMMONWEALTH COURT":
            current_office = "Judge of the Commonwealth Court"
            current_district = ""
        elif stripped_upper == "JUDGE OF THE COURT OF COMMON PLEAS" or stripped_upper.startswith("JUDGE OF THE COURT OF COMMON PLEAS") or re.sub(r'\s+', ' ', stripped_upper) == "JUDGE OF THE COURT OF COMMON PLEAS":
            current_office = "Judge of the Court of Common Pleas"
            current_district = ""
        elif stripped_upper == "ATTORNEY GENERAL":
            current_office = "Attorney General"
            current_district = ""
        elif stripped_upper == "AUDITOR GENERAL":
            current_office = "Auditor General"
            current_district = ""
        elif stripped_upper == "STATE TREASURER":
            current_office = "State Treasurer"
            current_district = ""
        elif stripped_upper == "UNITED STATES SENATOR":
            current_office = "U.S. Senate"
            current_district = ""
        elif stripped_upper == "COUNTY TREASURER":
            current_office = "County Treasurer"
            current_district = ""
        elif stripped_upper == "COUNTY CORONER":
            current_office = "County Coroner"
            current_district = ""
        elif stripped_upper == "COUNTY CONTROLLER":
            current_office = "County Controller"
            current_district = ""
        elif stripped_upper == "DISTRICT ATTORNEY":
            current_office = "District Attorney"
            current_district = ""
        elif stripped_upper == "SHERIFF":
            current_office = "Sheriff"
            current_district = ""
        elif stripped_upper == "JURY COMMISSIONER":
            current_office = "Jury Commissioner"
            current_district = ""
        elif stripped_upper.startswith("DISTRICT JUDGE MAGISTERIAL DISTRICT"):
            # Extract district from "DISTRICT JUDGE MAGISTERIAL DISTRICT 49-1-01"
            district = stripped.replace("DISTRICT JUDGE MAGISTERIAL DISTRICT", "").replace("District Judge Magisterial District", "").strip()
            current_office = "District Judge"
            current_district = district
        elif stripped_upper.startswith("REPRESENTATIVE IN CONGRESS"):
            current_office = "U.S. House"
            district_match = re.search(r'(\d+)(?:TH|ST|ND|RD)', stripped, re.IGNORECASE)
            current_district = district_match.group(1) if district_match else ""
        elif stripped_upper.startswith("SENATOR") and "GENERAL ASSEMBLY" in stripped_upper:
            current_office = "State Senate"
            district_match = re.search(r'(\d+)(?:TH|ST|ND|RD)', stripped, re.IGNORECASE)
            current_district = district_match.group(1) if district_match else ""
        elif stripped_upper.startswith("REPRESENTATIVE IN THE GENERAL ASSEMBLY"):
            current_office = "State House"
            district_match = re.search(r'(\d+)(?:TH|ST|ND|RD)', stripped, re.IGNORECASE)
            current_district = district_match.group(1) if district_match else ""
        elif stripped_upper.startswith("PRESIDENTIAL ELECTORS"):
            current_office = "President"
            current_district = ""
        elif stripped_upper.startswith("SUPREME-"):
            # Handle "SUPREME-[JUDGE NAME]" shorthand format (e.g., "SUPREME-DONOHUE")
            judge_name = stripped.replace("SUPREME-", "").replace("Supreme-", "").strip()
            current_office = f"Supreme Court of Pennsylvania Retention Election - {judge_name}" if judge_name else "Supreme Court of Pennsylvania Retention Election"
            current_district = ""
        elif stripped_upper.startswith("SUPERIOR-"):
            # Handle "SUPERIOR-[JUDGE NAME]" shorthand format (e.g., "SUPERIOR-DUBOW")
            judge_name = stripped.replace("SUPERIOR-", "").replace("Superior-", "").strip()
            current_office = f"Superior Court of Pennsylvania Retention Election - {judge_name}" if judge_name else "Superior Court of Pennsylvania Retention Election"
            current_district = ""
        elif stripped_upper.startswith("COMMONWEALTH-"):
            # Handle "COMMONWEALTH-[JUDGE NAME]" shorthand format (e.g., "COMMONWEALTH-WOJCIK")
            judge_name = stripped.replace("COMMONWEALTH-", "").replace("Commonwealth-", "").strip()
            current_office = f"Commonwealth Court of Pennsylvania Retention Election - {judge_name}" if judge_name else "Commonwealth Court of Pennsylvania Retention Election"
            current_district = ""
        elif stripped_upper.startswith("SUPREME COURT - RETAIN"):
            # Handle "SUPREME COURT - RETAIN [JUDGE NAME]" format (with dash)
            judge_name = stripped.replace("SUPREME COURT - RETAIN", "").replace("Supreme Court - Retain", "").strip()
            current_office = f"Supreme Court of Pennsylvania Retention Election - {judge_name}" if judge_name else "Supreme Court of Pennsylvania Retention Election"
            current_district = ""
        elif stripped_upper.startswith("SUPERIOR COURT - RETAIN"):
            # Handle "SUPERIOR COURT - RETAIN [JUDGE NAME]" format (with dash)
            judge_name = stripped.replace("SUPERIOR COURT - RETAIN", "").replace("Superior Court - Retain", "").strip()
            current_office = f"Superior Court of Pennsylvania Retention Election - {judge_name}" if judge_name else "Superior Court of Pennsylvania Retention Election"
            current_district = ""
        elif stripped_upper.startswith("COMMONWEALTH COURT - RETAIN"):
            # Handle "COMMONWEALTH COURT - RETAIN [JUDGE NAME]" format (with dash)
            judge_name = stripped.replace("COMMONWEALTH COURT - RETAIN", "").replace("Commonwealth Court - Retain", "").strip()
            current_office = f"Commonwealth Court of Pennsylvania Retention Election - {judge_name}" if judge_name else "Commonwealth Court of Pennsylvania Retention Election"
            current_district = ""
        elif stripped_upper.startswith("SUPREME COURT RETAIN"):
            # Handle "SUPREME COURT RETAIN [JUDGE NAME]" format (e.g., "Supreme Court Retain Donohue")
            judge_name = stripped.replace("SUPREME COURT RETAIN", "").replace("Supreme Court Retain", "").strip()
            current_office = f"Supreme Court of Pennsylvania Retention Election - {judge_name}" if judge_name else "Supreme Court of Pennsylvania Retention Election"
            current_district = ""
        elif stripped_upper.startswith("SUPERIOR COURT RETAIN"):
            # Handle "SUPERIOR COURT RETAIN [JUDGE NAME]" format (e.g., "Superior Court Retain Dubow")
            judge_name = stripped.replace("SUPERIOR COURT RETAIN", "").replace("Superior Court Retain", "").strip()
            current_office = f"Superior Court of Pennsylvania Retention Election - {judge_name}" if judge_name else "Superior Court of Pennsylvania Retention Election"
            current_district = ""
        elif stripped_upper.startswith("COMMONWEALTH COURT RETAIN"):
            # Handle "COMMONWEALTH COURT RETAIN [JUDGE NAME]" format (e.g., "Commonwealth Court Retain Wojcik")
            judge_name = stripped.replace("COMMONWEALTH COURT RETAIN", "").replace("Commonwealth Court Retain", "").strip()
            current_office = f"Commonwealth Court of Pennsylvania Retention Election - {judge_name}" if judge_name else "Commonwealth Court of Pennsylvania Retention Election"
            current_district = ""
        elif stripped_upper.startswith("SUPREME COURT JUSTICE"):
            # Handle "SUPREME COURT JUSTICE [NAME]" format
            judge_name = stripped.replace("SUPREME COURT JUSTICE", "").replace("Supreme Court Justice", "").strip()
            current_office = f"Supreme Court of Pennsylvania Retention Election - {judge_name}" if judge_name else "Supreme Court of Pennsylvania Retention Election"
            current_district = ""
        elif stripped_upper.startswith("SUPERIOR COURT JUDGE"):
            # Handle "SUPERIOR COURT JUDGE [NAME]" format
            judge_name = stripped.replace("SUPERIOR COURT JUDGE", "").replace("Superior Court Judge", "").strip()
            current_office = f"Superior Court of Pennsylvania Retention Election - {judge_name}" if judge_name else "Superior Court of Pennsylvania Retention Election"
            current_district = ""
        elif stripped_upper.startswith("COMMONWEALTH COURT JUDGE"):
            # Handle "COMMONWEALTH COURT JUDGE [NAME]" format
            judge_name = stripped.replace("COMMONWEALTH COURT JUDGE", "").replace("Commonwealth Court Judge", "").strip()
            current_office = f"Commonwealth Court of Pennsylvania Retention Election - {judge_name}" if judge_name else "Commonwealth Court of Pennsylvania Retention Election"
            current_district = ""
        elif stripped_upper.endswith(" RETENTION"):
            # Handle "[JUDGE NAME] Retention" format (e.g., "CHRISTINE DONOHUE Retention")
            # These are court retention elections - we need to determine which court
            judge_name = stripped.replace(" Retention", "").replace(" retention", "").strip()
            # Default to Supreme Court unless other indicators are found
            # This is a generic handler - specific court mapping would require additional info
            current_office = f"Judicial Retention Election - {judge_name}"
            current_district = ""
        elif stripped_upper.startswith("SUPREME COURT JUDICIAL RETENTION QUESTION"):
            # Extract judge name from "Supreme Court Judicial Retention Question - JUDGE NAME"
            judge_name = stripped.replace("SUPREME COURT JUDICIAL RETENTION QUESTION", "").replace("Supreme Court Judicial Retention Question", "").strip()
            if judge_name.startswith("-"):
                judge_name = judge_name[1:].strip()
            current_office = f"Supreme Court of Pennsylvania Retention Election - {judge_name}" if judge_name else "Supreme Court of Pennsylvania Retention Election"
            current_district = ""
        elif stripped_upper.startswith("SUPREME COURT RETENTION"):
            # Extract judge name from "SUPREME COURT RETENTION - JUDGE NAME"
            judge_name = stripped.replace("SUPREME COURT RETENTION", "").replace("Supreme Court Retention", "").strip()
            if judge_name.startswith("-"):
                judge_name = judge_name[1:].strip()
            current_office = f"Supreme Court of Pennsylvania Retention Election - {judge_name}" if judge_name else "Supreme Court of Pennsylvania Retention Election"
            current_district = ""
        elif stripped_upper.startswith("SUPREME COURT"):
            base_office = "Supreme Court of Pennsylvania Retention Election Question"
            # Track duplicates and add a number if this is a repeat
            if base_office in office_count:
                office_count[base_office] += 1
                current_office = f"{base_office} #{office_count[base_office]}"
            else:
                office_count[base_office] = 1
                current_office = base_office
            current_district = ""
        elif stripped_upper.startswith("SUPERIOR COURT JUDICIAL RETENTION QUESTION"):
            # Extract judge name from "Superior Court Judicial Retention Question - JUDGE NAME"
            judge_name = stripped.replace("SUPERIOR COURT JUDICIAL RETENTION QUESTION", "").replace("Superior Court Judicial Retention Question", "").strip()
            if judge_name.startswith("-"):
                judge_name = judge_name[1:].strip()
            current_office = f"Superior Court of Pennsylvania Retention Election - {judge_name}" if judge_name else "Superior Court of Pennsylvania Retention Election"
            current_district = ""
        elif stripped_upper.startswith("SUPERIOR COURT RETENTION") and "QUESTION" in stripped_upper:
            # For "SUPERIOR COURT RETENTION QUESTION" format, use full text
            current_office = stripped.strip()
            current_district = ""
        elif stripped_upper.startswith("SUPERIOR COURT RETENTION"):
            # Extract judge name from "SUPERIOR COURT RETENTION - JUDGE NAME"
            judge_name = stripped.replace("SUPERIOR COURT RETENTION", "").replace("Superior Court Retention", "").strip()
            if judge_name.startswith("-"):
                judge_name = judge_name[1:].strip()
            current_office = f"Superior Court of Pennsylvania Retention Election - {judge_name}" if judge_name else "Superior Court of Pennsylvania Retention Election"
            current_district = ""
        elif stripped_upper.startswith("COMMONWEALTH COURT JUDICIAL RETENTION QUESTION"):
            # Extract judge name from "Commonwealth Court Judicial Retention Question - JUDGE NAME"
            judge_name = stripped.replace("COMMONWEALTH COURT JUDICIAL RETENTION QUESTION", "").replace("Commonwealth Court Judicial Retention Question", "").strip()
            if judge_name.startswith("-"):
                judge_name = judge_name[1:].strip()
            current_office = f"Commonwealth Court of Pennsylvania Retention Election - {judge_name}" if judge_name else "Commonwealth Court of Pennsylvania Retention Election"
            current_district = ""
        elif stripped_upper.startswith("COMMONWEALTH COURT RETENTION") and "QUESTION" in stripped_upper:
            # For "COMMONWEALTH COURT RETENTION QUESTION" format, use full text
            current_office = stripped.strip()
            current_district = ""
        elif stripped_upper.startswith("COMMONWEALTH COURT RETENTION"):
            # Extract judge name from "COMMONWEALTH COURT RETENTION - JUDGE NAME"
            judge_name = stripped.replace("COMMONWEALTH COURT RETENTION", "").replace("Commonwealth Court Retention", "").strip()
            if judge_name.startswith("-"):
                judge_name = judge_name[1:].strip()
            current_office = f"Commonwealth Court of Pennsylvania Retention Election - {judge_name}" if judge_name else "Commonwealth Court of Pennsylvania Retention Election"
            current_district = ""
        elif stripped_upper.startswith("COURT OF COMMON PLEAS JUDICIAL RETENTION QUESTION"):
            # Extract judge name from "Court of Common Pleas Judicial Retention Question - JUDGE NAME"
            judge_name = stripped.replace("COURT OF COMMON PLEAS JUDICIAL RETENTION QUESTION", "").replace("Court of Common Pleas Judicial Retention Question", "").strip()
            # Handle leading special characters like "?" or "-"
            judge_name = re.sub(r'^[\?\-\s]+', '', judge_name).strip()
            current_office = f"Court of Common Pleas Retention Election - {judge_name}" if judge_name else "Court of Common Pleas Retention Election"
            current_district = ""
        elif stripped_upper.startswith("COURT OF COMMON PLEAS RETENTION"):
            # Extract judge name from "COURT OF COMMON PLEAS RETENTION? JUDGE NAME" or similar
            judge_name = stripped.replace("COURT OF COMMON PLEAS RETENTION", "").replace("Court of Common Pleas Retention", "").strip()
            # Handle leading special characters like "?" or "-"
            judge_name = re.sub(r'^[\?\-\s]+', '', judge_name).strip()
            current_office = f"Court of Common Pleas Retention Election - {judge_name}" if judge_name else "Court of Common Pleas Retention Election"
            current_district = ""
        elif stripped_upper.startswith("RETENTION ELECTION"):
            # Handle simple "Retention Election [Judge Name]" format
            current_office = stripped.strip()
            current_district = ""
        elif stripped_upper.startswith("RETAIN"):
            # Handle "RETAIN [JUDGE NAME]" format (e.g., "RETAIN ALICE BECK DUBOW")
            # This is typically for state Supreme/Superior Court retention
            judge_name = stripped.replace("RETAIN", "").replace("Retain", "").strip()
            current_office = f"Retention Election - {judge_name}" if judge_name else "Retention Election"
            current_district = ""
        elif stripped_upper.startswith("COURT OF COMMON PLEAS JUDICIAL RETENTION QUESTION") or stripped_upper.startswith("COURT OF COMMON PLEASE JUDICIAL RETENTION QUESTION"):
            # Extract judge name from "Court of Common Pleas Judicial Retention Question - JUDGE NAME"
            # Also handle typo "Court of Common Please Judicial Retention Question"
            judge_name = stripped.replace("Court of Common Pleas Judicial Retention Question", "").replace("Court of Common Please Judicial Retention Question", "").strip()
            # Handle leading special characters like "?" or "-"
            judge_name = re.sub(r'^[\?\-\s]+', '', judge_name).strip()
            current_office = f"Court of Common Pleas Retention Election - {judge_name}" if judge_name else "Court of Common Pleas Retention Election"
            current_district = ""
        elif stripped_upper.startswith("RET SUPREME COURT") or stripped_upper.startswith("RET SUPERIOR COURT") or stripped_upper.startswith("RET COMMONWEALTH COURT"):
            # Handle shortened retention format: "RET SUPREME COURT - NAME" etc.
            current_office = stripped.strip()
            current_district = ""
        elif stripped_upper.startswith("RETENTION QUESTION"):
            # Handle "RETENTION QUESTION [JUDGE NAME]" format (e.g., "RETENTION QUESTION DONOHUE")
            judge_name = stripped.replace("RETENTION QUESTION", "").replace("Retention Question", "").strip()
            current_office = f"Retention Question - {judge_name}" if judge_name else "Retention Question"
            current_district = ""
        elif stripped_upper.startswith("STATE SUPREME COURT RETENTION QUESTION"):
            # Use entire text as office name
            current_office = stripped.strip()
            current_district = ""
        elif stripped_upper.startswith("STATE SUPERIOR COURT RETENTION QUESTION"):
            # Use entire text as office name
            current_office = stripped.strip()
            current_district = ""
        elif stripped_upper.startswith("SUPERIOR COURT RETENTION QUESTION"):
            # Use entire text as office name
            current_office = stripped.strip()
            current_district = ""
        elif stripped_upper.startswith("SUPERIOR COURT OF PENNSYLVANIA RETENTION QUESTION"):
            # Use entire text as office name
            current_office = stripped.strip()
            current_district = ""
        elif stripped_upper.startswith("STATE COMMONWEALTH COURT RETENTION QUESTION"):
            # Use entire text as office name
            current_office = stripped.strip()
            current_district = ""
        elif stripped_upper.startswith("COMMONWEALTH COURT RETENTION QUESTION"):
            # Use entire text as office name
            current_office = stripped.strip()
            current_district = ""
        elif stripped_upper.startswith("COURT OF THE COMMON PLEAS RETENTION"):
            # Handle "COURT OF THE COMMON PLEAS RETENTION ELECTION QUESTION" format
            current_office = stripped.strip()
            current_district = ""
        elif stripped_upper.startswith("COMMON PLEAS COURT RETENTION"):
            # Extract judge name from "COMMON PLEAS COURT RETENTION - JUDGE NAME"
            judge_name = stripped.replace("COMMON PLEAS COURT RETENTION", "").replace("Common Pleas Court Retention", "").strip()
            # Handle leading special characters like "?" or "-"
            judge_name = re.sub(r'^[\?\-\s]+', '', judge_name).strip()
            current_office = f"Court of Common Pleas Retention Election - {judge_name}" if judge_name else "Court of Common Pleas Retention Election"
            current_district = ""
        elif stripped_upper.endswith(" QUESTION") and any(name in stripped_upper for name in ["DONOHUE", "DOUGHERTY", "WECHT", "DUBOW", "WOJCIK"]):
            # Handle "[JUDGE NAME] Question" retention election format (e.g., "Donohue Question", "Wecht Question")
            # Map judge names to court types
            if "DONOHUE" in stripped_upper:
                current_office = "Supreme Court of Pennsylvania Retention Election - Christine Donohue"
            elif "DOUGHERTY" in stripped_upper:
                current_office = "Supreme Court of Pennsylvania Retention Election - Kevin M. Dougherty"
            elif "WECHT" in stripped_upper:
                current_office = "Supreme Court of Pennsylvania Retention Election - David Wecht"
            elif "DUBOW" in stripped_upper:
                current_office = "Superior Court of Pennsylvania Retention Election - Alice Beck Dubow"
            elif "WOJCIK" in stripped_upper:
                current_office = "Commonwealth Court of Pennsylvania Retention Election - Michael H. Wojcik"
            else:
                current_office = stripped.strip()
            current_district = ""
        elif stripped_upper.startswith("REFERENDUM QUESTION"):
            # Handle "Referendum Question" ballot measure
            current_office = stripped.strip()
            current_district = ""
        elif stripped_upper.startswith("JUDICIAL RETENTION QUESTION"):
            # Handle "Judicial Retention Question - Name"
            current_office = stripped.strip()
            current_district = ""
        elif stripped_upper.startswith("BRADFORD CITY STUDY COMMISSION QUESTION"):
            # Handle "Bradford City Study Commission Question" ballot measure
            current_office = "Bradford City Study Commission Question"
            current_district = ""
        elif stripped_upper.startswith("BRADFORD CITY STUDY COMMISSION"):
            # Handle "Bradford City Study Commission" election
            current_office = "Bradford City Study Commission"
            current_district = ""
        elif stripped_upper.startswith("CHARTER AMENDMENT"):
            # Handle "CHARTER AMENDMENT [NUMBER] [LOCATION]" ballot measures
            # e.g., "CHARTER AMENDMENT 1 FARRELL", "CHARTER AMENDMENT 2 TOWN OF GREENVILLE"
            current_office = stripped.strip()
            current_district = ""
        elif stripped_upper.startswith("PROTHONOTARY"):
            current_office = "Prothonotary/Clerk of Courts"
            current_district = ""
        elif "REGISTER AND RECORDER" in stripped_upper or "REGISTER & RECORDER" in stripped_upper or "REGISTER & RECORDER" in stripped:
            # Handle "REGISTER AND RECORDER" or "REGISTER & RECORDER" format (various counties)
            current_office = stripped
            current_district = ""
        elif "REGISTER OF WILLS" in stripped_upper and "RECORDER OF DEEDS" in stripped_upper and "CLERK OF ORPHANS" in stripped_upper:
            # Handle combined office: "REGISTER OF WILLS, RECORDER OF DEEDS, CLERK OF ORPHANS' COURT"
            current_office = stripped
            current_district = ""
        elif stripped_upper.startswith("RECORDER OF DEEDS"):
            current_office = "Recorder of Deeds, Register of Wills, and Clerk of the Orphans' Court"
            current_district = ""
        elif stripped_upper.startswith("REGISTER OF WILLS"):
            current_office = "Recorder of Deeds, Register of Wills, and Clerk of the Orphans' Court"
            current_district = ""
        elif stripped_upper.startswith("MAGISTERIAL DISTRICT JUDGE"):
            current_office = "Magisterial District Judge"
            # Extract district from the line
            current_district = stripped.replace("Magisterial District Judge", "").replace("MAGISTERIAL DISTRICT JUDGE", "").strip()
        elif "SCHOOL BOARD DIRECTOR" in stripped_upper:
            # Handle "REGION X SCHOOL BOARD DIRECTOR" or "REGION X SCHOOL BOARD DIRECTOR NYR" format
            current_office = stripped
            current_district = ""
        elif " SCH DIR " in stripped_upper:
            # Handle "[SCHOOL NAME] SCH DIR [REGION/TYPE]" format
            # Keep the full office name as-is from the PDF
            current_office = stripped.strip()
            current_district = ""
        elif re.match(r'SCHOOL\s+DIRECTOR\s+T\d+V\d+', stripped_upper):
            # Handle "School Director T4V4 [District Name]" format
            # Keep the full office name as-is
            current_office = stripped.strip()
            current_district = ""
        elif stripped_upper.startswith("SCH DIR"):
            # Extract full location/district info and include in office name
            school_line = stripped.replace("School Director", "").replace("SCHOOL DIRECTOR", "").replace("School Directors", "").replace("SCHOOL DIRECTORS", "").strip()
            # Remove leading artifact "s " if present
            school_line = re.sub(r'^s\s+', '', school_line).strip()
            
            # Handle cases like "LMSD Region 1 Line Mountain School District Region 1"
            # Extract the full district name if present after the region specification
            match = re.match(r'^([A-Z]+)\s+(Region\s+\d+)\s+(.+)$', school_line)
            if match:
                abbr = match.group(1)
                region = match.group(2)
                rest = match.group(3).strip()
                # Remove trailing duplicate region if it exists (e.g., "Line Mountain School District Region 1" -> keep it)
                school_line = f"{rest} {region}".strip()
                # De-duplicate if the region appears twice (e.g., "Line Mountain School District Region 1 Region 1")
                school_line = re.sub(r'\s+Region\s+\d+\s+Region\s+\d+', lambda m: ' ' + re.search(r'Region\s+\d+', m.group(0)).group(0), school_line)
            else:
                # Handle cases like "MASD Region 1" with no full name - expand the abbreviation
                # Map common PA school district abbreviations
                abbr_map = {
                    'LMSD': 'Line Mountain School District',
                    'MASD': 'Milton Area School District',
                    'MCASD': 'Mifflin County Area School District',
                    'SASD': 'Shamokin Area School District',
                    'SCSD': 'Southern Columbia School District',
                    'WRSD': 'Warrior Run School District',
                }
                for abbr, full_name in abbr_map.items():
                    if school_line.upper().startswith(abbr):
                        school_line = school_line.replace(abbr, full_name, 1)
                        break
            
            # Remove duplicate district names (e.g., "Danville Area School District Danville Area School District")
            # Split by space and remove duplicate sequences
            parts = school_line.split()
            seen = []
            deduped = []
            for part in parts:
                # Check if this part starts a duplicate sequence
                if len(deduped) > 0:
                    # Build a potential duplicate phrase to check
                    test_phrase = ' '.join(parts[len(deduped)-1:])
                    # If we find the same multi-word phrase appearing again, skip it
                    if test_phrase.startswith(part):
                        check_idx = len(deduped)
                        test_match = ' '.join(parts[check_idx:check_idx+len(deduped)])
                        if test_match == ' '.join(deduped):
                            continue
                deduped.append(part)
            school_line = ' '.join(deduped)
            
            # Simpler deduplication: remove exact duplicate phrases
            # Pattern: "Word1 Word2 Word3 Word1 Word2 Word3"
            school_line = re.sub(r'^(.+?)\s+\1$', r'\1', school_line)
            
            current_office = f"School Director {school_line}" if school_line else "School Director"
            current_district = ""
        elif stripped_upper.startswith("MEMBER OF COUNCIL"):
            location = stripped.replace("Member of Council", "").replace("MEMBER OF COUNCIL", "").strip()
            current_office = f"Member of Council {location}" if location else "Member of Council"
            current_district = ""
        elif stripped_upper.startswith("COUNCIL AT LARGE") or stripped_upper.startswith("COUNCIL AT-LARGE"):
            # Capture full office name as-is from PDF
            current_office = stripped.strip()
            current_district = ""
        elif stripped_upper.startswith("COUNCIL MEMBER"):
            location = stripped.replace("Council Member", "").replace("COUNCIL MEMBER", "").strip()
            current_office = f"Council Member {location}" if location else "Council Member"
            current_district = ""
        elif stripped_upper.startswith("COMMISSIONER -"):
            # Handle "COMMISSIONER - [WARD] [LOCATION]" format
            current_office = stripped.strip()
            current_district = ""
        elif stripped_upper.startswith("COUNCIL 4YR"):
            # Handle "COUNCIL 4YR [LOCATION]" format
            current_office = stripped.strip()
            current_district = ""
        elif stripped_upper.startswith("COUNCIL 2YR"):
            # Handle "COUNCIL 2YR [LOCATION]" format
            current_office = stripped.strip()
            current_district = ""
        elif stripped_upper.startswith("DECREASE IN NUMBER OF MEMBERS OF"):
            # Handle "DECREASE IN NUMBER OF MEMBERS OF [COUNCIL]" ballot measures
            current_office = stripped.strip()
            current_district = ""
        elif stripped_upper.startswith("WILLIAMS TOWNSHIP EARNED INCOME TAX REFERENDUM"):
            # Handle Williams Township Earned Income Tax Referendum ballot measure
            current_office = stripped.strip()
            current_district = ""
        elif stripped_upper.startswith("CITY MAYOR"):
            # Handle "City Mayor [LOCATION]" format
            current_office = stripped.strip()
            current_district = ""
        elif stripped_upper.startswith("MAYOR"):
            location = stripped.replace("Mayor", "").replace("MAYOR", "").strip()
            # Remove duplicate location names (e.g., "Shamokin City Shamokin City" -> "Shamokin City")
            location = re.sub(r'^(.+?)\s+\1$', r'\1', location)
            current_office = f"Mayor {location}" if location else "Mayor"
            current_district = ""
        elif stripped_upper.startswith("TAX COLLECTOR"):
            location = stripped.replace("Tax Collector", "").replace("TAX COLLECTOR", "").strip()
            # Remove duplicate location names
            location = re.sub(r'^(.+?)\s+\1$', r'\1', location)
            current_office = f"Tax Collector {location}" if location else "Tax Collector"
            current_district = ""
        elif stripped_upper.startswith("CONTROLLER"):
            location = stripped.replace("Controller", "").replace("CONTROLLER", "").strip()
            # Remove duplicate location names
            location = re.sub(r'^(.+?)\s+\1$', r'\1', location)
            current_office = f"Controller {location}" if location else "Controller"
            current_district = ""
        elif stripped_upper.startswith("JUDGE OF ELECTION"):
            location = stripped.replace("Judge of Election", "").replace("JUDGE OF ELECTION", "").strip()
            # Remove duplicate location names
            location = re.sub(r'^(.+?)\s+\1$', r'\1', location)
            current_office = f"Judge of Election {location}" if location else "Judge of Election"
            current_district = ""
        elif stripped_upper.startswith("INSPECTOR OF ELECTION"):
            location = stripped.replace("Inspector of Election", "").replace("INSPECTOR OF ELECTION", "").strip()
            # Remove duplicate location names
            location = re.sub(r'^(.+?)\s+\1$', r'\1', location)
            current_office = f"Inspector of Election {location}" if location else "Inspector of Election"
            current_district = ""
        elif stripped_upper.startswith("INSPECTOR OF ELECTIONS"):
            location = stripped.replace("Inspector of Elections", "").replace("INSPECTOR OF ELECTIONS", "").strip()
            # Remove duplicate location names
            location = re.sub(r'^(.+?)\s+\1$', r'\1', location)
            current_office = f"Inspector of Elections {location}" if location else "Inspector of Elections"
            current_district = ""
        elif " JUDGE OF ELECTIONS " in stripped_upper or stripped_upper.endswith(" JUDGE OF ELECTIONS") or stripped_upper.endswith(" JUDGE OF ELECTIONS 4 YR"):
            # Capture full office name as-is from PDF
            current_office = stripped
            current_district = ""
        elif " INSPECTOR OF ELECTIONS " in stripped_upper or stripped_upper.endswith(" INSPECTOR OF ELECTIONS") or stripped_upper.endswith(" INSPECTOR OF ELECTIONS 4 YR"):
            # Capture full office name as-is from PDF
            current_office = stripped
            current_district = ""
        elif " SCHOOL DIRECTOR " in stripped_upper or "AREA SCHOOL DIRECTOR" in stripped_upper:
            # Capture full office name as-is from PDF
            current_office = stripped
            current_district = ""
        elif "PROPOSED" in stripped_upper and "CODE AMENDMENT" in stripped_upper:
            # Capture full office name as-is from PDF (must include CODE AMENDMENT to avoid matching RET COURT)
            current_office = stripped
            current_district = ""
        elif " MAYOR " in stripped_upper and "CITY OF" in stripped_upper or "BOROUGH" in stripped_upper:
            # Capture full office name as-is from PDF
            current_office = stripped
            current_district = ""
        elif " COUNCIL " in stripped_upper or "COUNCIL PERSON" in stripped_upper:
            # Capture full office name as-is from PDF
            current_office = stripped
            current_district = ""
        elif stripped_upper.startswith("AUDITOR") or " AUDITOR " in stripped_upper or stripped_upper.endswith(" AUDITOR"):
            # Capture full office name as-is from PDF
            current_office = stripped
            current_district = ""
        elif stripped_upper.startswith("TAX COLLECTOR") or " TAX COLLECTOR " in stripped_upper or stripped_upper.endswith(" TAX COLLECTOR"):
            # Capture full office name as-is from PDF
            current_office = stripped
            current_district = ""
        elif stripped_upper.startswith("CONSTABLE") or " CONSTABLE " in stripped_upper or stripped_upper.endswith(" CONSTABLE"):
            # Capture full office name as-is from PDF
            current_office = stripped
            current_district = ""
        elif stripped_upper.startswith("SUPERVISOR") or " SUPERVISOR " in stripped_upper or stripped_upper.endswith(" SUPERVISOR"):
            # Capture full office name as-is from PDF
            current_office = stripped
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
                stripped.startswith("Day") or
                stripped.startswith("Precinct Summary") or
                stripped.startswith("Total Votes") or
                stripped.startswith("Contest Totals") or
                stripped.startswith("Voter Turnout") or
                stripped.startswith("Election Day Precincts") or
                stripped_upper.startswith("VOTE FOR") or
                stripped_upper.startswith("TOTAL VOTES") or
                stripped_upper.startswith("CONTEST TOTALS") or
                stripped_upper.startswith("PRECINCTS") or
                # Skip date patterns like "November 4, 2025 Northumberland County"
                re.match(r'^(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d+,\s+\d{4}', stripped) or
                # Skip "NOVEMBER 4, 2025 CENTRE COUNTY" patterns appearing mid-text
                re.search(r'(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d+,\s+\d{4}\s+.*COUNTY', stripped)):
                i += 1
                continue
            
            # Parse candidate/vote lines
            # Format: "PARTY CANDIDATE_NAME TOTAL ELECTION_DAY MAIL PROVISIONAL"
            # Party codes: DEM, REP, LIB, LBR, GRN, CST, FWD, ASP, DAR, DEM/REP, etc.
            
            # Check for Yes/No votes (retention elections)
            if stripped_upper.startswith("YES") or stripped_upper.startswith("NO"):
                # Remove percentages from parts before extracting votes
                parts = [p for p in stripped.split() if not re.match(r'^\d+(?:\.\d+)?%$', p)]
                if len(parts) >= 5:
                    candidate = parts[0]  # "YES" or "NO"
                    votes = [p.replace(',', '') for p in parts[-4:]]
                    results.append([county, current_office, current_district,
                                  "", candidate, votes[0], votes[1], votes[2], votes[3]])
            
            # Check for Write-In Totals (skip the nested Write-In: Scattered details)
            elif stripped_upper.startswith("WRITE-IN TOTALS"):
                # Remove percentages from parts before extracting votes
                parts = [p for p in stripped.split() if not re.match(r'^\d+(?:\.\d+)?%$', p)]
                if len(parts) >= 5:
                    votes = [p.replace(',', '') for p in parts[-4:]]
                    results.append([county, current_office, current_district,
                                  "", "Write-ins", votes[0], votes[1], votes[2], votes[3]])
            
            # Skip "Not Assigned" rows entirely (these appear after Write-In Totals)
            elif "Not Assigned" in stripped or "NOT ASSIGNED" in stripped_upper:
                # Skip these rows entirely
                pass
            
            # Check for Over Votes / Under Votes
            elif stripped_upper.startswith("OVER VOTES"):
                # Remove percentages from parts before extracting votes
                parts = [p for p in stripped.split() if not re.match(r'^\d+(?:\.\d+)?%$', p)]
                if len(parts) >= 4:
                    votes = [p.replace(',', '') for p in parts[-4:]]
                    if len(votes) == 4:
                        results.append([county, current_office, current_district,
                                      "", "Over Votes", votes[0], votes[1], votes[2], votes[3]])
            
            elif stripped_upper.startswith("UNDER VOTES"):
                # Remove percentages from parts before extracting votes
                parts = [p for p in stripped.split() if not re.match(r'^\d+(?:\.\d+)?%$', p)]
                if len(parts) >= 4:
                    votes = [p.replace(',', '') for p in parts[-4:]]
                    if len(votes) == 4:
                        results.append([county, current_office, current_district,
                                      "", "Under Votes", votes[0], votes[1], votes[2], votes[3]])
            
            # Skip nested Write-In: Scattered lines
            elif stripped_upper.startswith("WRITE-IN:"):
                pass
            
            # Check for party-candidate lines
            else:
                # Skip lines that look like date headers appearing mid-file
                if re.search(r'(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d+,?\s+\d{4}', stripped, re.IGNORECASE):
                    i += 1
                    continue
                
                # Pattern 1: "PARTY CANDIDATE_NAME VOTES ELECTION_DAY MAIL PROVISIONAL"
                # Pattern 2: "CANDIDATE_NAME VOTES ELECTION_DAY MAIL PROVISIONAL" (no party)
                # Parties can be: DEM, REP, LIB, LBR, GRN, CST, FWD, ASP, DAR, DEM/REP, DNR, AAI, AID, etc.
                # Known valid parties: DEM, REP, LIB, LBR, GRN, CST, FWD, ASP, DAR, IND, DNR, AAI, AID
                
                # Try pattern with known party prefix
                # May include percentage like "REP MARIA BATTISTA 14,591  68.31%  13,268  1,319  4"
                # Known party codes to validate against
                base_parties = {
                    'DEM', 'REP', 'LIB', 'LBR', 'GRN', 'CST', 'FWD', 'ASP', 'DAR',
                    'IND', 'NOP', 'DNR', 'WOR', 'GRE', 'LIN', 'PIA', 'PIU'
                }
                
                # Match any 2-4 letter party code followed by a space, then candidate name
                party_match = re.match(r'^([A-Z]{2,4}(?:/[A-Z]{2,4})*)\s+(.+?)\s+(\d+(?:,\d+)?)\s+(?:\d+(?:\.\d+)?%)?\s*(\d+(?:,\d+)?)\s+(\d+(?:,\d+)?)\s+(\d+(?:,\d+)?)$', stripped, re.IGNORECASE)
                
                if party_match:
                    party_raw = party_match.group(1).upper()
                    party_parts = party_raw.split('/')
                    party_is_valid = all(part in base_parties for part in party_parts)
                else:
                    party_is_valid = False
                
                if party_match and party_is_valid:
                    party = '/'.join(party_parts)
                    candidate = party_match.group(2).strip()  # Strip extra spaces from candidate name
                    total = party_match.group(3).replace(',', '')
                    election_day = party_match.group(4).replace(',', '')
                    mail = party_match.group(5).replace(',', '')
                    provisional = party_match.group(6).replace(',', '')
                    
                    results.append([county, current_office, current_district,
                                  party, candidate, total, election_day, mail, provisional])
                else:
                    # Try pattern without party, may include percentage
                    # "No 12,184 59.37% 10,944 1,235 5" -> candidate="No", total=12184, election_day=10944, mail=1235, provisional=5
                    no_party_match = re.match(r'^(.+?)\s+(\d+(?:,\d+)?)\s+(?:\d+(?:\.\d+)?%)?\s+(\d+(?:,\d+)?)\s+(\d+(?:,\d+)?)\s+(\d+)$', stripped)
                    if no_party_match:
                        candidate = no_party_match.group(1).strip()
                        total = no_party_match.group(2).replace(',', '')
                        election_day = no_party_match.group(3).replace(',', '')
                        mail = no_party_match.group(4).replace(',', '')
                        provisional = no_party_match.group(5)
                        
                        # Skip "Not" candidate rows (these are duplicates of "Not Assigned")
                        if candidate.strip().upper() != "NOT":
                            results.append([county, current_office, current_district,
                                          "", candidate, total, election_day, mail, provisional])
        
        i += 1
    
    # Filter out rows where candidate is "Not" (these are duplicates of "Not Assigned")
    results = [row for row in results if row[4] != "Not"]
    
    # Filter out rows where candidate contains month names (page headers parsed as candidates)
    month_names = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December']
    results = [row for row in results if row[4] not in month_names and row[4].upper() not in [m.upper() for m in month_names]]
    
    # Write to CSV
    with open(output_csv, 'w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        writer.writerow(['county', 'office', 'district', 'party', 'candidate',
                        'votes', 'election_day', 'mail', 'provisional'])
        writer.writerows(results)
    
    print(f"Results written to {output_csv}")
    print(f"Total rows: {len(results)}")


if __name__ == "__main__":
    if len(sys.argv) < 3 or len(sys.argv) > 4:
        print("Usage: python electionware_text_county.py <pdf_path> <output_csv> [county_name]")
        print("  county_name: Optional county name in titlecase (e.g., 'Centre')")
        sys.exit(1)
    
    pdf_path = sys.argv[1]
    output_csv = sys.argv[2]
    county_name = sys.argv[3] if len(sys.argv) == 4 else None
    
    parse_election_results(pdf_path, output_csv, county_name)
