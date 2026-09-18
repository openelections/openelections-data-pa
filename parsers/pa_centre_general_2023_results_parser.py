#!/usr/bin/env python3
"""
Parser for Centre County, PA 2023 General Election precinct results.

Source: Centre County Precinct Results 2023 General.pdf (Electionware
"Summary Results Report" format). Parsed from the layout-preserving
pdftotext extract via the shared text engine ``electionware_txt``; a PDF
input is converted with pdftotext first.

Usage:
    python parsers/pa_centre_general_2023_results_parser.py \
        "<input.txt|input.pdf>" "<output.csv>"

Centre 2023 header style (ALL-CAPS, muni-last, same conventions as the
county's 2025 file 2025/counties/...centre__precinct.csv):
  - "AUDITOR (2-YEAR INTERIM) BENNER TOWNSHIP" ->
      Township Auditor (2 Year) | Benner Township
  - "COUNCILMAN - WARD 1 BELLEFONTE WARD 1" ->
      Borough Council | Bellefonte Ward 1 (ward duplicated in source)
  - "COUNCILMAN (2-YEAR INTERIM) STATE COLLEGE BOROUGH" ->
      Borough Council (2 Year) | State College Borough
  - "TOWNSHIP COUNCIL COLLEGE TOWNSHIP" -> Borough Council | College
      Township (matches the 2025 file's treatment of College Township)
  - "SUPERVISOR - WARD 1 FERGUSON WARD 1" ->
      Township Supervisor | Ferguson Ward 1
  - "SUPERVISOR AT LARGE FERGUSON TWP" ->
      Township Supervisor At Large | Ferguson Township
  - "SCHOOL DIRECTOR BASD" / "BEASD REGION 1" / "KSCSD REGION 3" /
      "POSD DISTRICT 1" / "PVASD REGION 1" / "SCASD" / "TASD" ->
      School Director [Region|District N] | <district name> (abbreviations
      expanded: BASD Bellefonte, BEASD Bald Eagle, KSCSD Keystone Central,
      POSD Philipsburg-Osceola, PVASD Penns Valley, SCASD State College,
      TASD Tyrone)
  - "DISTRICT JUDGE MAGISTERIAL DISTRICT 49-2-01" ->
      Magisterial District Judge | 49-2-01 (reversed word order)
  - Retentions: "Jack Panella - Superior Retention" ->
      Superior Court Retention - Jack Panella
Candidate rows are TOTAL-only in 2023 (no Election Day/Mail/Provisional
breakdown columns), and party prefixes (DEM/REP/D/R) are present.
Precinct labels are numbered ("1 BELLEFONTE NORTH") and prettified with
the Huntingdon-style title-caser.
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
    "JUDGE OF THE COURT OF COMMON PLEAS": ("Judge of the Court of Common Pleas", ""),
    "COUNTY COMMISSIONER": ("County Commissioner", ""),
    "CONTROLLER": ("Controller", ""),
    "SHERIFF": ("Sheriff", ""),
    "TREASURER": ("Treasurer", ""),
    "PROTHONOTARY": ("Prothonotary", ""),
    "RECORDER OF DEEDS": ("Recorder of Deeds", ""),
    "REGISTER OF WILLS": ("Register of Wills", ""),
    "CORONER": ("Coroner", ""),
}

RETENTION = {
    "Jack Panella - Superior Retention": (
        "Superior Court Retention - Jack Panella", ""),
    "Victor P Stabile - Superior Retention": (
        "Superior Court Retention - Victor P Stabile", ""),
}

MDJ_RE = re.compile(r"^DISTRICT JUDGE MAGISTERIAL DISTRICT\s+(.+)$")

# "AUDITOR (2-YEAR INTERIM) BENNER TOWNSHIP" etc.
INTERIM_RE = re.compile(
    r"^([A-Z][A-Z ]+?)\s*\((\d+)-YEAR INTERIM\)\s+(.+)$")

# "COUNCILMAN - WARD 1 BELLEFONTE WARD 1" / "SUPERVISOR - WARD 2 FERGUSON WARD 2"
WARD_DUP_RE = re.compile(r"^([A-Z ]+?)\s*-\s*WARD (\d+)\s+(.+?)\s+WARD \2$")

# "SUPERVISOR AT LARGE FERGUSON TWP"
AT_LARGE_RE = re.compile(r"^SUPERVISOR AT LARGE\s+(.+)$")

# "SCHOOL DIRECTOR [AT LARGE ]<ABBR>[ REGION N|DISTRICT N]"
SCHOOL_RE = re.compile(r"^SCHOOL DIRECTOR(?:\s+\((\d+)-YEAR INTERIM\))?"
                       r"(?:\s+(AT LARGE))?\s+([A-Z]+)"
                       r"(?:\s+(REGION|DISTRICT)\s+(\d+))?\s*$")

SCHOOL_ABBR = {
    "BASD": "Bellefonte",
    "BEASD": "Bald Eagle",
    "KSCSD": "Keystone Central",
    "POSD": "Philipsburg-Osceola",
    "PVASD": "Penns Valley",
    "SCASD": "State College",
    "TASD": "Tyrone",
}

# prefix -> normalized office (longest first)
LOCAL_OFFICES = [
    ("TAX COLLECTOR", "Tax Collector"),
    ("TOWNSHIP COUNCIL", "Borough Council"),  # College Township (per 2025)
    ("COUNCILMAN", "Borough Council"),
    ("SUPERVISOR", "Township Supervisor"),
    ("AUDITOR", "Township Auditor"),
    ("CONSTABLE", "Constable"),
    ("MAYOR", "Mayor"),
]


def normalize_office(line: str):
    line = re.sub(r"\s+", " ", line).strip()

    if line in EXACT_OFFICES:
        return EXACT_OFFICES[line]
    if line in RETENTION:
        return RETENTION[line]

    m = MDJ_RE.match(line)
    if m:
        return ("Magisterial District Judge", m.group(1).strip())

    m = SCHOOL_RE.match(line)
    if m:
        years, at_large, abbr, kind, num = m.groups()
        district = SCHOOL_ABBR[abbr]
        office = "School Director"
        if at_large:
            office += " At Large"
        elif kind:
            office += f" {kind.capitalize()} {num}"
        if years:
            office += f" ({years} Year)"
        return (office, district)

    m = WARD_DUP_RE.match(line)
    if m:
        office_raw, ward, muni = m.groups()
        base = dict(LOCAL_OFFICES)[office_raw.strip()]
        return (base, f"{expand_muni_flexible(muni)} Ward {ward}")

    m = AT_LARGE_RE.match(line)
    if m:
        return ("Township Supervisor At Large",
                expand_muni_flexible(m.group(1)))

    m = INTERIM_RE.match(line)
    if m:
        office_raw, years, rest = m.groups()
        base = dict(LOCAL_OFFICES).get(office_raw.strip())
        if base:
            return (f"{base} ({years} Year)", expand_muni_flexible(rest))

    for prefix, base in LOCAL_OFFICES:
        if line.startswith(prefix + " "):
            return (base, expand_muni_flexible(line[len(prefix):].strip()))

    return (line, "")


CONFIG = TxtConfig(
    county="Centre",
    normalize_office=normalize_office,
    prettify_precinct=prettify_huntingdon_precinct,
)


if __name__ == "__main__":
    run_cli(CONFIG)