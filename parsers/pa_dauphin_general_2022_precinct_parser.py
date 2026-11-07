#!/usr/bin/env python3
"""
Dauphin County, PA 2022 General Election Precinct Results Scraper

Adapted from pa_dauphin_general_2025_precinct_scraper.py for the 2022 general
election (site key=34). Scrapes the per-municipality precinct tables
(/election/Races?key=34&race=...) and writes the OpenElections precinct CSV.

Site quirks handled (verified 2026-11):
- Each race page links to its own "Races" page; the Races URL's race= param is
  the office name ("UNITED STATES SENATOR"). The Races page has FOUR tables:
  a visible precinct grid, a hidden clone of it, and two municipality-aggregate
  grids ("City of Harrisburg") — only parse tables whose class list contains
  "my_tab" (the visible precinct grid); the clone doubles every value and
  tab2 tables are municipality roll-ups.
- Candidate/office text is "SURNAME, FIRST MIDDLE"; converted to repo style
  ("Patty Kim", "John K. Fetterman", "James H. Massey Jr") via the county
  parser's format_candidate. Governor/LtGov tickets ("SHAPIRO, JOSHUA D /
  DAVIS, AUSTIN L") are mapped to the Governor's full name (ENR convention).
- No vote-method breakdown on the site (Machine/MailIn pages are receipt
  checklists) → 7-column output, party empty (one combined table per contest).

Usage:
    uv run python parsers/pa_dauphin_general_2022_precinct_parser.py <output_csv>

Output: 2022/counties/20221108__pa__general__dauphin__precinct.csv
Verification: per-office precinct sums == site county totals == state ENR
anchor (2022/20221108__pa__general__county.csv, per-party match).
"""

import re
import sys
import time
from typing import Dict, List
import urllib.parse

import csv
import requests
from bs4 import BeautifulSoup
import importlib.util

# reuse the 2022-specific office normalization + candidate formatting
_spec = importlib.util.spec_from_file_location(
    "dauphin_county_2022", "parsers/pa_dauphin_general_2022_results_parser.py"
)
_county = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_county)
normalize_office = _county.normalize_office

BASE_URL = "https://www.dauphinc.org"
ELECTION_KEY = "34"  # 2022 General Election Results

# Governor ticket first-surnames -> full governor name (source prints
# "SHAPIRO, JOSHUA D / DAVIS, AUSTIN L" in precinct headers)
TICKET_FULL = {
    "shapiro": "Joshua D. Shapiro",
    "mastriano": "Douglas V. Mastriano",
    "hackenburg": "Jonathan Matthew Hackenburg",
    "digiulio": "Christina Pk Digiulio",
    "soloski": "Joseph P. Soloski",
}


def fmt_ticket(candidate: str, office: str) -> str:
    """Format a (possibly ticket) candidate header cell."""
    if "WRITE" in candidate.upper():
        return "Write Ins"
    sides = candidate.split("/")
    fmt = [_county.format_candidate(side) for side in sides]
    if office == "Governor":
        first = fmt[0].split()[0].lower().rstrip(".")
        full = TICKET_FULL.get(first)
        if full:
            return full
    return "/".join(fmt)


def fetch_race_list() -> List[Dict[str, str]]:
    """Fetch list of all races from the 2022 general main page."""
    print("Fetching race list...")
    url = f"{BASE_URL}/election/GeneralElection?key={ELECTION_KEY}"
    response = requests.get(url, timeout=60)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")

    races, seen = [], set()
    for link in soup.find_all("a", href=True):
        href = link["href"]
        if f"key={ELECTION_KEY}&race=" in href or f"key={ELECTION_KEY}&amp;race=" in href:
            href = href.replace("&amp;", "&")
            if href.startswith("/"):
                href = BASE_URL + href
            race_name = urllib.parse.unquote(href.split("race=")[-1])
            if race_name not in seen:
                seen.add(race_name)
                races.append({"url": href, "race_name": race_name})
    races = sorted(races, key=lambda r: r["race_name"])
    print(f"Found {len(races)} races")
    return races


