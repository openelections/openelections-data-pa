#!/usr/bin/env python3
"""Parse the Greene County 2023 General Election "Final Results by Precinct"
CountySuite HTML report into the standard OpenElections precinct CSV.

Usage: python pa_greene_general_2023_results_parser.py <input.html> <output.csv> [--county Greene]

The HTML is the county's election-results web page (one file, no network
access needed). Each precinct is an <h3> followed by a sequence of
<h1>contest</h1> / <div id="results_module"> blocks; every candidate row is
its own <table> with name/party in the first cell, percent in the second and
votes in the third.
"""

import argparse
import csv
import re
import sys

from bs4 import BeautifulSoup

PARTY_MAP = {
    "Democrat": "DEM",
    "Republican": "REP",
    "Democrat / Republican": "D/R",
    "Libertarian": "LBR",
    "Green": "GRN",
    "Constitution": "CON",
    "No Party Specified": "",
}

# Contest names that are already in standard form (after light cleanup).
STATEWIDE = {
    "Justice Of The Supreme Court": "Justice of the Supreme Court",
    "Judge Of The Superior Court": "Judge of the Superior Court",
    "Judge Of The Commonwealth Court": "Judge of the Commonwealth Court",
}

COUNTYWIDE = {
    "County Commissioner (Vote for 2)": "County Commissioner",
    "Clerk Of Courts": "Clerk of Courts",
    "County Controller": "County Controller",
    "District Attorney": "District Attorney",
    "Prothonotary": "Prothonotary",
    "Register & Recorder": "Register & Recorder",
    "County Treasurer": "County Treasurer",
}

RETENTION = {
    "Superior Court Retention Election Question (Jack Panella)":
        "Superior Court Retention Election Question (Jack Panella)",
    "Superior Court Retention Election Question (Victor P Stabile)":
        "Superior Court Retention Election Question (Victor P Stabile)",
}

TERM_RE = re.compile(r"^(.*?)-\s*(\d+)\s*yr\s*(.*)$", re.I)
TERM_LABEL = {"2": "2 Year", "4": "4 Year", "6": "6 Year"}


def norm_office(contest):
    """Return (office, district) for a contest header."""
    c = re.sub(r"\s+", " ", contest).strip()
    c = re.sub(r"\s*\(Vote for \d+\)\s*$", "", c, flags=re.I)
    if c in STATEWIDE or c in COUNTYWIDE:
        return STATEWIDE.get(c, COUNTYWIDE.get(c)), ""
    if c in RETENTION:
        return RETENTION[c], ""
    m = re.match(r"^Magisterial District Judge (\d{2}-\d-\d+)", c, re.I)
    if m:
        return "Magisterial District Judge", m.group(1)

    # "<Office> - At Large <Municipality>" (school director at-large seats)
    m = re.match(r"^(.*?)\s*-\s*At Large\s+(.*)$", c, re.I)
    if m:
        return f"{m.group(1).strip()} At Large", m.group(2).strip()

    # Local races: "<Office> - <N>yr <Municipality>" or "<Office> <Municipality>"
    m = TERM_RE.match(c)
    if m:
        base, term, rest = m.group(1).strip(), m.group(2), m.group(3).strip()
        label = TERM_LABEL.get(term, f"{term} Year")
        # School Director At Large keeps "At Large" on the office so it does not
        # collide with the region seats of the same school district.
        m2 = re.match(r"^(.*?)\s+At Large\s+(.*)$", rest, re.I)
        if "school director" in base.lower() and m2:
            return f"{base} At Large", m2.group(1).strip()
        return f"{base} ({label})", rest

    # "<Office> <Municipality>" (Constable Aleppo, Mayor X Boro, Tax Collector Y)
    for off in ("Judge Of Elections", "Inspector Of Elections", "Constable",
                "Mayor", "Tax Collector"):
        if c.lower().startswith(off.lower() + " "):
            rest = c[len(off):].strip()
            return off.replace("Of", "of"), rest

    m = re.match(r"^Council Member - (\d+)yr (.*?)( \(Vote for \d+\))?$", c, re.I)
    if m:
        label = TERM_LABEL.get(m.group(1), f"{m.group(1)} Year")
        return f"Council Member ({label})", m.group(2).strip()

    # Fallback: use the header as the office, no district.
    return c, ""


