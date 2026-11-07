#!/usr/bin/env python3
"""
Dauphin County, PA 2022 General Primary Results Builder

Dauphin County publishes no result files — its site (dauphinc.org) hosts live
results only, at key=32 (2022 primary, election-night vintage) and key=33
(GOP U.S. Senate recount). For 2024-2026-style coverage this builder mixes
two vintages the same way the Fulton 2024 primary build does:

- NAMED candidates for USS/GOV/LTG/USC/STS/STH come from the PA DOS certified
  primary precinct-returns bulk file (ElectionReturns_2022_Primary_
  PrecinctReturns.txt, county code 22) — its county sums tie the ENR anchor
  (2022/20220517__pa__primary__county.csv, post-recount) EXACTLY for all 48
  named Dauphin keys, including GOP U.S. Senate (Oz 8,950 / McCormick 9,342).
- Member of Democratic/Republican State Committee + per-contest write-in
  rows come from the county site (key=32), the only available source for
  those contests; they are election-night vintage: the GOP state-committee
  candidates sit 4-12 votes below the ENR anchor (canvass deltas); the DEM
  state-committee rows tie the anchor exactly. Known site-internal
  inconsistency: the DSC write-in rows do not tie out — the county page
  prints 564 (deduped from an identical-per-slot print) while summing the
  Races per-precinct write-in column yields 1,692; the RSC pair ties
  (610 == 610). Both views are kept as their source prints them.

Candidate spellings are taken from the ENR anchor (letters-only lookup).

Usage:
    uv run python parsers/pa_dauphin_primary_2022_results_parser.py \
        <DOS bulk file> <output_csv> [--verify]

Output: 2022/counties/20220517__pa__primary__dauphin__county.csv (7-col)
"""

import csv
import re
import sys
import time
import urllib.parse
from pathlib import Path
from typing import Dict, List
from collections import Counter

import requests
from bs4 import BeautifulSoup

BASE_URL = "https://www.dauphinc.org"
ELECTION_KEY = "32"          # 2022 primary
ELECTION_NAME = "2022 General Primary"
COUNTY_CODE = "22"

DOS_OFFICE = {
    '"USS"': "U.S. Senate",
    '"GOV"': "Governor",
    '"LTG"': "Lieutenant Governor",
    '"USC"': "U.S. House",
    '"STS"': "State Senate",
    '"STH"': "State House",
}

# site race pages that carry the state-pwide contests (for write-in rows)
STATEWIDE_SITE_RACES = [
    "US SENATOR", "GOVERNOR", "LIEUTENANT GOVERNOR",
    "REPRESENTATIVE IN CONGRESS", "STATE SEN 34",
    "HD 103", "HD 104", "HD 105", "HD 106", "HD 125",
]
STATE_COMMITTEE_RACES = ["STATE PARTY COMMITTEE"]

FIELDNAMES = ["county", "office", "district", "party", "candidate", "votes"]


