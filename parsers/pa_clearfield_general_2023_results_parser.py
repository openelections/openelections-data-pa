#!/usr/bin/env python3
"""
Parser for Clearfield County, PA 2023 General Election precinct results.

Source: Clearfield County Precinct Summary 2023 General.pdf (Electionware
"Summary Results Report", 289 pages, 70 precincts). Parsed from the
layout-preserving pdftotext extract via the shared text engine
``electionware_txt``; a PDF input is converted with pdftotext first.

Usage:
    python parsers/pa_clearfield_general_2023_results_parser.py \
        "<input.txt|input.pdf>" "<output.csv>"

Clearfield 2023 header style: "<OFFICE> <seats>-<years> [MULTI] <muni>"
(ALL-CAPS), e.g. "AUDITOR 1-4 MULTI SANDY TOWNSHIP - WEST SANDY",
"COUNTY COMMISSIONER 2-4", "SCHOOL DIRECTOR 5-4 CLEARFIELD AREA SCHOOL
DISTRICT - AT LARGE". Conventions follow the county's 2025 file/parser
(2025/counties/...clearfield__precinct.csv): the second number is the
term length, emitted as " (N Year)" on the office; "MULTI" markers and
duplicated municipality tokens are dropped; TWP/typo spellings expanded;
MEMBER OF COUNCIL -> Borough Council, with the DuBois City council as
"City Council | Dubois City" (2025 convention). Retention questions are
UNNAMED in the source ("SUPERIOR COURT RETENTION QUESTION #1/#2", "COURT
OF COMMON PLEAS RETENTION QUESTION") and are kept as question-numbered
offices.
"""

import re

from electionware_txt import TxtConfig, run_cli
from electionware_precinct_np import (
    expand_muni_flexible,
    prettify_huntingdon_precinct,
    title_case,
)


EXACT_OFFICES = {
    "JUSTICE OF THE SUPREME COURT": ("Justice of the Supreme Court", ""),
    "JUDGE OF THE SUPERIOR COURT": ("Judge of the Superior Court", ""),
    "JUDGE OF THE COMMONWEALTH COURT": ("Judge of the Commonwealth Court", ""),
    "JUDGE OF THE COURT OF COMMON PLEAS": ("Judge of the Court of Common Pleas", ""),
    "COUNTY COMMISSIONER 2-4": ("County Commissioner (4 Year)", ""),
    "DISTRICT ATTORNEY 1-4": ("District Attorney (4 Year)", ""),
    "COUNTY TREASURER 1-4": ("Treasurer (4 Year)", ""),
    "COUNTY CORONER 1-4": ("Coroner (4 Year)", ""),
}

RETENTION = {
    "SUPERIOR COURT RETENTION QUESTION #1": (
        "Superior Court Retention Question #1", ""),
    "SUPERIOR COURT RETENTION QUESTION #2": (
        "Superior Court Retention Question #2", ""),
    "COURT OF COMMON PLEAS RETENTION QUESTION": (
        "Court of Common Pleas Retention Question", ""),
}

# "MAGISTERIAL DISTRICT JUDGE 46-3-04 46-3-04" (district duplicated)
MDJ_RE = re.compile(r"^MAGISTERIAL DISTRICT JUDGE\s+(\d[\d-]*)(?:\s+\1)?\s*$")

# "SCHOOL DIRECTOR [seats-years] <NAME> [AREA] SCHOOL DISTRICT - DESIGNATOR"
# (also matches the "SCHOOL DIRECTOR1-4" missing-space typo)
SCHOOL_RE = re.compile(
    r"^SCHOOL DIRECTOR\s?(\d+-\d+)?\s*(.+?)\s+SCHOOL DISTRICT(?:\s*-\s*(.+))?\s*$")

# "<OFFICE> <seats>-<years> [- MULTI] <muni>"  (muni may be duplicated)
LOCAL_RE = re.compile(
    r"^([A-Z][A-Z ]+?)\s+(\d+)-(\d+)\s+(?:-\s+)?(?:MULTI\s+)?(.+)$")

