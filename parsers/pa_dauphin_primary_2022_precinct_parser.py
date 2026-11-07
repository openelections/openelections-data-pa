#!/usr/bin/env python3
"""
Dauphin County, PA 2022 General Primary Precinct Results Builder

Companion to pa_dauphin_primary_2022_results_parser.py (see its docstring for
the vintage decision): named-candidate rows for USS/GOV/LTG/USC/STS/STH come
from the PA DOS certified primary precinct-returns bulk file (county code 22,
ties the post-recount ENR anchor exactly); Member of State Committee,
municipal committee (Committeeman/Committeewoman) races and per-precinct
write-in rows come from the county site (dauphinc.org key=32, election-night
vintage) — the only source carrying those contests.

Site/DOS precinct crosswalk: DOS municipality names ("CONEWAGO D 1") differ
from the site's precinct names ("CITY--1ST WARD, 1ST PRECINCT"), so each DOS
precinct code is mapped to a site precinct name by exact per-precinct vote
vectors over every contest carried by both sources; every DOS code must match
exactly one site name (the run prints the crosswalk summary).

Usage:
    uv run python parsers/pa_dauphin_primary_2022_precinct_parser.py \
        <DOS bulk file> <output_csv> [--keep-code-names]

Output: 2022/counties/20220517__pa__primary__dauphin__precinct.csv (7-col)
"""

import csv
import re
import sys
import time
import urllib.parse
from pathlib import Path
from typing import Dict, List, Set
from collections import defaultdict

import requests
from bs4 import BeautifulSoup
import importlib.util

_here = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location(
    "dauphin_primary_2022_results", str(_here / "pa_dauphin_primary_2022_results_parser.py")
)
_c = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_c)

BASE_URL = "https://www.dauphinc.org"
ELECTION_KEY = "32"
COUNTY_CODE = "22"

# Governor ticket first-surnames -> full governor name (site prints
# "SHAPIRO, JOSHUA D / DAVIS, AUSTIN L" in precinct headers)
TICKET_FULL = {
    "shapiro": "Joshua D. Shapiro",
    "mastriano": "Douglas V. Mastriano",
    "hackenburg": "Jonathan Matthew Hackenburg",
    "digiulio": "Christina Pk Digiulio",
    "soloski": "Joseph P. Soloski",
}


def fmt_ticket(candidate: str, office: str) -> str:
    if "WRITE" in candidate.upper():
        return "Write Ins"
    if office != "Governor":
        return _c.format_candidate(candidate)
    first = _c.format_candidate(candidate.split("/")[0])
    full = TICKET_FULL.get(first.split()[0].lower().rstrip("."))
    return full or _c.format_candidate(candidate)


def race_party(office_text: str) -> str:
    """Party suffix on the site office name, e.g. 'GOVERNOR - DEM (DEM)' or
    'UNITED STATES SENATOR - DEM'."""
    m = re.search(
        r"\s-\s*(LIBRT|LIB|DEM|REP)\s*(?:\(\s*(?:LIBRT|LIB|DEM|REP)\s*\))?\s*$",
        office_text.strip(),
    )
    if m:
        return {"LIBRT": "LIB", "LIB": "LIB"}.get(m.group(1), m.group(1))
    if "DEMOCRATIC STATE COMMITTEE" in office_text.upper():
        return "DEM"
    if "REPUBLICAN STATE COMMITTEE" in office_text.upper():
        return "REP"
    return ""


FIELDNAMES = ["county", "precinct", "office", "district", "party", "candidate", "votes"]


