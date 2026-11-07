#!/usr/bin/env python3
"""
Dauphin County, PA 2022 General Election Results Scraper (county-level)

Adapted from pa_dauphin_general_2025_scraper.py for the 2022 general election
(site key=34). Dauphin County does not publish downloadable result files — the
only source is the county's live results site (dauphinc.org), so this scrapes
the county-level per-office tables and writes the OpenElections county CSV.

Usage:
    uv run python parsers/pa_dauphin_general_2022_results_parser.py <output_csv>

Output: 2022/counties/20221108__pa__general__dauphin__county.csv
Verification: candidate sums are the site's printed countywide totals; check
against 2022/20221108__pa__general__county.csv (state ENR anchor).
"""

import re
import sys
import time
from typing import Dict, List, Optional, Tuple

import urllib.parse

import requests
from bs4 import BeautifulSoup

BASE_URL = "https://www.dauphinc.org"
ELECTION_KEY = "34"  # 2022 General Election Results
ELECTION_URL = f"{BASE_URL}/election/GeneralElection?key={ELECTION_KEY}"

# Full name canonicalization for ticket rows / candidate surnames, anchored on
# the names the state ENR county file prints (2022/20221108__pa__general__county.csv).
# The site prints Governor+LtGov tickets as "SHAPIRO/DAVIS"; repo convention is
# the Governor's full name alone.
CANDIDATE_NAMES = {
    ("Governor", "DEM"): "Joshua D. Shapiro",
    ("Governor", "REP"): "Douglas V. Mastriano",
    ("Governor", "LIB"): "Jonathan Matthew Hackenburg",
    ("Governor", "GRN"): "Christina Pk Digiulio",
    ("Governor", "KEY"): "Joseph P. Soloski",  # site prints the ticket "SOLOSKI/SHULTZ"
}


def format_candidate(name: str) -> str:
    """Convert site "LAST, FIRST MIDDLE [SUFFIX]" to repo style
    ("Patty Kim", "David D. Buell", "James H. Massey Jr"): title-case,
    initials get periods, suffixes move to the end without a period."""
    s = name.strip().rstrip(",")
    if "," not in s:
        # already given-then-surname: title-case but preserve initials/suffixes
        toks = s.split()
        out = []
        for w in toks:
            if len(w) == 1:
                out.append(w + ".")
            elif w.upper().rstrip(".") in ("JR", "SR", "II", "III", "IV"):
                out.append(w.upper().rstrip(".").capitalize())
            else:
                out.append(w.title())
        return " ".join(out)
    parts = [p.strip() for p in s.split(",") if p.strip()]
    last_part, given = parts[0], parts[1] if len(parts) > 1 else ""
    surname_tokens = last_part.split()
    suffix = ""
    if surname_tokens and surname_tokens[-1].upper().rstrip(".") in ("JR", "SR", "II", "III", "IV"):
        suffix = surname_tokens.pop().rstrip(".").capitalize()
    last = " ".join(w.title() for w in surname_tokens)
    out = []
    for w in given.split():
        if len(w) == 1:
            out.append(w + ".")
        elif w.upper().rstrip(".") in ("JR", "SR", "II", "III", "IV"):
            out.append(w.upper().rstrip(".").capitalize())
        else:
            out.append(w.title())
    pieces = out + [last] + ([suffix] if suffix else [])
    return " ".join(pieces)


def fetch_race_list() -> List[Dict[str, str]]:
    """Fetch list of all race URLs from the 2022 general main page."""
    print("Fetching race list...")
    response = requests.get(ELECTION_URL, timeout=60)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")

    races: List[Dict[str, str]] = []
    seen = set()
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


def normalize_office(race_name: str, office_text: str) -> Tuple[str, str]:
    """Normalize the election-site race/office text to (office, district)."""
    rn = race_name.strip().upper()
    ot = office_text.strip().upper()

    if "US SENATOR" in rn or "UNITED STATES SENATOR" in ot:
        return ("U.S. Senate", "")
    if "GOVERNOR" in rn:  # GOVERNOR AND LT GOVERNOR
        return ("Governor", "")
    if rn.startswith("REPRESENTATIVE IN CONGRESS"):
        return ("U.S. House", "10")  # Dauphin sits wholly in CD-10 (anchor)
    m = re.search(r"\bHD\s*-?\s*(\d+)", rn) or re.search(r"\bHD\s*-?\s*(\d+)", ot)
    if m:
        return ("State House", m.group(1))
    m = re.search(r"\bSD\s*-?\s*(\d+)", rn) or re.search(r"\bSD\s*-?\s*(\d+)", ot)
    if m or "SENATOR IN THE GENERAL ASSEMBLY" in ot:
        dist = m.group(1) if m else ""
        return ("State Senate", dist)
    # local races fallback
    return (office_text.strip().title(), "")


