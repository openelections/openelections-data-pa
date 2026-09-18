#!/usr/bin/env python3
"""
Parser for Elk County, PA 2023 General Election precinct results.

Source: Elk County Official Precinct Report 2023 General.pdf (Electionware
"Summary Results Report", 896 pages, 29 precincts). Parsed from the
layout-preserving pdftotext extract via the shared text engine
``electionware_txt``; a PDF input is converted with pdftotext first.

Usage:
    python parsers/pa_elk_general_2023_results_parser.py \
        "<input.txt|input.pdf>" "<output.csv>"

Elk 2023 header style follows the county's 2025 file/parser
(2025/counties/...elk__precinct.csv + parsers/pa_elk_general_2025_results_parser.py):

  - Suffix-style local offices with the municipality FIRST:
      "BENEZETTE TWP SUPER - 4"  -> Township Supervisor (4 Year) | Benezette
      Township; "FOX TWP AUDITOR - 2" -> Township Auditor (2 Year) | Fox
      Township; "FOX TWP SUPERVISOR" -> Township Supervisor (no term printed
      in 2023); "JOHNSONBURG BOROUGH COUNCIL PERSON" -> Borough Council;
      "CITY OF ST MARYS COUNCIL PERSON" -> City Council | City of St Marys.
  - "COUNCIL PERSON" is Elk's label for council; normalized per 2025.
  - Retentions use Elk's compact form: "RET SUPERIOR COURT - PANELLA" ->
      "Superior Court Retention - Jack Panella";
      "RET SUPERIOR COURT -STABILE" (missing space, as printed) ->
      "Superior Court Retention - Victor P Stabile" (2025/Berks convention,
      periods dropped from names).
  - "MJD 59-03-3" -> Magisterial District Judge | 59-03-3 (district kept as
      printed; the source abbreviates MDJ as "MJD").
  - School directors:
      "BROCKWAY AREA SCHOOL DIRECTOR - REGION 2" -> School Director Region 2
      | Brockway Area; "FOREST AREA SCHOOL DIRECTOR - REGION B" -> School
      Director Region B | Forest Area; "JOHNSONBURG AREA SCHOOL DIRECTOR" ->
      School Director | Johnsonburg Area; "SMA SCHOOL DIRECTOR - R2/ 2" ->
      School Director Region 2 (2 Year) | St Marys Area; "SMA SCHOOL
      DIRECTOR - R2/ 4 YR" -> School Director Region 2 (4 Year).
  - Referenda (YES/NO rows): "RIDGWAY BOR AMBULANCE REF" -> Ambulance
      Referendum | Ridgway Borough; "SPRING CREEK AMBULANCE REF" ->
      Ambulance Referendum | Spring Creek Township; "ST MARYS HOME RULE
      AMENDMENT REF" -> Home Rule Amendment Referendum | City of St Marys.
"""

import re

from electionware_txt import TxtConfig, run_cli
from electionware_precinct_np import (
    expand_muni_flexible,
    prettify_huntingdon_precinct,
)


EXACT_OFFICES = {
    "JUSTICE OF THE SUPREME COURT": ("Justice of the Supreme Court", ""),
    "JUDGE OF THE SUPERIOR COURT": ("Judge of the Superior Court", ""),
    "JUDGE OF THE COMMONWEALTH COURT": ("Judge of the Commonwealth Court", ""),
    "DISTRICT ATTORNEY": ("District Attorney", ""),
    "SHERIFF": ("Sheriff", ""),
    "COUNTY CORONER": ("Coroner", ""),
    "COUNTY TREASURER": ("Treasurer", ""),
    "PROTHONOTARY": ("Prothonotary", ""),
    "COUNTY COMMISSIONER": ("County Commissioner", ""),
    "COUNTY AUDITOR": ("County Auditor", ""),
    "REGISTER & RECORDER": ("Register and Recorder", ""),
}

RETENTION = {
    "RET SUPERIOR COURT - PANELLA": (
        "Superior Court Retention - Jack Panella", ""),
    "RET SUPERIOR COURT -STABILE": (
        "Superior Court Retention - Victor P Stabile", ""),
}

# "MJD 59-03-3" (the source abbreviates Magisterial District Judge as MJD)
MJD_RE = re.compile(r"^MJD\s+(\d[\d-]*)\s*$")

# Township suffix offices: "<muni> TWP SUPER[-VISOR] [- N]" /
# "<muni> TWP AUDITOR [- N]" / "<muni> TWP CONSTABLE".
TWP_RE = re.compile(
    r"^(.+?)\s+TWP\s+(SUPER|SUPERVISOR|AUDITOR|CONSTABLE)"
    r"(?:\s*-\s*(\d+))?\s*$")