# ---------------------------------------------------------------- DOS rows
def dos_rows(dos_path: str, name_map: Dict) -> List[Dict]:
    """Certified per-precinct named-candidate rows for Dauphin."""
    out = []
    with open(dos_path) as f:
        for line in f:
            p = line.rstrip("\n").split(",")
            if len(p) < 16:
                continue
            if p[2].strip().strip('"') != COUNTY_CODE or p[1].strip().strip('"') != "P":
                continue
            office = _c.DOS_OFFICE.get(p[8].strip())
            if not office:
                continue
            party = p[9].strip().strip('"')
            last, first, mid, sfx = (x.strip().strip('"') for x in p[11:15])
            cand = " ".join(x for x in (first, mid, last) if x) + (" " + sfx if sfx else "")
            cand = re.sub(r"\s+", " ", cand).strip()
            spelled = name_map.get((office, party, _c.letters(cand)))
            if spelled:
                cand = spelled
            else:
                cand = _c.format_candidate(
                    f"{last}, {first}" + (f" {mid}" if mid else "")
                )
            # district: office-distinct row fields (candidate district 5;
            # usc/sd/shd at 18/19/20)
            district = ""
            if office == "U.S. House" and p[18].strip().strip('"'):
                district = str(int(float(p[18])))
            elif office == "State Senate" and p[19].strip().strip('"'):
                district = str(int(float(p[19])))
            elif office == "State House" and p[20].strip().strip('"'):
                district = str(int(float(p[20])))
            out.append({
                "precinct_code": p[3].strip().strip('"'),
                "county": "Dauphin", "office": office, "district": district,
                "party": party, "candidate": cand, "votes": int(float(p[15])),
            })
    return out


# ---------------------------------------------------------------- site rows
def fetch_race_list() -> List[Dict[str, str]]:
    print("Fetching race list...")
    url = f"{BASE_URL}/election/GeneralPrimary?key={ELECTION_KEY}"
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
    races = sorted(races, key=lambda r: r["race_name"])
    print(f"Found {len(races)} race pages")
    return races


def site_precinct_rows(race_pages: List[Dict[str, str]]) -> List[Dict]:
    """Scrape my_tab precinct grids from every Races page linked off every
    race page (statewide offices, state committee, municipal committee)."""
    out: List[Dict] = []
    seen_races_pages: Set[str] = set()
    for i, race in enumerate(race_pages, 1):
        try:
            response = requests.get(race["url"], timeout=60)
            response.raise_for_status()
        except requests.RequestException as e:
            print(f"  Race {race['race_name']}: {e}")
            continue
        soup = BeautifulSoup(response.text, "html.parser")
        races_links = []
        for a in soup.find_all("a", href=True):
            href = a["href"].replace("&amp;", "&")
            if "Races?key=" in href and f"key={ELECTION_KEY}" in href and "race=" in href:
                if href.startswith("/"):
                    href = BASE_URL + href
                if href not in seen_races_pages:
                    seen_races_pages.add(href)
                    races_links.append(href)
        for precinct_url in sorted(races_links):
            office_text = urllib.parse.unquote(precinct_url.split("race=")[-1])
            office, district = _c.site_office(race["race_name"], office_text)
            contest_party = race_party(office_text)
            if office is None:
                # municipal committee race: "DEMOCRATIC COMMITTEEMAN-BERRYSBURG (DEM)"
                mm = re.match(
                    r"(DEMOCRATIC|REPUBLICAN) (COMMITTEEMAN|COMMITTEEWOMAN)-(.+?)\s*\((DEM|REP)\)$",
                    office_text,
                )
                if not mm:
                    print(f"    SKIPPED unparsed race text: {office_text!r}")
                    continue
                party_word, seat, muni = mm.group(1), mm.group(2), mm.group(3)
                contest_party = "DEM" if party_word == "DEMOCRATIC" else "REP"
                office = f"{party_word.capitalize()} {seat.title()} - {muni.title()}"
                district = ""
            print(f"  [{i}/{len(race_pages)}] {office_text} -> {office}")
            try:
                response = requests.get(precinct_url, timeout=120)
                response.raise_for_status()
            except requests.RequestException as e:
                print(f"    Error fetching {precinct_url}: {e}")
                continue
            psoup = BeautifulSoup(response.text, "html.parser")
            for table in psoup.find_all("table", class_="padding"):
                classes = table.get("class") or []
                if "my_tab" not in classes:
                    continue
                header_row = table.find("tr", class_="uppercase")
                if not header_row:
                    continue
                candidates = []
                for cell in header_row.find_all("td", class_="padding"):
                    ctext = cell.get_text(strip=True)
                    if ctext:
                        candidates.append(ctext)
                if not candidates:
                    continue
                for row in table.find_all("tr", class_="font-weight-bolder"):
                    cells = row.find_all("td", class_="padding")
                    if not cells:
                        continue
                    precinct_name = (cells[0].find("span") or cells[0]).get_text(strip=True)
                    if not precinct_name or "TOTAL" in precinct_name.upper():
                        continue
                    for ci, cand_raw in enumerate(candidates):
                        if ci >= len(cells) - 1:
                            continue
                        vm = re.search(r"(\d+)", cells[ci + 1].get_text(strip=True))
                        if not vm:
                            continue
                        if "WRITE" in cand_raw.upper():
                            cand = "Write Ins"
                        else:
                            cand = fmt_ticket(cand_raw, office)
                        out.append({
                            "county": "Dauphin", "precinct": precinct_name,
                            "office": office, "district": district,
                            "party": contest_party,
                            "candidate": cand, "votes": int(vm.group(1)),
                        })
        time.sleep(0.3)
    return out


