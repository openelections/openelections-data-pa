#!/usr/bin/env python3
"""
Scraper for Philadelphia County 2025 General Election Board Workers Results

Scrapes election board worker (Judge of Election, Inspector of Election, etc.)
results from Philadelphia's election results website for all 66 wards and aggregates
them to county-level.

The website structure:
- Main page lists all 66 wards
- Each ward has its own page with results for multiple divisions
- Results are displayed in HTML tables on the page

Usage:
    python parsers/pa_philadelphia_general_2025_boardworkers_scraper.py

Output:
    2025/counties/20251104__pa__general__philadelphia__boardworkers__county.csv
"""

import requests
from bs4 import BeautifulSoup
import csv
import time
import re
from collections import defaultdict


def scrape_ward_results(ward_number):
    """
    Scrape election board worker results for a single ward.

    Args:
        ward_number: Ward number (1-66)

    Returns:
        list of dict: Each dict represents a result with keys:
            office, candidate, party, votes
    """
    # Format ward number with leading zero
    ward_str = str(ward_number).zfill(2)
    url = f"https://philadelphiaresults.azurewebsites.us/ResultsSW.aspx?type=BDW&Area={ward_str}&map=CTY"

    print(f"  Fetching Ward {ward_str}...")

    try:
        response = requests.get(url, timeout=30)
        response.raise_for_status()
    except Exception as e:
        print(f"  Error fetching Ward {ward_str}: {e}")
        return []

    soup = BeautifulSoup(response.content, 'html.parser')
    results = []

    # Find all race headers (H1 elements with race title like "JUDGE OF ELECTION 01-01")
    # The structure is:
    # - H1 with race title (e.g., "JUDGE OF ELECTION 01-01<BR/>(VOTE FOR 1)")
    # - Then multiple section groups with candidate info
    #   - display-results-box-d contains H1 with candidate name and H2 with party
    #   - display-results-box-f contains H1 with vote count

    # Find all elements that could be race titles
    all_h1 = soup.find_all('h1')

    for h1 in all_h1:
        race_text = h1.get_text(strip=True)

        # Skip if not an election board worker race
        # (These races have division numbers like "01-01", "01-02", etc.)
        if not re.search(r'\d{2}-\d{2}', race_text):
            continue

        # Extract office name and division
        # Format: "JUDGE OF ELECTION 01-01(VOTE FOR 1)"
        match = re.match(r'(.+?)\s+(\d{2}-\d{2})', race_text)
        if not match:
            continue

        office = match.group(1).strip()
        division = match.group(2).strip()

        # Normalize office name
        office = office.title()

        # Find the container for this race by going up the parent chain
        # The race H1 is in display-results-box-a, which is in display-results-box-wrapper,
        # which is in a div that contains the candidate sections
        race_container = h1.parent.parent.parent
        if not race_container:
            continue

        # Find all candidate sections within this container
        # Each candidate has display-results-box-d (name/party) and display-results-box-f (votes)
        candidate_name_boxes = race_container.find_all('div', class_='display-results-box-d')

        for name_box in candidate_name_boxes:
            # Extract candidate name
            name_h1 = name_box.find('h1')
            if not name_h1:
                continue
            candidate_name = name_h1.get_text(strip=True)

            # Extract party (in H2)
            party_h2 = name_box.find('h2')
            party = party_h2.get_text(strip=True) if party_h2 else None

            # Clean up party
            if party and party.lower() in ['nonpartisan', 'non']:
                party = None

            # Find the corresponding votes box
            # The structure is: section group contains both name_box and votes_box
            section = name_box.find_parent('div', class_='section')
            if not section:
                section = name_box.find_parent('div', class_='group')
            if not section:
                # Try to find parent that contains both boxes
                parent = name_box.parent
                while parent and parent.name != 'body':
                    votes_box = parent.find('div', class_='display-results-box-f')
                    if votes_box:
                        break
                    parent = parent.parent
            else:
                votes_box = section.find('div', class_='display-results-box-f')

            if not votes_box:
                continue

            votes_h1 = votes_box.find('h1')
            if not votes_h1:
                continue
            votes_text = votes_h1.get_text(strip=True).replace(',', '')

            try:
                votes = int(votes_text)
            except ValueError:
                continue

            results.append({
                'office': office,
                'division': division,
                'candidate': candidate_name,
                'party': party,
                'votes': votes
            })

    return results


def aggregate_to_county_level(all_results):
    """
    Aggregate division-level results to county-level by including ward in office name.

    Each division has a format like "01-01" where the first two digits are the ward.
    We'll create office names like "Judge of Election Ward 01" to preserve ward-level
    aggregation while still being at county level (no precinct column).

    Args:
        all_results: List of result dicts with office, division, candidate, party, votes

    Returns:
        list of dict: Aggregated county-level results with ward in office name
    """
    # Use a dict to accumulate votes
    # Key: (office_with_ward, candidate, party)
    aggregated = defaultdict(int)

    for result in all_results:
        # Extract ward from division (e.g., "01-01" -> "01")
        division = result['division']
        ward = division.split('-')[0]

        # Create office name with ward
        office_with_ward = f"{result['office']} Ward {ward}"

        key = (office_with_ward, result['candidate'], result['party'])
        aggregated[key] += result['votes']

    # Convert back to list of dicts
    county_results = []
    for (office, candidate, party), votes in sorted(aggregated.items()):
        county_results.append({
            'county': 'Philadelphia',
            'office': office,
            'district': None,
            'party': party,
            'candidate': candidate,
            'votes': votes
        })

    return county_results


def main():
    """
    Main function to scrape all 66 wards and aggregate to county level.
    """
    output_dir = '2025/counties'
    output_file = '20251104__pa__general__philadelphia__boardworkers__county.csv'

    import os
    os.makedirs(output_dir, exist_ok=True)

    print("Starting scrape of Philadelphia election board worker results...")
    print("This will take several minutes (66 wards to process)")

    all_results = []

    # Scrape each ward (1-66)
    for ward_num in range(1, 67):
        results = scrape_ward_results(ward_num)
        all_results.extend(results)
        print(f"  Ward {ward_num:02d}: {len(results)} results")

        # Be polite - add a small delay between requests
        time.sleep(1)

    print(f"\nTotal raw results scraped: {len(all_results)}")

    # Aggregate to county level
    print("Aggregating to county level...")
    county_results = aggregate_to_county_level(all_results)

    print(f"Total county-level results: {len(county_results)}")

    # Write to CSV
    output_path = os.path.join(output_dir, output_file)
    fieldnames = ['county', 'office', 'district', 'party', 'candidate', 'votes']

    with open(output_path, 'w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for result in county_results:
            writer.writerow(result)

    print(f"\nOutput written to: {output_path}")


if __name__ == '__main__':
    main()
