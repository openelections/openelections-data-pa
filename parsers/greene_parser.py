from bs4 import BeautifulSoup
import requests
import csv
import re

def clean_text(text):
    """Clean and standardize text fields"""
    if not text:
        return ""
    # Remove extra whitespace
    text = " ".join(text.strip().split())
    # Convert to title case for names
    return text

def parse_party(text):
    """Extract standardized party abbreviation"""
    party_map = {
        "Democrat": "DEM",
        "Republican": "REP",
        "Libertarian": "LIB",
        "Green": "GRN",
        "Common Sense Thinking": "CST",
        "No Party Specified": "FWD"
    }
    for full_name, abbrev in party_map.items():
        if full_name.lower() in text.lower():
            return abbrev
    return ""

def parse_name(text):
    """Clean up candidate names"""
    if not text:
        return ""
    # Remove party prefixes like "Dem", "Rep", etc.
    name = re.sub(r'^(Dem|Rep|Lib|Grn|Cst|Fwd|Asp)\s+', '', text)
    # Convert to title case and clean up
    return clean_text(name)

def parse_office(text):
    """Standardize office names"""
    office_map = {
        "Presidential Electors": "President",
        "United States Senator": "U.S. Senate",
        "Attorney General": "Attorney General",
        "Auditor General": "Auditor General",
        "State Treasurer": "State Treasurer",
        "Representative In Congress": "U.S. House",
        "Representative In Thegeneral Assembly": "General Assembly",
        "Judge Of The Superior Court": "Judge of the Superior Court",
        "Judge Of The Commonwealth Court": "Judge of the Commonwealth Court",
        "Judge Of The Court Of Common Pleas": "Judge of the Court of Common Pleas",
        "Magisterial District Judge - District 13-3-03": "Magisterial District Judge - District 13-3-03",
        "School Director (4-yr) Carmichaels Area School District (Vote for 4)": "School Director (4-yr) Carmichaels Area School District",
        "School Director (4-yr) Central Greene School District At (Vote for 5)": "School Director (4-yr) Central Greene School District",
        "School Director (4-yr) Jefferson-morgan Area School Dist (Vote for 4)": "School Director (4-yr) Jefferson-Morgan Area School District",
        "School Director (4-yr) Southeastern Greene School Distri (Vote for 4)": "School Director (4-yr) Southeastern Greene School District",
        "School Director (2-yr) West Greene School District - At (Vote for 2)": "School Director (2-yr) West Greene School District - At Large",
        "School Director (4-yr) West Greene School District Regio": "School Director (4-yr) West Greene School District Region",
        "Member Of Council Carmichaels Borough (Vote for 3)": "Member of Council Carmichaels Borough",
        "Member Of Council (2-yr) Carmichaels Borough (Vote for 2)": "Member of Council (2-yr) Carmichaels Borough",
        "Member Of Council Clarksville Borough (Vote for 2)": "Member of Council Clarksville Borough",
        "Member Of Council (4-yr) Jefferson Borough (Vote for 2)": "Member of Council (4-yr) Jefferson Borough",
        "Member Of Council Rices Landing Borough (Vote for 3)": "Member of Council Rices Landing Borough",
        "Member Of Council (4-yr) Waynesburg Borough Ward 2 (Vote for 2)": "Member of Council (4-yr) Waynesburg Borough Ward 2"
    }
    
    for full_name, standard_name in office_map.items():
        if full_name.lower() in text.lower():
            return standard_name
    return text

def parse_district(text):
    """Extract district number if present"""
    district_match = re.search(r'(\d+)[thstrdnd]*\s+District', text)
    if district_match:
        return district_match.group(1)
    return ""

def scrape_election_results(html_content):
    soup = BeautifulSoup(html_content, 'html.parser')
    
    # Get county and precinct info
    county = soup.find('h1').text.strip()
    precinct = soup.find('h3').text.strip()
    
    results = []
    
    # Find all results modules
    for section in soup.find_all('div', id='results_module'):
        # Get the office name from the preceding h1
        office_header = section.find_previous('h1')
        if not office_header:
            continue
            
        office_text = office_header.text.strip()
        office = parse_office(office_text)
        district = parse_district(office_text)
        
        # Process each row in the results table
        for row in section.find_all('tr')[1:]:  # Skip header row
            cells = row.find_all('td')
            if len(cells) < 3:
                continue
                
            candidate_cell = cells[0]
            votes_cell = cells[4]
            
            # Extract candidate name and party
            candidate_first = candidate_cell.find('h1')
            candidate_last = candidate_cell.find('h2').text.strip().split(" (")[0]
            if candidate_first:
                candidate_first = parse_name(candidate_first.text.strip())
            else:
                candidate_first = ""

            candidate_name = candidate_first + " " + candidate_last 
                
            party_span = candidate_cell.find('span', class_='party_name')
            party = ""
            if party_span:
                party = parse_party(party_span.text)
                
            votes = votes_cell.text.strip()
            
            # Special handling for registration and turnout
            if "Registered Voters" in office_text:
                office = "Registered Voters"
                party = ""
                candidate_name = ""
            elif "Ballots Cast" in office_text:
                office = "Ballots Cast"
                party = ""
                candidate_name = ""
            
            results.append({
                'county': county,
                'precinct': precinct,
                'office': office,
                'district': district,
                'party': party,
                'candidate': candidate_name,
                'votes': votes
            })
    
    return results

def write_csv(results, output_file='election_results.csv'):
    """Write results to CSV file"""
    fieldnames = ['county', 'precinct', 'office', 'district', 'party', 'candidate', 'votes']
    
    with open(output_file, 'w', newline='') as csvfile:
        writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
        writer.writeheader()
        for result in results:
            writer.writerows(result)

def main():

    all_results = []
    for precinct in (10173, 17, 10174, 30, 20206, 20207, 20209, 20208, 20210, 20211, 20212, 20214, 20215, 20213, 10185, 10186, 10187, 10188, 4, 10189, 6, 20216, 20217, 20218, 20219, 20220, 20221, 20223, 20222, 20224, 10199, 10200, 19, 10201, 10202, 10203, 20225, 20226, 20227, 20228, 20229, 10206):
        # Read the HTML file
        r = requests.get(f'https://greenecountypa.gov/elections/Default.aspx?PageLayout=BYPRECINCT&Election=30063&Precinct={precinct}')
        html_content = r.text
    
        # Parse the results
        all_results.append(scrape_election_results(html_content))
    
    # Write to CSV
    write_csv(all_results)

if __name__ == "__main__":
    main()