def parse_candidate_cell(cell):
    """Return (name, party) from the first <td> of a candidate row."""
    h1 = cell.find("h1")
    h2 = cell.find("h2")
    first = h1.get_text(" ", strip=True) if h1 else ""
    last = ""
    party = ""
    if h2:
        span = h2.find("span", class_="party_name")
        if span:
            party_raw = span.get_text(strip=True).strip("()")
            party = PARTY_MAP.get(party_raw, party_raw)
            span.extract()
        last = h2.get_text(" ", strip=True)
    name = re.sub(r"\s+", " ", f"{first} {last}").strip()
    return name, party


def clean_candidate(name, contest):
    """Map special 'candidate' rows to the standard metadata/write-in forms."""
    n = re.sub(r"\s+", " ", name).strip()
    if n.upper().startswith("WRITE-IN"):
        return "Write-ins"
    # Ballots Cast rows: the contest header carries the real label; the
    # candidate cell repeats it ("Ballots Cast - Total (Ballots Cast Total)").
    if n.startswith("Ballots Cast"):
        return ""
    return n


def parse_html(path, county="Greene"):
    with open(path, encoding="utf-8", errors="replace") as f:
        soup = BeautifulSoup(f.read(), "html.parser")

    rows = []
    precincts = []
    for h3 in soup.find_all("h3"):
        precinct = re.sub(r"\s+", " ", h3.get_text(" ", strip=True))
        precincts.append(precinct)
        node = h3.find_next_sibling()
        while node is not None and node.name != "h3":
            if node.name == "h1":
                contest = node.get_text(" ", strip=True)
                office, district = norm_office(contest)
                mod = node.find_next_sibling("div", id="results_module")
                if mod is None:
                    node = node.find_next_sibling()
                    continue
                for table in mod.find_all("table"):
                    for tr in table.find_all("tr"):
                        tds = tr.find_all("td", recursive=False)
                        if len(tds) < 3:
                            continue
                        if tds[0].get_text(strip=True) == "Candidate":
                            continue  # header row
                        raw_name, party = parse_candidate_cell(tds[0])
                        votes = re.sub(r"[^0-9]", "", tds[2].get_text(strip=True))
                        name = clean_candidate(raw_name, contest)
                        # Ballots Cast rows -> office renaming
                        row_office, row_district = office, district
                        if raw_name.strip().startswith("Ballots Cast"):
                            n = re.sub(r"\s+", " ", raw_name)
                            if "Blank" in n:
                                row_office = "Ballots Cast - Blank"
                            else:
                                row_office = "Ballots Cast"
                            row_district = ""
                        rows.append({
                            "county": county,
                            "precinct": precinct,
                            "office": row_office,
                            "district": row_district,
                            "party": party if name else "",
                            "candidate": name,
                            "votes": votes,
                            "election_day": "",
                            "mail": "",
                            "provisional": "",
                        })
            node = node.find_next_sibling()

    # Some contests (e.g. the two West Greene at-large school director seats)
    # list two separate WRITE-IN lines under one header; merge rows that share
    # a key so the precinct CSV never contains duplicates.
    merged = {}
    order = []
    for r in rows:
        key = (r["precinct"], r["office"], r["district"], r["party"],
               r["candidate"])
        if key in merged:
            merged[key]["votes"] = str(int(merged[key]["votes"]) +
                                       int(r["votes"]))
        else:
            merged[key] = r
            order.append(key)
    rows = [merged[k] for k in order]
    return rows, precincts


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("input_path")
    ap.add_argument("output_path")
    ap.add_argument("--county", default="Greene")
    args = ap.parse_args()

    rows, _ = parse_html(args.input_path, args.county)
    cols = ["county", "precinct", "office", "district", "party", "candidate",
            "votes", "election_day", "mail", "provisional"]
    with open(args.output_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)
    print(f"wrote {len(rows)} rows to {args.output_path}")


if __name__ == "__main__":
    main()