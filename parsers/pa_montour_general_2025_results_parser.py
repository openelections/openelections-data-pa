#!/usr/bin/env python3
"""
Parse Montour County PA 2025 General (Municipal) Election precinct results.

Source: Montour PA Unofficial-Precinct-Summary.pdf (Electionware format,
ALL-CAPS office headers, condensed retention headers).

Usage:
    python parsers/pa_montour_general_2025_results_parser.py \\
        "<input.pdf>" "<output.csv>"

Uses the shared natural-pdf Electionware parser in
``electionware_precinct_np`` and supplies Montour-specific config.

Montour-specific quirks:
  - Retention headers are condensed to ``<COURT>-<LASTNAME>`` with no
    "RETENTION" or "RETAIN" keyword:
      "SUPREME-DONOHUE", "SUPREME-DOUGHERTY", "SUPREME-WECHT",
      "SUPERIOR-DUBOW", "COMMONWEALTH-WOJCIK"
    These are mapped by an ``extra_office_handlers`` entry (first-priority
    after exact matches) to the standard "Supreme Court Retention -
    Christine Donohue" style used by the other county parsers.
  - "COUNTY TREASURER" (not just "TREASURER") — added as an exact office.
  - "COUNCILMAN" is used in place of "BOROUGH COUNCIL"; we normalize it to
    "Borough Council" for schema consistency.
  - "BOROUGH AUDITOR" is a distinct local office (Washingtonville).
  - School Director headers have two variants:
      "SCHOOL DIRECTOR 2YR DANVILLE AREA SCHOOL DISTRICT"
      "SCHOOL DIRECTOR WARRIOR RUN SCHOOL DISTRICT - REGION 3"
  - Precinct names are ALL-CAPS and include Roman numeral designators
    ("MAHONING TOWNSHIP II"), so we title-case them with Roman preservation.
  - Some municipalities in local office headers also include Roman numerals
    (INSPECTOR OF ELECTIONS MAHONING TOWNSHIP II); handled by the same
    Roman-preserving normalizer.
"""

import re

from electionware_precinct_np import (
    ElectionwareConfig,
    TERM_TOKEN_RE,
    ROMAN_RE,
    run_cli,
    title_case,
    _cap_preserving_mc,
)


SKIP_PREFIXES = (
    "Summary Results Report UNOFFICIAL RESULTS",
    "Summary Results Report OFFICIAL RESULTS",
    "Municipal Election",
    "November 4, 2025 Montour County",
    "Precinct Summary - ",
    "Report generated with Electionware",
    "TOTAL Election Mail Provision",
    "Day Votes al",
    "Voter Turnout - Total",
    "Vote For ",
    "Total Votes Cast",
    "Contest Totals",
)


# Mapping of Montour's condensed retention headers to the canonical
# "<Court> Court Retention - <Full Name>" form used elsewhere.
MONTOUR_RETENTION = {
    "SUPREME-DONOHUE": ("Supreme Court Retention - Christine Donohue", ""),
    "SUPREME-DOUGHERTY": ("Supreme Court Retention - Kevin M Dougherty", ""),
    "SUPREME-WECHT": ("Supreme Court Retention - David Wecht", ""),
    "SUPERIOR-DUBOW": ("Superior Court Retention - Alice Beck Dubow", ""),
    "COMMONWEALTH-WOJCIK": ("Commonwealth Court Retention - Michael H Wojcik", ""),
}


def montour_retention(line: str):
    return MONTOUR_RETENTION.get(line)


EXACT_OFFICES = {
    "JUDGE OF THE SUPERIOR COURT": ("Judge of the Superior Court", ""),
    "JUDGE OF THE COMMONWEALTH COURT": ("Judge of the Commonwealth Court", ""),
    "JUDGE OF THE COURT OF COMMON PLEAS": ("Judge of the Court of Common Pleas", ""),
    "COUNTY TREASURER": ("Treasurer", ""),
    "TREASURER": ("Treasurer", ""),
    "SHERIFF": ("Sheriff", ""),
    "CORONER": ("Coroner", ""),
    "DISTRICT ATTORNEY": ("District Attorney", ""),
    "CONTROLLER": ("Controller", ""),
    "REGISTER AND RECORDER": ("Register and Recorder", ""),
    "COUNTY COMMISSIONER": ("County Commissioner", ""),
}


