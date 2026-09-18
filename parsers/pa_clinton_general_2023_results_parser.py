#!/usr/bin/env python3
"""
Parser for Clinton County, PA 2023 General Election precinct results.

Source: Clinton County Official Precinct Results 2023 General.pdf
(Electionware format; the txt extract is a county-level Summary Results
Report (pages 1-10, no precinct name above its Statistics marker -- that
segment is skipped) followed by the per-precinct report whose page
footers name the precinct). Parsed from the layout-preserving pdftotext
extract via the shared text engine ``electionware_txt``.

Usage:
    python parsers/pa_clinton_general_2023_results_parser.py \
        "<input.txt|input.pdf>" "<output.csv>"

Clinton 2023 header style (ALL-CAPS):
  - "SUPERVISOR 6 YR VOTE 1 WOODWARD TOWNSHIP" ->
      Township Supervisor (6 Year) | Woodward Township ("VOTE N" tokens
      dropped)
  - "AUDITOR 6 YR PINE CREEK PINE CREEK TOWNSHIP" -> duplicated muni
      prefix dropped -> Township Auditor (6 Year) | Pine Creek Township
  - "COUNCIL MEMBER 4 YR VOTE 3 LOCK HAVEN CITY OF LOCK HAVEN" ->
      City Council (4 Year) | Lock Haven (Lock Haven is a city; other
      boroughs -> "Borough Council (4 Year) | <borough>")
  - "MAYOR CITY OF LOCK HAVEN" / "CONTROLLER CITY OF LOCK HAVEN" ->
      Mayor | Lock Haven / City Controller | Lock Haven
  - "MAGISTERIAL DISTRICT JUDGE 25-3-01 DISTRICT I" ->
      Magisterial District Judge | 25-3-01
  - School regions: "KCSD REGION I KCSD REGION I" -> School Director
      Region I | Keystone Central; "JSSD REGION 1 JSSD REGION I" ->
      School Director Region 1 | Jersey Shore Area; "WEST BRANCH AREA
      SCHOOL DISTRICT WEST BRANCH AREA SCHOOL DISTRICT" -> School
      Director | West Branch Area
  - Retentions: two UNNAMED "SUPERIOR COURT RETENTION ELECTION QUESTION"
      headers per precinct. Ballot order (question 1 first) is Jack
      Panella then Victor P. Stabile, confirmed against the PA DoS
      Clinton County 2% statistical audit report (Woodward precinct:
      Panella Yes 270 / No 204, Stabile Yes 288 / No 184), so they are
      emitted as "Superior Court Retention Election Question - Jack
      Panella" / "- Victor P. Stabile" (Berks 2023 naming convention).
      The "COURT OF COMMON PLEAS RETENTION ELECTION QUESTION" is unnamed
      in the source and kept unnamed.
  - "NO CANDIDATE FILED" rows are skipped.
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
    "DISTRICT ATTORNEY": ("District Attorney", ""),
    "SHERIFF": ("Sheriff", ""),
    "REGISTER AND RECORDER": ("Register and Recorder", ""),
    "PROTHONOTARY": ("Prothonotary", ""),
    "COUNTY COMMISSIONER": ("County Commissioner", ""),
    "COUNTY AUDITOR": ("County Auditor", ""),
    "COURT OF COMMON PLEAS RETENTION ELECTION QUESTION": (
        "Court of Common Pleas Retention Election Question", ""),
    "SUPERIOR COURT RETENTION ELECTION QUESTION": (
        "Superior Court Retention Election Question", ""),
}

# Question occurrence (0 = first per precinct) -> office name. Ballot
# order confirmed via the PA DoS Clinton County 2% audit report.
SUPERIOR_QUESTIONS = [
    "Superior Court Retention Election Question - Jack Panella",
    "Superior Court Retention Election Question - Victor P. Stabile",
]


def disambiguate(office, district, occurrence):
    if office == "Superior Court Retention Election Question":
        return (SUPERIOR_QUESTIONS[occurrence], "")
    return (office, district)


MDJ_RE = re.compile(
    r"^MAGISTERIAL DISTRICT JUDGE\s+(\d[\d-]*)\s+DISTRICT\s+\S+\s*$")

# "SUPERVISOR 6 YR VOTE 1 WOODWARD TOWNSHIP" / "AUDITOR 2 YR <muni>"
SUPERVISOR_AUDITOR_RE = re.compile(
    r"^(SUPERVISOR|AUDITOR)\s+(\d+)\s+YR(?:\s+VOTE\s+\d+)?\s+(.+)$")

# "COUNCIL MEMBER 4 YR VOTE 3 <muni>"
COUNCIL_RE = re.compile(
    r"^COUNCIL MEMBER\s+(\d+)\s+YR(?:\s+VOTE\s+\d+)?\s+(.+)$")

# "CONTROLLER CITY OF LOCK HAVEN" / "MAYOR CITY OF LOCK HAVEN"
LOCK_HAVEN_RE = re.compile(r"^(CONTROLLER|MAYOR)\s+CITY OF LOCK HAVEN\s*$")

CONSTABLE_RE = re.compile(r"^CONSTABLE\s+(.+)$")

KCSD_RE = re.compile(r"^KCSD\s+(REGION\s+\S+)\s+KCSD\s+(?:REGION\s+\S+)\s*$")
JSSD_RE = re.compile(r"^JSSD\s+(REGION\s+\S+)\s+JSSD\s+(?:REGION\s+\S+)\s*$")
WEST_BRANCH_RE = re.compile(
    r"^WEST BRANCH AREA SCHOOL DISTRICT\s+WEST BRANCH AREA SCHOOL DISTRICT\s*$")

# Duplicated "<name> <name> TOWNSHIP|BOROUGH" suffix form
# ("PINE CREEK PINE CREEK TOWNSHIP" -> "PINE CREEK TOWNSHIP").
DUP_SUFFIX_RE = re.compile(r"^(.+?)\s+\1\s+(TOWNSHIP|BOROUGH)$")


def _clean_muni(rest: str) -> str:
    rest = rest.strip()
    if rest.upper().endswith("CITY OF LOCK HAVEN"):
        return "Lock Haven"
    m = DUP_SUFFIX_RE.match(rest)
    if m:
        return f"{m.group(1)} {m.group(2)}"
    return rest


def normalize_office(line: str):
    line = re.sub(r"\s+", " ", line).strip()

    if line in EXACT_OFFICES:
        return EXACT_OFFICES[line]

    m = MDJ_RE.match(line)
    if m:
        return ("Magisterial District Judge", m.group(1))

    m = SUPERVISOR_AUDITOR_RE.match(line)
    if m:
        kind, years, rest = m.groups()
        office = ("Township Supervisor" if kind == "SUPERVISOR"
                  else "Township Auditor")
        return (f"{office} ({years} Year)",
                expand_muni_flexible(_clean_muni(rest)))

    m = COUNCIL_RE.match(line)
    if m:
        years, rest = m.groups()
        rest_clean = _clean_muni(rest)
        office = ("City Council" if rest_clean == "Lock Haven"
                  else "Borough Council")
        district = (rest_clean if rest_clean == "Lock Haven"
                    else expand_muni_flexible(rest_clean))
        return (f"{office} ({years} Year)", district)

    m = LOCK_HAVEN_RE.match(line)
    if m:
        office = "City Controller" if m.group(1) == "CONTROLLER" else "Mayor"
        return (office, "Lock Haven")

    m = KCSD_RE.match(line)
    if m:
        return (f"School Director {title_case(m.group(1).strip())}",
                "Keystone Central")

    m = JSSD_RE.match(line)
    if m:
        return (f"School Director {title_case(m.group(1).strip())}",
                "Jersey Shore Area")

    if WEST_BRANCH_RE.match(line):
        return ("School Director", "West Branch Area")

    m = CONSTABLE_RE.match(line)
    if m:
        rest = _clean_muni(m.group(1))
        district = expand_muni_flexible(rest)
        # Preserve a trailing Roman numeral ("PINE CREEK TOWNSHIP II").
        m2 = re.match(r"^(.*\s)([IVX]+)$", district)
        if m2:
            district = f"{m2.group(1)}{m2.group(2)}"
        return ("Constable", district)

    return (line.title(), "")


CONFIG = TxtConfig(
    county="Clinton",
    normalize_office=normalize_office,
    prettify_precinct=prettify_huntingdon_precinct,
    disambiguate=disambiguate,
    # The extract also embeds four cumulative GROUP summary reports
    # (CITY OF LOCK HAVEN, Loganton, Pine Creek, Renovo) whose Statistics
    # marker has no name line above it, so the pseudo-name picked up from
    # the previous page's last row ("Contest Totals ...") identifies them;
    # they are group rollups, not precincts, and are skipped.
    drop_precinct=lambda name: name.startswith("Contest Totals"),
)


if __name__ == "__main__":
    run_cli(CONFIG)