PARTY_NAMES = {
    "DEM": "DEM",
    "REPUBLICAN": "REP",
    "REP": "REP",
    "LIBERTARIAN": "LIB",
    "LIBRT": "LIB",
    "LIB": "LIB",
    "GREEN PARTY": "GRN",
    "GREEN": "GRN",
    "GRN": "GRN",
    "KEYSTONE": "KEY",
    "KEYSTONE PARTY": "KEY",
    "KEY": "KEY",
    "CONSTITUTION": "CON",
    "NONPARTISAN": "",  # write-in rows
}


def parse_race_page_race_tables(race: Dict[str, str]) -> List[Dict]:
    print(f"Fetching: {race['race_name']}")
    response = requests.get(race["url"], timeout=60)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")

    results: List[Dict] = []
    for table in soup.find_all("table", id="tblRace"):
        office_header = table.find("span", class_="color-lightgrey font-weight-bolder")
        if not office_header:
            continue
        office_text = office_header.get_text(strip=True)
        office_text = re.sub(r"\s*\(\s*Vote\s+For\s+\d+\s*\)", "", office_text, flags=re.IGNORECASE)

        office, district = normalize_office(race["race_name"], office_text)

        for row in table.find_all("tr", class_="bg-white"):
            name_div = row.find("div", class_="col-xs-8")
            if not name_div:
                continue
            strong = name_div.find("strong")
            if not strong:
                continue
            party_span = strong.find("span")
            raw_party = party_span.get_text(strip=True) if party_span else ""
            candidate_text = strong.get_text(strip=True)
            # strip leading party prefix ("DEM . ", "GREEN PARTY . ", ...)
            candidate_text = re.sub(r"^[A-Z/][A-Z/ ]+?\s*\.\s*", "", candidate_text)
            if re.fullmatch(r"TOTAL:\s*", strong.get_text(strip=True)):
                continue
            candidate = candidate_text.strip()
            if not candidate:
                continue

            party = PARTY_NAMES.get(raw_party.upper(), raw_party.upper())
            if "WRITE-IN" in candidate.upper():
                candidate = "Write Ins"

            if raw_party.upper() == "NONPARTISAN":
                party = ""

            if office == "Governor":
                # ticket surnames -> ENR full names by ballot party
                ticket = candidate.split(". ", 1)[-1] if ". " in candidate else candidate
                # first surname alone can't disambiguate; use the party
                full = CANDIDATE_NAMES.get((office, party or "?"))
                if full:
                    candidate = full
            else:
                candidate = format_candidate(candidate)

            votes_div = row.find("div", class_="col-md-12", style=lambda x: x and "margin-top:-10px" in x)
            if not votes_div:
                votes_div = row.find("div", class_="col-md-12")
            votes = ""
            if votes_div:
                votes_text = votes_div.get_text(strip=True)
                vote_match = re.search(r"(\d+)", votes_text)
                if vote_match:
                    votes = vote_match.group(1)

            if candidate and votes:
                results.append(
                    {
                        "county": "Dauphin",
                        "office": office,
                        "district": district,
                        "party": party,
                        "candidate": candidate,
                        "votes": votes,
                        "election_day": "",
                        "mail": "",
                        "provisional": "",
                    }
                )
    return results


def write_csv(results: List[Dict], output_path: str) -> None:
    fieldnames = [
        "county", "office", "district", "party",
        "candidate", "votes", "election_day", "mail", "provisional",
    ]
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
        all_results.extend(parse_race_page_race_tables(race))
        time.sleep(0.5)

    print(f"\nWriting {len(all_results)} records to {output_csv}...")
    write_csv(all_results, output_csv)
    print("Done!")
    return 0


if __name__ == "__main__":
    import csv

    sys.exit(main(sys.argv))
