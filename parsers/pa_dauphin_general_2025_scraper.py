#!/usr/bin/env python3
"""
Dauphin County, PA 2025 Municipal Election Results Scraper

Scrapes county-level results from Dauphin County website and outputs
OpenElections standardized CSV format.

Usage:
    python pa_dauphin_general_2025_scraper.py <output_csv>
"""

import sys
import csv
import time
import re
import requests
from bs4 import BeautifulSoup
from typing import List, Dict
import urllib.parse

BASE_URL = "https://www.dauphinc.org"
ELECTION_URL = f"{BASE_URL}/election/?key=40"


def fetch_race_list() -> List[str]:
    """Fetch list of all race URLs from main page"""
    print("Fetching race list...")
    response = requests.get(ELECTION_URL)
    response.raise_for_status()

    soup = BeautifulSoup(response.text, 'html.parser')

    # Find all race links
    race_links = []
    for link in soup.find_all('a', href=True):
        href = link['href']
        if '?key=40&race=' in href or '?key=40&amp;race=' in href:
            # Convert relative to absolute URL
            if href.startswith('/'):
                href = BASE_URL + href
            # Decode HTML entities
            href = href.replace('&amp;', '&')
            race_links.append(href)

    # Remove duplicates
    race_links = list(set(race_links))
    print(f"Found {len(race_links)} races")

    return sorted(race_links)


def normalize_office(race_name: str, office_text: str) -> tuple:
    """
    Convert race name and office text to OpenElections format.
    Returns (normalized_office, district)
    """
    race_name = race_name.strip()
    office_text = office_text.strip()

    # State judges
    if 'SUPERIOR COURT' in race_name or 'JUDGE OF THE SUPERIOR COURT' in office_text:
        return ('Judge of the Superior Court', '')

    if 'COMMONWEALTH COURT' in race_name or 'JUDGE OF THE COMMONWEALTH COURT' in office_text:
        return ('Judge of the Commonwealth Court', '')

    # Retention races
    if 'RETENTION' in race_name or 'RETENTION' in office_text:
        # Extract judge name from race_name or office_text
        # Format: "RETENTION-SUPERIOR COURT-ALICE BECK DUBOW"
        # Try office_text first (more specific), then race_name
        search_text = office_text if 'RETENTION' in office_text else race_name
        name_match = re.search(r'RETENTION[- ](?:SUPREME|SUPERIOR|COMMONWEALTH)\s+COURT[- ](.+)', search_text, re.IGNORECASE)
        if name_match:
            judge_name = name_match.group(1).strip().title()
            if 'SUPREME' in search_text:
                return (f'Justice of the Supreme Court Retention {judge_name}', '')
            elif 'SUPERIOR' in search_text:
                return (f'Judge of the Superior Court Retention {judge_name}', '')
            elif 'COMMONWEALTH' in search_text:
                return (f'Judge of the Commonwealth Court Retention {judge_name}', '')

    # County offices
    if 'COURT OF COMMON PLEAS' in race_name or 'COURT OF COMMON PLEAS' in office_text:
        return ('Judge of the Court of Common Pleas', '')

    if 'PROTHONOTARY' in race_name:
        return ('Prothonotary', '')

    if 'CORONER' in race_name:
        return ('Coroner', '')

    if 'CLERK OF COURTS' in race_name:
        return ('Clerk of Courts', '')

    # School districts
    if 'SCHOOL DISTRICT' in race_name:
        # Extract district name and region
        # Format: "CENTRAL DAUPHIN SCHOOL DISTRICT REGION I"
        match = re.match(r'(.+?SCHOOL DISTRICT)\s*(?:REGION\s+([IVX]+))?', race_name, re.IGNORECASE)
        if match:
            district_name = match.group(1).strip().title()
            region = match.group(2) if match.group(2) else ''
            return (district_name, region)

    # Judge of Election and Inspector of Election
    # Format: "HARRISBURG 1-1 JUDGE OF ELECTIONS", "DERRY TWP 1 INSPECTOR OF ELECTIONS"
    election_official_match = re.match(r'(.+?)\s+(JUDGE OF ELECTIONS?|INSPECTOR OF ELECTIONS?)$', office_text, re.IGNORECASE)
    if election_official_match:
        jurisdiction = election_official_match.group(1).strip().title()
        office = election_official_match.group(2).strip()

        # Normalize office name
        if 'JUDGE' in office.upper():
            office = f"Judge of Elections - {jurisdiction}"
        else:
            office = f"Inspector of Elections - {jurisdiction}"

        return (office, '')

    # Municipal offices
    # Format: "BERRYSBURG MAYOR", "BERRYSBURG COUNCIL", "DERRY TOWNSHIP SUPERVISOR"
    # Also: "LOWER PAXTON SUPERVISOR (6-YEAR TERM)", "SUSQUEHANNA 2ND WARD TOWNSHIP COMMISSIONER"
    # Extract office and municipality
    municipal_match = re.match(r'(.+?)\s+(MAYOR|COUNCIL|SUPERVISOR|TAX COLLECTOR|AUDITOR|COMMISSIONER)(\s*\([^)]+\))?', office_text, re.IGNORECASE)
    if municipal_match:
        municipality = municipal_match.group(1).strip().title()
        office_base = municipal_match.group(2).strip()
        term_info = municipal_match.group(3) if municipal_match.group(3) else ''

        # Extract term length if present
        term_year = ''
        if term_info and 'YEAR' in term_info.upper():
            term_match = re.search(r'\((\d+)-YEAR', term_info, re.IGNORECASE)
            if term_match:
                term_year = f" {term_match.group(1)} Year Term"

        # Normalize office title
        if 'AUDITOR' in office_base.upper():
            office = f"Auditor{term_year} - {municipality}"
        elif 'TAX COLLECTOR' in office_base.upper():
            office = f"Tax Collector{term_year} - {municipality}"
        elif 'SUPERVISOR' in office_base.upper():
            office = f"Supervisor{term_year} - {municipality}"
        elif 'COUNCIL' in office_base.upper():
            office = f"Council{term_year} - {municipality}"
        elif 'COMMISSIONER' in office_base.upper():
            office = f"Commissioner{term_year} - {municipality}"
        elif 'MAYOR' in office_base.upper():
            office = f"Mayor{term_year} - {municipality}"
        else:
            office = f"{office_base.title()}{term_year} - {municipality}"

        return (office, '')

    # Default: title case the race name
    return (race_name.title(), '')