# Prefix-style local offices. "COUNCILMAN" is Montour's label for what other
# counties call "BOROUGH COUNCIL"; we normalize to the common form.
LOCAL_OFFICES = [
    ("BOROUGH COUNCIL", "Borough Council"),
    ("COUNCILMAN", "Borough Council"),
    ("BOROUGH AUDITOR", "Borough Auditor"),
    ("TOWNSHIP SUPERVISOR", "Township Supervisor"),
    ("TOWNSHIP AUDITOR", "Township Auditor"),
    ("JUDGE OF ELECTIONS", "Judge of Elections"),
    ("INSPECTOR OF ELECTIONS", "Inspector of Elections"),
    ("TAX COLLECTOR", "Tax Collector"),
    ("CONSTABLE", "Constable"),
    ("MAYOR", "Mayor"),
]


def montour_muni(raw: str) -> str:
    """Expand TWP/BORO and title-case, preserving Roman numerals like II.

    Examples:
        "VALLEY TWP"                  -> "Valley Township"
        "DANVILLE BORO"               -> "Danville Borough"
        "MAHONING TOWNSHIP II"        -> "Mahoning Township II"
        "DANVILLE BOROUGH - FIRST WARD" -> "Danville Borough - First Ward"
    """
    s = re.sub(r"\bTwp\b", "Township", raw, flags=re.IGNORECASE)
    s = re.sub(r"\bBoro\b", "Borough", s, flags=re.IGNORECASE)

    def _rep(m: "re.Match[str]") -> str:
        word = m.group(0)
        if ROMAN_RE.match(word.upper()):
            return word.upper()
        return _cap_preserving_mc(word)

    return re.sub(r"[A-Za-z]+", _rep, s)


def montour_precinct(name: str) -> str:
    """Title-case an ALL-CAPS precinct name while preserving Roman numerals
    and punctuation (hyphens, slashes)."""
    return title_case(name)


# School Director headers:
#   "SCHOOL DIRECTOR 2YR DANVILLE AREA SCHOOL DISTRICT"
#     -> ("School Director (2 Year)", "Danville Area")
#   "SCHOOL DIRECTOR 4YR DANVILLE AREA SCHOOL DISTRICT"
#     -> ("School Director (4 Year)", "Danville Area")
#   "SCHOOL DIRECTOR WARRIOR RUN SCHOOL DISTRICT - REGION 3"
#     -> ("School Director Region 3", "Warrior Run")
def school_director(line: str):
    if not line.startswith("SCHOOL DIRECTOR"):
        return None
    core = line[len("SCHOOL DIRECTOR"):].strip()

    # Optional trailing " - REGION N" designator.
    region = ""
    rm = re.search(r"\s*-\s*REGION\s+(\S+)$", core, re.IGNORECASE)
    if rm:
        region = f"Region {rm.group(1).upper()}"
        core = core[: rm.start()].strip()

    # Optional leading NNYR term token.
    years = None
    tokens = core.split()
    if tokens:
        tm = TERM_TOKEN_RE.match(tokens[0])
        if tm:
            years = tm.group(1)
            tokens = tokens[1:]

    # Strip trailing " SCHOOL DISTRICT".
    if len(tokens) >= 2 and tokens[-2:] == ["SCHOOL", "DISTRICT"]:
        tokens = tokens[:-2]

    district = title_case(" ".join(tokens)) if tokens else ""

    office = "School Director"
    if region:
        office += f" {region}"
    if years:
        office += f" ({years} Year)"
    return (office, district)


CONFIG = ElectionwareConfig(
    county="Montour",
    skip_prefixes=SKIP_PREFIXES,
    county_header_suffix="Montour County",
    exact_offices=EXACT_OFFICES,
    local_offices=LOCAL_OFFICES,
    local_office_orientation="prefix",
    retention_style="retention",  # unused; retention handled by extra handler
    extra_office_handlers=[montour_retention],
    municipality_normalizer=montour_muni,
    school_director_handler=school_director,
    prettify_precinct=montour_precinct,
)


if __name__ == "__main__":
    run_cli(CONFIG)
