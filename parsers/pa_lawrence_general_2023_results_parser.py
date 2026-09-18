#!/usr/bin/env python3
"""
Parser for Lawrence County, PA 2023 General Election precinct results.

Source: Lawrence County Precinct Summary Results 2023 General.pdf
(Electionware "Summary Results Report", 381 pages, 75 precincts). Parsed
from the layout-preserving pdftotext extract via the shared text engine
``electionware_txt``; a PDF input is converted with pdftotext first.

Usage:
    python parsers/pa_lawrence_general_2023_results_parser.py \
        "<input.txt|input.pdf>" "<output.csv>"

Lawrence 2023 header style follows the county's 2025 file/parser
(2025/counties/...lawrence__precinct.csv +
parsers/pa_lawrence_general_2025_results_parser.py); precinct names
("New Castle 1-1", "Hickory Twp 1", "Bessemer Boro", "S.N.P.J. Boro")
match the 2025 file exactly.

  - Kind-first local offices with redundant "VOTE N" (Vote For) and
    "TERM N" infixes and the municipality LAST:
      "TOWNSHIP SUPERVISOR VOTE 1 TERM 6 HICKORY" -> Township Supervisor
      (6 Year) | Hickory; "TOWNSHIP AUDITOR VOTE 1 TERM 2 HICKORY" ->
      Township Auditor (2 Year) | Hickory; "TOWNSHIP TAX COLLECTER
      NESHANNOCK" (source typo COLLECTER, no term) -> Tax Collector |
      Neshannock; "BOROUGH COUNCIL VOTE 1 TERM 2 ENON VALLEY" -> Borough
      Council (2 Year) | Enon Valley; "BOROUGH AUDITOR VOTE1 TERM 4 ENON
      VALLEY" (no space) -> Borough Auditor (4 Year) | Enon Valley;
      "BOROUGH MAYOR SOUTH NEW CASTLE" -> Mayor | South New Castle;
      "BOROUGH TAX COLLECTOR ENON VALLEY" -> Tax Collector | Enon Valley;
      "CITY COUNCIL VOTE 1 TERM2 NEW CASTLE" -> City Council (2 Year) |
      New Castle; "MAYOR NEW CASTLE" -> Mayor | New Castle.
    Districts use the short municipality form, per the 2025 file.
  - "MAGISTERIAL DISTRICT JUDGE DISTRICT 53-3-1" -> Magisterial District
      Judge | 53-3-1.
  - School directors: "SCHOOL DIRECTOR <DISTRICT>" (no term printed in
      2023) -> School Director | <District>; the Blackhawk entry "SCHOOL
      DIRECTOR VOTE 2 TERM 4 BLACKHAWK AREA REGION 1" -> School Director
      Region 1 (4 Year) | Blackhawk Area (2025 convention).
  - Retentions: bare "RETAIN JACK PANELLA" / "RETAIN VICTOR P STABILE"
      -> "Superior Court Retention - Jack Panella" / "Superior Court
      Retention - Victor P Stabile" (2025 Lawrence retention style,
      periods dropped from names).
  - Ballot question (YES/NO rows): "THE MERGER OF SHENANGO TOWNSHIP AND
      SOUTH NEW CASTLE BOROUGH SHENANGO AREA" -> Merger Referendum |
      Shenango Township and South New Castle Borough.
  - County offices: "COUNTY CONTROLLER" -> County Controller; "COUNTY
      CORONER" -> Coroner; "COUNTY TREASURER" -> Treasurer; "PROTHONOTARY
      AND CLERK OF COURTS" -> Prothonotary and Clerk of Courts.
  - Candidates carry an inline VOTE % column (stripped by the engine);
    the "D/R" cross-filed party code is preserved.
"""

import re

from electionware_txt import TxtConfig, run_cli
from electionware_precinct_np import title_case


EXACT_OFFICES = {
    "JUSTICE OF THE SUPREME COURT": ("Justice of the Supreme Court", ""),
    "JUDGE OF THE SUPERIOR COURT": ("Judge of the Superior Court", ""),
    "JUDGE OF THE COMMONWEALTH COURT": ("Judge of the Commonwealth Court", ""),
    "COUNTY COMMISSIONER": ("County Commissioner", ""),
    "COUNTY CONTROLLER": ("County Controller", ""),
    "COUNTY CORONER": ("Coroner", ""),
    "COUNTY TREASURER": ("Treasurer", ""),
    "PROTHONOTARY AND CLERK OF COURTS": ("Prothonotary and Clerk of Courts", ""),
}

