#!/usr/bin/env python3
"""
Dauphin County, PA 2025 Municipal Election Precinct Results Scraper

Scrapes precinct-level results from Dauphin County website and outputs
OpenElections standardized CSV format.

Usage:
    python pa_dauphin_general_2025_precinct_scraper.py <output_csv>
"""

import sys
import csv
import time
import re
import requests
from bs4 import BeautifulSoup
from typing import List, Dict
import urllib.parse

# Import the normalize_office function from the county scraper
import importlib.util
spec = importlib.util.spec_from_file_location("county_scraper", "parsers/pa_dauphin_general_2025_scraper.py")
county_scraper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(county_scraper)
normalize_office = county_scraper.normalize_office

BASE_URL = "https://www.dauphinc.org"
ELECTION_URL = f"{BASE_URL}/election/?key=40"


def fetch_race_list() -> List[Dict[str, str]]:
    """Fetch list of all races with their URLs and office texts"""
    print("Fetching race list...")
    response = requests.get(ELECTION_URL)
    response.raise_for_status()

    soup = BeautifulSoup(response.text, 'html.parser')

    races = []

    # Find all race links
    for link in soup.find_all('a', href=True):
        href = link['href']
        if '?key=40&race=' in href or '?key=40&amp;race=' in href:
            # Convert relative to absolute URL
            if href.startswith('/'):
                href = BASE_URL + href
            # Decode HTML entities
            href = href.replace('&amp;', '&')

            # Extract race name from URL
            race_name = href.split('race=')[-1]
            race_name = urllib.parse.unquote(race_name)

            races.append({
                'url': href,
                'race_name': race_name
            })

    # Remove duplicates by race_name
    seen = set()
    unique_races = []
    for race in races:
        if race['race_name'] not in seen:
            seen.add(race['race_name'])
            unique_races.append(race)

    print(f"Found {len(unique_races)} races")
    return sorted(unique_races, key=lambda x: x['race_name'])


def get_office_texts_from_race_page(race_url: str) -> List[str]:
    """Get all office texts from a race page (may have multiple contests)"""
    response = requests.get(race_url)
    response.raise_for_status()

    soup = BeautifulSoup(response.text, 'html.parser')

    office_texts = []
    race_tables = soup.find_all('table', id='tblRace')

    for table in race_tables:
        office_header = table.find('span', class_='color-lightgrey font-weight-bolder')
        if office_header:
            office_text = office_header.get_text(strip=True)
            # Remove vote count info
            office_text = re.sub(r'\s*\(\s*Vote\s+For\s+\d+\s*\)', '', office_text, flags=re.IGNORECASE)
            office_texts.append(office_text)

    return office_texts


def fetch_precinct_results(race_name: str, office_text: str) -> List[Dict]:
    """Fetch precinct-level results for a single race/office"""
    # Construct precinct results URL
    precinct_url = f"{BASE_URL}/election/Races?key=40&race={urllib.parse.quote(office_text)}"

    print(f"  Fetching precincts for: {office_text}")

    try:
        response = requests.get(precinct_url)
        response.raise_for_status()
    except requests.RequestException as e:
        print(f"  Error fetching {precinct_url}: {e}")
        return []

    soup = BeautifulSoup(response.text, 'html.parser')

    results = []

    # Normalize the office name
    normalized_office, district = normalize_office(race_name, office_text)

    # Find all tables with precinct results
    tables = soup.find_all('table', class_='padding')

    for table in tables:
        # Find the header row to get candidate names
        header_row = table.find('tr', class_='uppercase')
        if not header_row:
            continue

        # Extract candidate names from header
        header_cells = header_row.find_all('td', class_='padding')
        candidates = []
        for cell in header_cells:
            candidate_text = cell.get_text(strip=True)
            if candidate_text:  # Skip empty cells
                candidates.append(candidate_text)

        if not candidates:
            continue

        # Find all precinct rows
        precinct_rows = table.find_all('tr', class_='font-weight-bolder')

        for row in precinct_rows:
            cells = row.find_all('td', class_='padding')
            if not cells:
                continue

            # First cell is precinct name
            precinct_cell = cells[0]
            precinct_span = precinct_cell.find('span')
            if not precinct_span:
                continue

            precinct_name = precinct_span.get_text(strip=True)

            # Skip if this looks like a total/summary row
            if 'TOTAL' in precinct_name.upper() or not precinct_name:
                continue

            # Remaining cells are vote counts for each candidate
            vote_cells = cells[1:]

            for i, candidate in enumerate(candidates):
                if i < len(vote_cells):
                    vote_text = vote_cells[i].get_text(strip=True)
                    # Extract just the number
                    vote_match = re.search(r'(\d+)', vote_text)
                    if vote_match:
                        votes = vote_match.group(1)

                        results.append({
                            'county': 'Dauphin',
                            'precinct': precinct_name,
                            'office': normalized_office,
                            'district': district,
                            'party': '',
                            'candidate': candidate,
                            'votes': votes
                        })

    return results


def write_csv(results: List[Dict], output_path: str):
    """Write results to CSV in OpenElections format"""
    fieldnames = [
        'county', 'precinct', 'office', 'district', 'party', 'candidate', 'votes'
    ]

    with open(output_path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(results)


def main():
    if len(sys.argv) != 2:
        print("Usage: python pa_dauphin_general_2025_precinct_scraper.py <output_csv>")
        sys.exit(1)

    output_csv = sys.argv[1]

    all_results = []

    # Get list of races
    races = fetch_race_list()

    # Scrape each race
    for i, race in enumerate(races, 1):
        print(f"\nProcessing race {i}/{len(races)}: {race['race_name']}")

        # Get office texts from the race page
        try:
            office_texts = get_office_texts_from_race_page(race['url'])
        except Exception as e:
            print(f"  Error getting office texts: {e}")
            continue

        # For each office on the race page, fetch precinct results
        for office_text in office_texts:
            precinct_results = fetch_precinct_results(race['race_name'], office_text)
            all_results.extend(precinct_results)

            # Be nice to the server
            time.sleep(0.5)

    # Write results
    print(f"\nWriting {len(all_results)} records to {output_csv}...")
    write_csv(all_results, output_csv)

    print("Done!")


if __name__ == '__main__':
    main()