def surname_of(name: str) -> str:
    """Comparable surname token: drop honorific suffixes, take the last word
    ("James H. Massey Jr" -> "Massey")."""
    toks = name.replace(".", "").split()
    while toks and toks[-1].upper() in ("JR", "SR", "II", "III", "IV"):
        toks.pop()
    return toks[-1].upper() if toks else ""


def _muni_match(site_name: str, muni_label: str) -> bool:
    """Compare a site precinct name ("WEST HANOVER TWP--5TH PRECINCT") with a
    DOS municipality label ("WEST HANOVER D 5") by their leading tokens."""
    s = re.sub(r"\d+(st|nd|rd|th)\b", "", site_name.lower(), flags=re.I)
    s = re.sub(r"[^a-z ]", " ", s)
    s = set(re.sub(r"\s+", " ", re.sub(r"twp|township|boro|borough|precinct", "", s)).split())
    m = re.sub(r"\d+(\s|$)", " ", muni_label.lower())
    m = set(re.sub(r"\s+", " ", re.sub(r"twp|township|boro|borough|precinct|\bb\b|\bd\b|\bw\b|\bp\b", "", m)).split())
    return bool(s & m) and len(s | m) <= 8


def build_crosswalk(dos: List[Dict], site: List[Dict], dos_path: str = "") -> Dict[str, str]:
    """Map DOS precinct_code -> site precinct name via exact vote vectors.
    Vector keys: (office, district, party, SURNAME) — the site prints shorter
    candidate forms ("SHAPIRO, JOSH") than the anchor spellings ("Joshua D.
    Shapiro"), so full names don't join but surnames do."""
    def key(r):
        return (r["office"], r["district"], r["party"], surname_of(r["candidate"]))

    dos_keys = {key(r) for r in dos}
    vec_dos: Dict[str, Dict] = defaultdict(dict)
    vec_site: Dict[str, Dict] = defaultdict(dict)
    for r in dos:
        vec_dos[r["precinct_code"]][key(r)] = r["votes"]
    for r in site:
        if r["candidate"] == "Write Ins" or "Committee" in r["office"]:
            continue
        if key(r) not in dos_keys:
            continue
        vec_site[r["precinct"]][key(r)] = r["votes"]
    site_by_vec: Dict[tuple, List[str]] = defaultdict(list)
    for name, vec in vec_site.items():
        sig = tuple(sorted((k, v) for k, v in vec.items() if v != 0))
        site_by_vec[sig].append(name)
    crosswalk: Dict[str, str] = {}
    problems = []
    used: Set[str] = set()
    for code, vec in vec_dos.items():
        sig = tuple(sorted((k, v) for k, v in vec.items() if v != 0))
        matches = [m for m in site_by_vec.get(sig, []) if m not in used]
        if len(matches) == 1:
            crosswalk[code] = matches[0]
            used.add(matches[0])
    # tier 2: election-night vs certified per-precinct deltas; take the
    # unique nearest site vector within a small Manhattan distance
    for code, vec in vec_dos.items():
        if code in crosswalk:
            continue
        dos_vec = {k: v for k, v in vec.items() if v != 0}
        best, best_d, ties = None, None, 0
        for name, svec in vec_site.items():
            if name in used:
                continue
            d = 0
            for k, v in dos_vec.items():
                d += abs(v - svec.get(k, 0))
            for k, v in svec.items():
                if k not in dos_vec:
                    d += v
            if best_d is None or d < best_d:
                best, best_d, ties = name, d, 1
            elif d == best_d:
                ties += 1
        if best is not None and ties == 1 and best_d <= 25:
            crosswalk[code] = best
            used.add(best)
    # tier 3: ordinal fallback for the residual codes — the site's own party
    # grids disagree for a handful of precincts (West Hanover 5th/6th: the
    # DEM grids carry D6's certified votes under "5TH" and the REP grids carry
    # D5's), so no consistent vote-vector match exists for them. Follow the
    # DOS labels ("WEST HANOVER D n" -> "...--NTH PRECINCT").
    dos_muni = {}
    with open(dos_path, newline="", encoding="latin-1") as fh:
        for row in csv.reader(fh):
            if len(row) >= 27 and row[2] == COUNTY_CODE and row[1] == "P":
                dos_muni[row[3]] = (row[22], row[23], row[24], row[25], row[26])
    site_names = set(vec_site)
    for code in list(vec_dos):
        if code in crosswalk:
            continue
        muni_fields = dos_muni.get(code)
        if not muni_fields:
            continue
        muni_label = " ".join(x for x in (muni_fields[0], muni_fields[2], muni_fields[4]) if x.strip())
        om = re.search(r"\bD\s*(\d+)\b", muni_label.upper())
        if om:
            ordinal = int(om.group(1))
            ORD = {1: "1ST", 2: "2ND", 3: "3RD", 4: "4TH", 5: "5TH", 6: "6TH",
                   7: "7TH", 8: "8TH", 9: "9TH", 10: "10TH", 11: "11TH",
                   12: "12TH", 13: "13TH"}
            for suffix in (f"--{ORD.get(ordinal, str(ordinal)+'TH')} PRECINCT", f"--{ordinal}"):
                for name in site_names:
                    base = name.split("--")[0].replace(" TWP", " TWP").upper().replace("TOWNSHIP", "TWP")
                    muni_base = muni_label.upper().replace(" D ", " TWP D ").upper()
                    if name.upper().endswith(suffix) and _muni_match(name, muni_label):
                        crosswalk[code] = name
                        used.add(name)
                        break
                if code in crosswalk:
                    break
        if code not in crosswalk:
            problems.append((code, 0, "ordinal-fallback failed"))
    print(f"crosswalk: {len(crosswalk)}/{len(vec_dos)} DOS codes matched uniquely")
    if problems:
        print(f"UNMATCHED DOS codes: {len(problems)}; first few:")
        for p in problems[:6]:
            print("  ", p)
    return crosswalk