def letters(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def anchor_name_map() -> Dict[str, str]:
    """letters-normalized (office, party, candidate) -> ENR spelled name."""
    out: Dict[str, str] = {}
    with open("2022/20220517__pa__primary__county.csv") as f:
        for r in csv.DictReader(f):
            if r["county"] != "Dauphin":
                continue
            out[(r["office"], r["party"], letters(r["candidate"]))] = r["candidate"]
    return out


def dos_rows(dos_path: str, name_map: Dict) -> List[Dict]:
    """Certified named-candidate rows for Dauphin from the DOS bulk file."""
    rows = []
    with open(dos_path) as f:
        for line in f:
            p = line.rstrip("\n").split(",")
            if len(p) < 16:
                continue
            if p[2].strip().strip('"') != COUNTY_CODE or p[1].strip().strip('"') != "P":
                continue
            office = DOS_OFFICE.get(p[8].strip())
            if not office:
                continue
            party = p[9].strip().strip('"')
            last, first, mid, sfx = (x.strip().strip('"') for x in p[11:15])
            cand = " ".join(x for x in (first, mid, last) if x) + (" " + sfx if sfx else "")
            cand = re.sub(r"\s+", " ", cand).strip()
            key = (office, party, letters(cand))
            spelled = name_map.get(key)
            if spelled:
                cand = spelled
            else:
                cand = " ".join(
                    (w.capitalize() if len(w) > 2 else w + "." if len(w) == 1 and w.isalpha() else w.capitalize())
                    for w in cand.split()
                )
            district = str(int(float(p[5]))) if float(p[5] or 0) > 0 else ""
            rows.append({
                "county": "Dauphin", "office": office, "district": district,
                "party": party, "candidate": cand, "votes": int(float(p[15])),
            })
    return rows


def fetch_races(url: str) -> List[Dict[str, str]]:
    response = requests.get(url, timeout=60)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")
    races, seen = [], set()
    for a in soup.find_all("a", href=True):
        href = a["href"].replace("&amp;", "&")
        if f"key={ELECTION_KEY}&race=" in href:
            if href.startswith("/"):
                href = BASE_URL + href
            name = urllib.parse.unquote(href.split("race=")[-1])
            if name not in seen:
                seen.add(name)
                races.append({"url": href, "race_name": name})
    return races


def parse_county_site_rows(races_url_list: List[str]) -> List[Dict]:
    """Parse county-level tblRace candidate rows (state committee + write-ins
    for the listed races) from the site's key=32 pages."""
    out: List[Dict] = []
    for race_name in races_url_list:
        url = f"{BASE_URL}/election/GeneralPrimary?key={ELECTION_KEY}&race={urllib.parse.quote(race_name)}"
        try:
            response = requests.get(url, timeout=60)
            response.raise_for_status()
        except requests.RequestException as e:
            print(f"  Error fetching {race_name}: {e}")
            continue
        soup = BeautifulSoup(response.text, "html.parser")
        for table in soup.find_all("table", id="tblRace"):
            office_header = table.find("span", class_="color-lightgrey font-weight-bolder")
            if not office_header:
                continue
            office_text = office_header.get_text(strip=True)
            for row in table.find_all("tr", class_="bg-white"):
                name_div = row.find("div", class_="col-xs-8")
                if not name_div:
                    continue
                strong = name_div.find("strong")
                if not strong:
                    continue
                if re.fullmatch(r"TOTAL:\s*", strong.get_text(strip=True)):
                    continue
                party_span = strong.find("span")
                raw_party = (party_span.get_text(strip=True) if party_span else "").upper()
                candidate_text = strong.get_text(strip=True)
                candidate_text = re.sub(r"^[A-Z/][A-Z/ ]+?\s*\.\s*", "", candidate_text).strip()
                if not candidate_text:
                    continue

                office, _dist = site_office(race_name, office_text)
                if office is None:
                    continue

                party = {"DEM": "DEM", "REP": "REP", "LIBRT": "LIB", "LIB": "LIB",
                         "GREEN PARTY": "GRN", "KEYSTONE": "KEY", "NONPARTISAN": ""}.get(raw_party, raw_party)

                if "WRITE-IN" in candidate_text.upper():
                    candidate = "Write Ins"
                else:
                    # state committee names: "BENNETT, PHYLLIS I" -> "Phyllis I. Bennett"
                    candidate = format_candidate(candidate_text)

                votes_div = row.find("div", class_="col-md-12")
                votes = ""
                if votes_div:
                    m = re.search(r"(\d+)", votes_div.get_text(strip=True))
                    if m:
                        votes = m.group(1)
                if candidate and votes:
                    out.append({
                        "county": "Dauphin", "office": office, "district": _dist,
                        "party": party, "candidate": candidate, "votes": int(votes),
                        "_table_id": id(table),
                        "_wi_raw": candidate_text.upper(),
                    })
        time.sleep(0.3)

    # The site repeats the same WRITE-IN row once per multi-slot write-in
    # ("WRITE-IN | 564" printed 7x for the DSC's 7 slots). Within one contest
    # table, dedupe identical (raw-name, votes) write-in pairs; distinct
    # numbered slots ("WRITE-IN1"/"WRITE-IN2") stay as separate rows that the
    # county aggregation sums into Write Ins.
    per_table: Dict[int, Dict] = {}
    kept = []
    for r in out:
        if not str(r.get("_wi_raw", "")).startswith("WRITE"):
            kept.append({k: v for k, v in r.items() if not k.startswith("_")})
            continue
        wi = per_table.setdefault(r["_table_id"], {})
        pairkey = (r["party"], r["_wi_raw"], r["votes"])
        first = pairkey not in wi
        wi[pairkey] = wi.get(pairkey, 0) + 1
        if first:
            kept.append({k: v for k, v in r.items() if not k.startswith("_")})
    return kept


def site_office(race_name: str, office_text: str):
    """Site office text -> (office, district); None to skip (municipal races)."""
    rn, ot = race_name.upper(), office_text.upper()
    if "UNITED STATES SENATOR" in ot or "US SENATOR" in rn:
        return ("U.S. Senate", "")
    if rn == "GOVERNOR" or ot.startswith("GOVERNOR"):
        return ("Governor", "")
    if "LIEUTENANT GOVERNOR" in ot:
        return ("Lieutenant Governor", "")
    if "REPRESENTATIVE IN CONGRESS" in ot:
        return ("U.S. House", "10")   # Dauphin wholly in CD-10 (ENR anchor)
    m = re.search(r"\bHD\s*-?\s*(\d+)", ot) or re.search(r"\bHD\s*-?\s*(\d+)", rn)
    if m:
        return ("State House", m.group(1))
    m = re.search(r"\bSD\s*-?\s*(\d+)", ot) or re.search(r"\bSD\s*-?\s*(\d+)", rn)
    if m or "SENATOR IN THE GENERAL ASSEMBLY" in ot:
        d = m.group(1) if m else ""
        return ("State Senate", d)
    if "STATE COMMITTEE" in ot:
        return ("Member of Democratic State Committee" if "DEMOCRATIC" in ot
                else "Member of Republican State Committee", "")
    return (None, "")


def format_candidate(name: str) -> str:
    """'BENNETT, PHYLLIS I' -> 'Phyllis I. Bennett' (repo style)."""
    s = name.strip().rstrip(",")
    parts = [p.strip() for p in s.split(",") if p.strip()]
    last_part, given = parts[0], parts[1] if len(parts) > 1 else ""
    surname_tokens = last_part.split()
    suffix = ""
    if surname_tokens and surname_tokens[-1].upper().rstrip(".") in ("JR", "SR", "II", "III", "IV"):
        suffix = surname_tokens.pop().rstrip(".").capitalize()
    last = " ".join(w.title() for w in surname_tokens)
    out = []
    for w in given.split():
        if len(w) == 1 and w.isalpha():
            out.append(w + ".")
        elif w.upper().rstrip(".") in ("JR", "SR", "II", "III", "IV"):
            out.append(w.upper().rstrip(".").capitalize())
        else:
            out.append(w.title())
    pieces = out + [last] + ([suffix] if suffix else [])
    return " ".join(pieces)


def verify(county_rows: List[Dict]) -> None:
    """Named DOS rows must match the ENR anchor exactly. Site-sourced rows
    (state committee, write-ins) are reported separately: state-committee
    candidates carry election-night site votes (a few below the certified
    anchor, same vintage caveat as Susquehanna 2022 general); the anchor's
    state-committee district field holds the county code (22) — the repo
    convention leaves district empty here."""
    anchor: Dict = {}
    with open("2022/20220517__pa__primary__county.csv") as f:
        for r in csv.DictReader(f):
            if r["county"] != "Dauphin":
                continue
            dist = "" if "State Committee" in r["office"] else r["district"]
            key = (r["office"], dist, r["party"], letters(r["candidate"]))
            anchor[key] = anchor.get(key, 0) + int(r["votes"])
    ours: Dict = {}
    for r in county_rows:
        key = (r["office"], "" if "State Committee" in r["office"] else r["district"],
               r["party"], letters(r["candidate"]))
        ours[key] = ours.get(key, 0) + r["votes"]
    site_keys, exact, delta = 0, 0, []
    for key, votes in sorted(ours.items()):
        a = anchor.get(key)
        if a is None:
            site_keys += 1
            continue
        if votes == a:
            exact += 1
        else:
            delta.append((key, votes, a))
    print(f"named keys exact vs ENR anchor: {exact}/{sum(1 for k in ours if k in anchor)}")
    print(f"site-only (write-in) keys: {site_keys}")
    if delta:
        print(f"DELTAS vs anchor: {len(delta)}")
        for d in delta:
            print("  ", d)


def main(argv: List[str]) -> int:
    args = [a for a in argv[1:] if a != "--verify"]
    if not 1 <= len(args) <= 2:
        print(f"Usage: {argv[0]} <dos_bulk.txt> <output.csv> [--verify]")
        return 1
    dos_path, out_csv = args
    verify_mode = "--verify" in argv

    name_map = anchor_name_map()
    named = dos_rows(dos_path, name_map)
    print(f"DOS named rows: {len(named)}")

    site = parse_county_site_rows(STATE_COMMITTEE_RACES + STATEWIDE_SITE_RACES)
    # The DOS file already covers every named candidate of the six statewide
    # office families (certified), so from the site keep ONLY the state
    # committee contests and write-in rows (nickname spellings like
    # "Rick Coplen" would otherwise duplicate the DOS rows).
    site_rows = [
        r for r in site
        if "State Committee" in r["office"] or r["candidate"] == "Write Ins"
    ]
    print(f"site rows (state committee + write-ins): {len(site_rows)}")

    rows = named + site_rows
    # the DOS rows are per-precinct; the county file aggregates to
    # one row per (office, district, party, candidate)
    aggregated: Dict = {}
    for r in rows:
        key = (r["office"], r["district"], r["party"], r["candidate"])
        aggregated[key] = aggregated.get(key, 0) + r["votes"]
    order = {o: i for i, o in enumerate(
        ["U.S. Senate", "Governor", "Lieutenant Governor", "U.S. House",
         "State Senate", "State House",
         "Member of Democratic State Committee", "Member of Republican State Committee"])}
    with open(out_csv, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        writer.writeheader()
        for (office, district, party, cand), votes in sorted(
            aggregated.items(),
            key=lambda kv: (order.get(kv[0][0], 99), kv[0][2], kv[0][1] or "", kv[0][3]),
        ):
            writer.writerow({
                "county": "Dauphin", "office": office, "district": district,
                "party": party, "candidate": cand, "votes": votes,
            })
    print(f"Wrote {len(aggregated)} rows to {out_csv}")

    if verify_mode:
        verify(rows)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