def fetch_race_results(race_url: str) -> List[Dict]:
    """Fetch and parse results for a single race"""
    print(f"Fetching: {race_url}")

    try:
        response = requests.get(race_url)
        response.raise_for_status()
    except requests.RequestException as e:
        print(f"Error fetching {race_url}: {e}")
        return []

    soup = BeautifulSoup(response.text, 'html.parser')

    results = []

    # Find all race tables (there may be multiple races on one page, e.g., municipal offices)
    race_tables = soup.find_all('table', id='tblRace')

    for table in race_tables:
        # Extract office name from table header
        office_header = table.find('span', class_='color-lightgrey font-weight-bolder')
        if not office_header:
            continue

        office_text = office_header.get_text(strip=True)
        # Remove vote count info
        office_text = re.sub(r'\s*\(\s*Vote\s+For\s+\d+\s*\)', '', office_text, flags=re.IGNORECASE)

        # Get race name from URL
        race_name = race_url.split('race=')[-1]
        race_name = urllib.parse.unquote(race_name)

        # Normalize office name
        normalized_office, district = normalize_office(race_name, office_text)

        # Find all candidate rows
        # Each row is a <tr> with class "bg-white color-black bold-font"
        candidate_rows = table.find_all('tr', class_='bg-white')

        for row in candidate_rows:
            # Find the candidate name div (col-xs-8)
            name_div = row.find('div', class_='col-xs-8')
            if not name_div:
                continue

            strong = name_div.find('strong')
            if not strong:
                continue

            # Extract party
            party_span = strong.find('span')
            party = party_span.get_text(strip=True) if party_span else ''

            # Extract candidate name
            candidate_text = strong.get_text(strip=True)
            # Remove party prefix - match any uppercase letters followed by optional space and period
            # This handles: "DEM . ", "DEM/REP.", "NONPARTISAN.", "/REP.", "LIBERAL.", "ERAL."
            candidate_text = re.sub(r'^[A-Z/]+\s*\.?\s*', '', candidate_text)
            candidate = candidate_text.strip()

            # Find the votes div (col-md-12 with style containing margin-top:-10px)
            votes_div = row.find('div', class_='col-md-12', style=lambda x: x and 'margin-top:-10px' in x)
            if not votes_div:
                # Try without style check
                votes_div = row.find('div', class_='col-md-12')

            votes = ''
            if votes_div:
                votes_text = votes_div.get_text(strip=True)
                # Extract just the number (might have other text)
                vote_match = re.search(r'(\d+)', votes_text)
                if vote_match:
                    votes = vote_match.group(1)

            if candidate and votes:
                results.append({
                    'county': 'Dauphin',
                    'office': normalized_office,
                    'district': district,
                    'party': party,
                    'candidate': candidate,
                    'votes': votes,
                    'election_day': '',
                    'mail': '',
                    'provisional': ''
                })

    return results


def get_metadata() -> List[Dict]:
    """Get Ballots Cast metadata from main page"""
    print("Fetching metadata...")
    response = requests.get(ELECTION_URL)
    response.raise_for_status()

    soup = BeautifulSoup(response.text, 'html.parser')

    metadata = []

    # Look for ballots cast info
    # It's in the text somewhere - search for patterns
    text = soup.get_text()

    # Try to find total ballots cast
    # The page might have text like "Total Ballots Cast: 123456"
    match = re.search(r'Total\s+Ballots?\s+Cast[:\s]+(\d+)', text, re.IGNORECASE)
    if match:
        ballots_cast = match.group(1)
        metadata.append({
            'county': 'Dauphin',
            'office': 'Ballots Cast',
            'district': '',
            'party': '',
            'candidate': '',
            'votes': ballots_cast,
            'election_day': '',
            'mail': '',
            'provisional': ''
        })

    return metadata


def write_csv(results: List[Dict], output_path: str):
    """Write results to CSV in OpenElections format"""
    fieldnames = [
        'county', 'office', 'district', 'party',
        'candidate', 'votes', 'election_day', 'mail', 'provisional'
    ]

    with open(output_path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(results)


def main():
    if len(sys.argv) != 2:
        print("Usage: python pa_dauphin_general_2025_scraper.py <output_csv>")
        sys.exit(1)

    output_csv = sys.argv[1]

    # Get metadata
    all_results = get_metadata()

    # Get list of races
    race_urls = fetch_race_list()

    # Scrape each race
    for i, race_url in enumerate(race_urls, 1):
        print(f"Processing race {i}/{len(race_urls)}")
        race_results = fetch_race_results(race_url)
        all_results.extend(race_results)

        # Be nice to the server
        time.sleep(0.5)

    # Write results
    print(f"\nWriting {len(all_results)} records to {output_csv}...")
    write_csv(all_results, output_csv)

    print("Done!")


if __name__ == '__main__':
    main()