def main(argv: List[str]) -> int:
    args = [a for a in argv[1:] if a != "--keep-code-names"]
    if not 1 <= len(args) <= 2:
        print(f"Usage: {argv[0]} <dos_bulk.txt> <output.csv>")
        return 1
    dos_path, out_csv = args

    name_map = _c.anchor_name_map()
    dos = dos_rows(dos_path, name_map)
    print(f"DOS rows: {len(dos)} over {len({r['precinct_code'] for r in dos})} precinct codes")

    race_pages = fetch_race_list()
    site = site_precinct_rows(race_pages)
    print(f"site rows: {len(site)} over {len({r['precinct'] for r in site})} precinct names")

    crosswalk = build_crosswalk(dos, site, dos_path)

    rows = []
    for r in dos:
        name = crosswalk.get(r["precinct_code"], f"(DOS {r['precinct_code']})")
        rows.append({k: r[k] for k in ("county", "office", "district", "party", "candidate", "votes")}
                    | {"precinct": name})
    # keep only site-sourced rows: write-ins + state/municipal committee
    for r in site:
        if (r["candidate"] == "Write Ins"
                or "State Committee" in r["office"]
                or "Committeeman" in r["office"] or "Committeewoman" in r["office"]):
            rows.append(r)

    out = []
    for r in rows:
        out.append({k: r.get(k, "") for k in FIELDNAMES})

    def sortkey(r):
        return (r["precinct"], r["office"], r["district"] or "", r["party"], r["candidate"])

    out.sort(key=sortkey)
    with open(out_csv, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        writer.writeheader()
        for r in out:
            writer.writerow(r)
    print(f"Wrote {len(out)} rows to {out_csv}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
