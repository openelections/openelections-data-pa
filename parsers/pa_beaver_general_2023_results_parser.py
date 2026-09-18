#!/usr/bin/env python3
"""
Parser for Beaver County, PA 2023 General Election precinct results.

Source: Beaver County Precinct Results 2023 General.pdf (Electionware
"Summary Results Report" format). Parsed from the layout-preserving
pdftotext extract via the shared text engine ``electionware_txt`` (the
natural_pdf engine times out on the 831-page PDF); a PDF input is
converted with pdftotext first.

Usage:
    python parsers/pa_beaver_general_2023_results_parser.py \
        "<input.txt|input.pdf>" "<output.csv>"

Beaver 2023 header style is "<OFFICE> - <N> YR <MUNI>" (ALL-CAPS), e.g.
"AUDITOR - 2 YR DARLINGTON TWP", "MEMBER OF COUNCIL - 4 YR BEAVER FALLS",
"SCHOOL DIRECTOR - 4 YR BLACKHAWK SCHOOL DISTRICT REGION 1". Conventions
follow the county's 2025 file (2025/counties/...beaver__precinct.csv):
office gets "(N Year)", municipalities are expanded (TWP -> Township,
BORO -> Borough), "MEMBER OF COUNCIL" -> "Borough Council", school
director regions become "School Director Region N (X Year)" with the
school district name as district. Retention headers read "JUDICIAL
RETENTION QUESTION <NAME>" (same form as 2025).
"""

import re

from electionware_txt import TxtConfig, run_cli
from electionware_precinct_np import (
    expand_muni_flexible,
    prettify_all_caps_precinct,
    title_case,
)


EXACT_OFFICES = {
    "JUSTICE OF THE SUPREME COURT": ("Justice of the Supreme Court", ""),
    "JUDGE OF THE SUPERIOR COURT": ("Judge of the Superior Court", ""),
    "JUDGE OF THE COMMONWEALTH COURT": ("Judge of the Commonwealth Court", ""),
    "JUDGE OF THE COURT OF COMMON PLEAS": ("Judge of the Court of Common Pleas", ""),
    "COUNTY COMMISSIONER": ("County Commissioner", ""),
    "COUNTY CONTROLLER": ("County Controller", ""),
    "COUNTY TREASURER": ("County Treasurer", ""),
    "CLERK OF COURTS": ("Clerk of Courts", ""),
    "CORONER": ("Coroner", ""),
    "DISTRICT ATTORNEY": ("District Attorney", ""),
    "PROTHONOTARY": ("Prothonotary", ""),
    "RECORDER OF DEEDS": ("Recorder of Deeds", ""),
    "REGISTER OF WILLS": ("Register of Wills", ""),
    "SHERIFF": ("Sheriff", ""),
    "TREASURER": ("Treasurer", ""),
    "CONTROLLER": ("Controller", ""),
}

RETENTION = {
    "JUDICIAL RETENTION QUESTION JACK PANELLA": (
        "Superior Court Retention - Jack Panella", ""),
    "JUDICIAL RETENTION QUESTION VICTOR P STABILE": (
        "Superior Court Retention - Victor P Stabile", ""),
    "JUDICIAL RETENTION QUESTION JAMES J ROSS": (
        "Court of Common Pleas Retention - James J Ross", ""),
    "JUDICIAL RETENTION QUESTION RICHARD MANCINI": (
        "Court of Common Pleas Retention - Richard Mancini", ""),
}

MDJ_RE = re.compile(r"^MAGISTERIAL DISTRICT JUDGE\s+(\d[\d-]*)\s*$")

# "<OFFICE> - <N> YR [TERM] <MUNI>"
LOCAL_RE = re.compile(r"^([A-Z][A-Z ]+?)\s*-\s*(\d)\s*YR(?:\s+TERM)?\s+(.+)$")

LOCAL_OFFICE_NAMES = {
    "MEMBER OF COUNCIL": "Borough Council",
    "TOWNSHIP SUPERVISOR": "Township Supervisor",
    "TOWNSHIP COMMISSIONER": "Township Commissioner",
    "TAX COLLECTOR": "Tax Collector",
    "COMMISSIONER": "Township Commissioner",
    "AUDITOR": "Auditor",
    "MAYOR": "Mayor",
    "CONTROLLER": "Controller",
    "TREASURER": "Treasurer",
}

# Header typos / duplicated municipality names.
MUNI_FIXES = {
    "KOPPEL BOROUGH KOPPEL": "Koppel",
}


def normalize_office(line: str):
    line = re.sub(r"\s+", " ", line).strip()
    if line in EXACT_OFFICES:
        return EXACT_OFFICES[line]
    if line in RETENTION:
        return RETENTION[line]

    m = MDJ_RE.match(line)
    if m:
        return ("Magisterial District Judge", m.group(1))

    m = LOCAL_RE.match(line)
    if m:
        office_raw = m.group(1).strip()
        years = m.group(2)
        rest = m.group(3).strip()
        rest = MUNI_FIXES.get(rest, rest)
        office = LOCAL_OFFICE_NAMES.get(office_raw, title_case(office_raw))
        return (f"{office} ({years} Year)", expand_muni_flexible(rest))

    return (title_case(line), "")


CONFIG = TxtConfig(
    county="Beaver",
    normalize_office=normalize_office,
    prettify_precinct=prettify_all_caps_precinct,
)


if __name__ == "__main__":
    run_cli(CONFIG)