RETENTION = {
    "RETAIN JACK PANELLA": ("Superior Court Retention - Jack Panella", ""),
    "RETAIN VICTOR P STABILE": ("Superior Court Retention - Victor P Stabile", ""),
}

BALLOT_QUESTIONS = {
    "THE MERGER OF SHENANGO TOWNSHIP AND SOUTH NEW CASTLE BOROUGH SHENANGO AREA": (
        "Merger Referendum", "Shenango Township and South New Castle Borough"),
}

# "MAGISTERIAL DISTRICT JUDGE DISTRICT 53-3-1"
MDJ_RE = re.compile(r"^MAGISTERIAL DISTRICT JUDGE DISTRICT\s+(\d[\d-]*)\s*$")

# Kind-first local offices:
#   "TOWNSHIP SUPERVISOR VOTE 1 TERM 6 HICKORY"
#   "BOROUGH AUDITOR VOTE1 TERM 4 ENON VALLEY"   (no space after VOTE)
#   "CITY COUNCIL VOTE 1 TERM2 NEW CASTLE"       (no space after TERM)
#   "TOWNSHIP TAX COLLECTER NESHANNOCK"          (typo, no VOTE/TERM)
#   "BOROUGH MAYOR SOUTH NEW CASTLE"
LOCAL_RE = re.compile(
    r"^(TOWNSHIP|BOROUGH|CITY)\s+"
    r"(SUPERVISOR|AUDITOR|TAX COLLECTER|TAX COLLECTOR|COUNCIL|MAYOR)"
    r"(?:\s+VOTE\s*(\d+))?(?:\s+TERM\s*(\d+))?\s+([A-Z0-9 .]+?)\s*$")

LOCAL_OFFICES = {
    ("TOWNSHIP", "SUPERVISOR"): "Township Supervisor",
    ("TOWNSHIP", "AUDITOR"): "Township Auditor",
    ("TOWNSHIP", "TAX COLLECTER"): "Tax Collector",
    ("TOWNSHIP", "TAX COLLECTOR"): "Tax Collector",
    ("BOROUGH", "COUNCIL"): "Borough Council",
    ("BOROUGH", "AUDITOR"): "Borough Auditor",
    ("BOROUGH", "MAYOR"): "Mayor",
    ("BOROUGH", "TAX COLLECTOR"): "Tax Collector",
    ("CITY", "COUNCIL"): "City Council",
    ("CITY", "MAYOR"): "Mayor",
}

# "SCHOOL DIRECTOR <DISTRICT>" with optional VOTE N / TERM N infixes:
#   "SCHOOL DIRECTOR ELLWOOD CITY AREA"
#   "SCHOOL DIRECTOR VOTE 2 TERM 4 BLACKHAWK AREA REGION 1"
SCHOOL_RE = re.compile(
    r"^SCHOOL DIRECTOR(?:\s+VOTE\s*\d+)?(?:\s+TERM\s*(\d+))?\s+([A-Z0-9 .]+?)\s*$")
# Trailing "REGION N" (Blackhawk): the region moves into the office name.
SCHOOL_REGION_RE = re.compile(r"^(.+?)\s+REGION\s+(\d+)$")


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

    m = MDJ_RE.match(line)
    if m:
        return ("Magisterial District Judge", m.group(1))

    m = LOCAL_RE.match(line)
    if m:
        kind, office_tok, _vote, years, muni = m.groups()
        office = LOCAL_OFFICES[(kind, office_tok)]
        return (_with_term(office, years), title_case(muni.strip()))

    m = SCHOOL_RE.match(line)
    if m:
        years, rest = m.groups()
        rest = rest.strip()
        office = "School Director"
        district = title_case(rest)
        sm = SCHOOL_REGION_RE.match(rest)
        if sm:
            district = title_case(sm.group(1).strip())
            office += f" Region {sm.group(2)}"
        return (_with_term(office, years), district)

    return (line.title(), "")


CONFIG = TxtConfig(
    county="Lawrence",
    normalize_office=normalize_office,
    prettify_precinct=lambda s: s,  # names already match the 2025 file
)


if __name__ == "__main__":
    run_cli(CONFIG)