# Header typos -> canonical municipality text.
MUNI_FIXES = {
    "PINE TOWNSHP": "PINE TOWNSHIP",
    "UNION TOWHIP": "UNION TOWNSHIP",
    "HOUTZDAE BOROUGH": "HOUTZDALE BOROUGH",
}

LOCAL_OFFICE_NAMES = {
    "MEMBER OF COUNCIL": "Borough Council",
    "TAX COLLECTOR": "Tax Collector",
    "SUPERVISOR": "Township Supervisor",
    "AUDITOR": "Township Auditor",
    "MAYOR": "Mayor",
    "CITY CONTROLLER": "City Controller",
}


def _clean_muni(rest: str) -> str:
    rest = rest.strip()
    rest = MUNI_FIXES.get(rest.upper(), rest)
    # Drop a duplicated municipality token ("DUBOIS CITY DUBOIS CITY").
    half = len(rest) // 2
    if len(rest) % 2 == 0 and rest[:half].strip() == rest[half:].strip():
        rest = rest[:half].strip()
    return rest


def normalize_office(line: str):
    line = re.sub(r"\s+", " ", line).strip()

    if line in EXACT_OFFICES:
        return EXACT_OFFICES[line]
    if line in RETENTION:
        return RETENTION[line]

    m = MDJ_RE.match(line)
    if m:
        return ("Magisterial District Judge", m.group(1))

    m = SCHOOL_RE.match(line)
    if m:
        term, district_raw, designator = m.groups()
        office = "School Director"
        if designator:
            d = designator.strip()
            if d.upper().startswith("AT LARGE"):
                office += " At Large"
            elif d.upper().startswith("DISTRICT "):
                office += f" District {d[len('DISTRICT '):].strip()}"
            elif d.upper().startswith("REGION "):
                office += f" Region {d[len('REGION '):].strip()}"
            else:
                office += f" {d}"
        if term:
            years = term.split("-")[1]
            office += f" ({years} Year)"
        return (office, expand_muni_flexible(district_raw))

    m = LOCAL_RE.match(line)
    if m:
        office_raw, seats, years, rest = m.groups()
        office_raw = office_raw.strip()
        rest_clean = _clean_muni(rest)
        # "MAYOR DUBOIS CITY 1-4 DUBOIS CITY" / "CITY CONTROLLER DUBOIS
        # CITY 1-4 DUBOIS CITY": muni embedded in the office name.
        if office_raw.upper().endswith("DUBOIS CITY"):
            office_raw = office_raw[: -len("DUBOIS CITY")].strip()
            rest_clean = "DUBOIS CITY"
        office_name = LOCAL_OFFICE_NAMES.get(office_raw, office_raw.title())
        # DuBois City's council is a city council (2025 convention).
        if (office_raw == "MEMBER OF COUNCIL"
                and "DUBOIS CITY" in rest_clean.upper()):
            office_name = "City Council"
            rest_clean = "DUBOIS CITY"
        return (f"{office_name} ({years} Year)", expand_muni_flexible(rest_clean))

    # Combined row-office header.
    if line.upper().startswith("RECORDER OF DEEDS"):
        return (title_case(line), "")

    return (title_case(line), "")


ORDINAL_RE = re.compile(r"(\d)(St|Nd|Rd|Th)\b")


def prettify_precinct(name: str) -> str:
    s = prettify_huntingdon_precinct(name)
    return ORDINAL_RE.sub(lambda m: m.group(1) + m.group(2).lower(), s)


CONFIG = TxtConfig(
    county="Clearfield",
    normalize_office=normalize_office,
    prettify_precinct=prettify_precinct,
    # One printed row ("MERLE D HAYWARD", Borough Council Houtzdale) has
    # no party prefix in the source; emit it with an empty party.
    party_optional=True,
)


if __name__ == "__main__":
    run_cli(CONFIG)