def fetch_races_links(race_url: str) -> List[str]:
    """Return the /election/Races?key=34 precinct-page URLs linked off a race page."""
    response = requests.get(race_url, timeout=60)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")
    links = []
    seen = set()
    for link in soup.find_all("a", href=True):
        href = link["href"].replace("&amp;", "&")
        if "Races?key=" in href and f"key={ELECTION_KEY}" in href and "race=" in href:
            if href.startswith("/"):
                href = BASE_URL + href
            if href not in seen:
                seen.add(href)
                links.append(href)
    return links


def parse_precinct_table(table, race_name: str, office_text: str) -> List[Dict]:
    normalized_office, district = normalize_office(race_name, office_text)
    header_row = table.find("tr", class_="uppercase")
    if not header_row:
        return []
    candidates = []
    for cell in header_row.find_all("td", class_="padding"):
        candidate_text = cell.get_text(strip=True)
        if candidate_text:
            candidates.append(fmt_ticket(candidate_text, normalized_office))
    if not candidates:
        return []

    results: List[Dict] = []
    for row in table.find_all("tr", class_="font-weight-bolder"):
        cells = row.find_all("td", class_="padding")
        if not cells:
            continue
        precinct_span = cells[0].find("span") or cells[0]
        precinct_name = precinct_span.get_text(strip=True)
        if not precinct_name or "TOTAL" in precinct_name.upper():
            continue
        vote_cells = cells[1:]
        for i, candidate in enumerate(candidates):
            if i < len(vote_cells):
                vote_match = re.search(r"(\d+)", vote_cells[i].get_text(strip=True))
                if vote_match:
                    results.append(
                        {
                            "county": "Dauphin",
                            "precinct": precinct_name,
                            "office": normalized_office,
                            "district": district,
                            "party": "",
                            "candidate": candidate,
                            "votes": vote_match.group(1),
                        }
                    )
    return results


def fetch_precinct_results(race_name: str) -> List[Dict]:
    """Fetch and parse precinct-level results for one race."""
    race_url = f"{BASE_URL}/election/GeneralElection?key={ELECTION_KEY}&race={urllib.parse.quote(race_name)}"
    races_links = fetch_races_links(race_url)
    if not races_links:
        print(f"  (no precinct links on {race_name})")
        return []

    results: List[Dict] = []
    for precinct_url in sorted(races_links):
        offices = urllib.parse.unquote(precinct_url.split("race=")[-1])
        print(f"  Fetching precincts for: {offices}")
        try:
            response = requests.get(precinct_url, timeout=120)
            response.raise_for_status()
        except requests.RequestException as e:
            print(f"  Error fetching {precinct_url}: {e}")
            continue
        psoup = BeautifulSoup(response.text, "html.parser")
        for table in psoup.find_all("table", class_="padding"):
            # visible precinct grids carry "my_tab"; the site's hidden clone
            # and municipality-aggregate ("tab2") variants are skipped
            classes = table.get("class") or []
            if "my_tab" not in classes:
                continue
            results.extend(parse_precinct_table(table, race_name, offices))
        time.sleep(0.5)

    return results


def write_csv(results: List[Dict], output_path: str) -> None:
    fieldnames = ["county", "precinct", "office", "district", "party", "candidate", "votes"]
    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(results)


def main(argv: List[str]) -> int:
    if len(argv) != 2:
        print(f"Usage: python {argv[0]} <output_csv>")
        return 1
    output_csv = argv[1]

    races = fetch_race_list()
    all_results: List[Dict] = []
    for i, race in enumerate(races, 1):
        print(f"Processing race {i}/{len(races)}: {race['race_name']}")
        all_results.extend(fetch_precinct_results(race["race_name"]))

    print(f"\nWriting {len(all_results)} records to {output_csv}...")
    write_csv(all_results, output_csv)
    print("Done!")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