# Borough suffix offices: "<muni> BOROUGH COUNCIL PERSON|CONSTABLE|MAYOR|
# TAX COLLECTOR [- N]".
BOROUGH_RE = re.compile(
    r"^(.+?)\s+BOROUGH\s+(COUNCIL PERSON|CONSTABLE|MAYOR|TAX COLLECTOR)"
    r"(?:\s*-\s*(\d+))?\s*$")

# St Marys (Elk's only city): "CITY OF ST MARYS COUNCIL PERSON|CONSTABLE|
# MAYOR|TAX COLLECTOR [- N]".
CITY_RE = re.compile(
    r"^CITY OF ST MARYS\s+(COUNCIL PERSON|CONSTABLE|MAYOR|TAX COLLECTOR)"
    r"(?:\s*-\s*(\d+))?\s*$")

# School directors:
#   "<DISTRICT> AREA SCHOOL DIRECTOR - REGION N" (Brockway / Forest /
#   St Marys), "<DISTRICT> AREA SCHOOL DIRECTOR" (Johnsonburg / Kane /
#   Ridgway), and St Marys' own "SMA SCHOOL DIRECTOR - R2/ 2" /
#   "SMA SCHOOL DIRECTOR - R2/ 4 YR" (region + term).
SCHOOL_RE = re.compile(r"^(.+?)\s+SCHOOL DIRECTOR(?:\s*-\s*(.+))?\s*$")
# "R2/ 2" and "R2/ 4 YR" — the trailing number is the term length (the
# "YR" suffix is present only on the 4-year variant).
SMA_REGION_RE = re.compile(r"^R(\d+)\s*/\s*(?:(\d+)(?:\s*YR)?)?$")

BALLOT_QUESTIONS = {
    "RIDGWAY BOR AMBULANCE REF": ("Ambulance Referendum", "Ridgway Borough"),
    "SPRING CREEK AMBULANCE REF": (
        "Ambulance Referendum", "Spring Creek Township"),
    "ST MARYS HOME RULE AMENDMENT REF": (
        "Home Rule Amendment Referendum", "City of St Marys"),
}

# "<name> BOR" -> "<name> Borough" (expand_muni_flexible leaves "Bor").
BOR_SUFFIX_RE = re.compile(r"^(.+?)\s+BOR$", re.IGNORECASE)


def _muni(name: str) -> str:
    name = name.strip()
    m = BOR_SUFFIX_RE.match(name)
    if m:
        name = f"{m.group(1)} Borough"
    return expand_muni_flexible(name)


def _with_term(office: str, years) -> str:
    return f"{office} ({years} Year)" if years else office


def normalize_office(line: str):
    line = re.sub(r"\s+", " ", line).strip()

    if line in EXACT_OFFICES:
        return EXACT_OFFICES[line]
    if line in RETENTION:
        return RETENTION[line]
    if line in BALLOT_QUESTIONS:
        return BALLOT_QUESTIONS[line]

    m = MJD_RE.match(line)
    if m:
        return ("Magisterial District Judge", m.group(1))

    m = TWP_RE.match(line)
    if m:
        muni, kind, years = m.groups()
        office = {"SUPER": "Township Supervisor",
                  "SUPERVISOR": "Township Supervisor",
                  "AUDITOR": "Township Auditor",
                  "CONSTABLE": "Constable"}[kind]
        return (_with_term(office, years), _muni(muni))

    m = BOROUGH_RE.match(line)
    if m:
        muni, kind, years = m.groups()
        office = ("Borough Council" if kind == "COUNCIL PERSON"
                  else kind.title())
        return (_with_term(office, years),
                expand_muni_flexible(muni.strip()) + " Borough")

    m = CITY_RE.match(line)
    if m:
        kind, years = m.groups()
        office = ("City Council" if kind == "COUNCIL PERSON"
                  else kind.title())
        return (_with_term(office, years), "City of St Marys")

    m = SCHOOL_RE.match(line)
    if m:
        district_raw, designator_raw = m.groups()
        district = "St Marys Area" if district_raw.strip() == "SMA" \
            else _muni(district_raw)
        office = "School Director"
        years = None
        if designator_raw:
            d = designator_raw.strip()
            sm = SMA_REGION_RE.match(d)
            if sm:
                region, years = sm.groups()
                office += f" Region {region}"
            elif d.upper().startswith("REGION "):
                office += f" Region {d[len('REGION '):].strip()}"
            else:
                office += f" {d}"
        return (_with_term(office, years), district)

    return (line.title(), "")


CONFIG = TxtConfig(
    county="Elk",
    normalize_office=normalize_office,
    prettify_precinct=prettify_huntingdon_precinct,
)


if __name__ == "__main__":
    run_cli(CONFIG)