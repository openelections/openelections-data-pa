#!/usr/bin/env python3
"""
Parser for Juniata County, PA 2023 General Election precinct results.

Source: Juniata County Precinct Results 2023 General.pdf (Electionware
"Summary Results Report", 255 pages, 18 precincts). Parsed from the
layout-preserving pdftotext extract via the shared text engine
``electionware_txt``; a PDF input is converted with pdftotext first.

Usage:
    python parsers/pa_juniata_general_2023_results_parser.py \
        "<input.txt|input.pdf>" "<output.csv>"

Juniata 2023 header style follows the county's 2025 file/parser
(2025/counties/...juniata__precinct.csv +
parsers/pa_juniata_general_2025_results_parser.py):

  - Suffix-style local offices with the municipality FIRST and an
    optional trailing lowercase term token:
      "BEALE TOWNSHIP SUPERVISOR"        -> Township Supervisor | Beale Township
      "DELAWARE TOWNSHIP AUDITOR 2yr"    -> Township Auditor (2 Year) | Delaware Township
      "MIFFLIN BOROUGH COUNCILMAN"       -> Borough Council | Mifflin Borough
      "MIFFLIN BOROUGH AUDITOR 2yr"      -> Auditor (2 Year) | Mifflin Borough
      "MIFFLIN BOROUGH MAYOR 2yr"        -> Mayor (2 Year) | Mifflin Borough
      "LACK TOWNSHIP TAX COLLECTOR 2yr"  -> Tax Collector (2 Year) | Lack Township
      "SUSQUEHANNA TOWNSHIP SUPERVISOR SUSQUEHANNA TWP" (duplicated
      trailing muni token) -> Township Supervisor | Susquehanna Township
  - "COUNCILMAN" is Juniata's label for Borough Council (2025
    convention); borough auditors are "Auditor" (not "Borough Auditor"),
    matching the 2025 file.
  - "MAGISTERIAL DISTRICT JUDGE 41-3-02" -> Magisterial District Judge |
    41-3-02.
  - School board headers are region-prefixed (Juniata County School
    District is county-wide, so district stays empty, per the 2025 file):
    "REGION 2 SCHOOL BOARD DIRECTOR" -> "School Director Region 2";
    "REGION 3 GREENWOOD SCHOOL BOARD DIRECTOR" -> "School Director
    Region 3 Greenwood" (the source embeds the Greenwood area name).
  - Retentions: "SUPERIOR COURT - RETAIN JACK PANELLA" -> "Superior
    Court Retention - Jack Panella"; "SUPERIOR COURT - RETAIN VICTOR P.
    STABILE" -> "Superior Court Retention - Victor P. Stabile" (this
    county's 2025 file keeps periods in retention names).
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
    "CORONER": ("Coroner", ""),
    "COUNTY COMMISSIONER": ("County Commissioner", ""),
    "COUNTY AUDITOR": ("County Auditor", ""),
    "PROTHONOTARY": ("Prothonotary", ""),
}

RETENTION = {
    "SUPERIOR COURT - RETAIN JACK PANELLA": (
        "Superior Court Retention - Jack Panella", ""),
    "SUPERIOR COURT - RETAIN VICTOR P. STABILE": (
        "Superior Court Retention - Victor P. Stabile", ""),
}

# "MAGISTERIAL DISTRICT JUDGE 41-3-02"
MDJ_RE = re.compile(r"^MAGISTERIAL DISTRICT JUDGE\s+(\d[\d-]*)\s*$")

# "<Muni> TOWNSHIP <OFFICE> [2yr|4yr] [duplicate trailing '<Muni> TWP']"
TOWNSHIP_RE = re.compile(
    r"^([A-Z ]+?)\s+TOWNSHIP\s+"
    r"(SUPERVISOR|AUDITOR|CONSTABLE|TAX COLLECTOR)"
    r"(?:\s+(\d+)yr)?(?:\s+([A-Z ]+?TWP))?\s*$")

# "<Muni> BOROUGH <OFFICE> [2yr|4yr]"
BOROUGH_RE = re.compile(
    r"^([A-Z ]+?)\s+BOROUGH\s+"
    r"(COUNCILMAN|CONSTABLE|MAYOR|AUDITOR|TAX COLLECTOR)"
    r"(?:\s+(\d+)yr)?\s*$")

# "REGION N SCHOOL BOARD DIRECTOR" with an optional embedded area name
# ("REGION 3 GREENWOOD SCHOOL BOARD DIRECTOR").
SCHOOL_RE = re.compile(
    r"^REGION\s+(\S+)\s+(?:([A-Z ]+?)\s+)?SCHOOL BOARD DIRECTOR\s*$")

TOWNSHIP_OFFICES = {
    "SUPERVISOR": "Township Supervisor",
    "AUDITOR": "Township Auditor",
    "CONSTABLE": "Constable",
    "TAX COLLECTOR": "Tax Collector",
}

BOROUGH_OFFICES = {
    "COUNCILMAN": "Borough Council",
    "MAYOR": "Mayor",
    "AUDITOR": "Auditor",
    "CONSTABLE": "Constable",
    "TAX COLLECTOR": "Tax Collector",
}


def _with_term(office: str, years) -> str:
    return f"{office} ({years} Year)" if years else office


def normalize_office(line: str):
    line = re.sub(r"\s+", " ", line).strip()

    if line in EXACT_OFFICES:
        return EXACT_OFFICES[line]
    if line in RETENTION:
        return RETENTION[line]

    m = MDJ_RE.match(line)
    if m:
        return ("Magisterial District Judge", m.group(1))

    m = TOWNSHIP_RE.match(line)
    if m:
        muni, kind, years = m.groups()[:3]
        return (_with_term(TOWNSHIP_OFFICES[kind], years),
                expand_muni_flexible(muni.strip() + " TOWNSHIP"))

    m = BOROUGH_RE.match(line)
    if m:
        muni, kind, years = m.groups()
        return (_with_term(BOROUGH_OFFICES[kind], years),
                expand_muni_flexible(muni.strip() + " BOROUGH"))

    m = SCHOOL_RE.match(line)
    if m:
        region, area = m.groups()
        office = f"School Director Region {region.upper()}"
        if area:
            office += f" {area.strip().title()}"
        return (office, "")

    return (line.title(), "")


CONFIG = TxtConfig(
    county="Juniata",
    normalize_office=normalize_office,
    prettify_precinct=prettify_huntingdon_precinct,
)


if __name__ == "__main__":
    run_cli(CONFIG)