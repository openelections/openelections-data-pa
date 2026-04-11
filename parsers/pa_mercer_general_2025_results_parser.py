#!/usr/bin/env python3
"""
Parse Mercer County PA 2025 General (Municipal) Election precinct results.

Source: Mercer PA PRECINCT.pdf (Electionware format, ALL-CAPS office
headers, prefix-style local offices, hyphenated term tokens, and the
county's distinctive "RETENTION ELECTION QUESTION - LASTNAME" court
retention headers).

Usage:
    python parsers/pa_mercer_general_2025_results_parser.py \\
        "<input.pdf>" "<output.csv>"

Uses the shared natural-pdf Electionware parser in
``electionware_precinct_np`` and supplies Mercer-specific config.

Mercer-specific quirks:
  - **"RETENTION ELECTION QUESTION - LASTNAME"** retention headers instead
    of "RETENTION - FULL NAME". Five entries handled via an explicit map
    in ``extra_office_handlers`` (same pattern as Montour / Lebanon).
  - **Hyphenated term tokens in TWO formats**:
      "AUDITOR - 4-YEAR FAIRVIEW TWP"   (with " - " separator)
      "AUDITOR 2-YEAR FAIRVIEW TWP"     (no dash, hyphen internal)
    Enumerated as distinct prefix entries in LOCAL_OFFICES; the remainder
    after the prefix is just the municipality.
  - **Prefix-style local offices** with the municipality LAST, ALL-CAPS,
    e.g. "SUPERVISOR WOLF CREEK TWP", "MAYOR CLARK BOROUGH",
    "BOROUGH COUNCIL GROVE CITY WARD 2", "TAX COLLECTOR TOWN OF GREENVILLE".
  - **City-specific local offices**: "CITY COMMISSIONER HERMITAGE",
    "CITY COUNCIL FARRELL", "CITY COUNCIL SHARON", "CITY TREASURER
    HERMITAGE". Third-class cities in Mercer (Hermitage, Farrell, Sharon)
    use these instead of borough council / mayor.
  - **Borough Auditor** is a distinct local office (Jackson Center, New
    Lebanon, Sheakleyville).
  - **Charter Amendments**: "CHARTER AMENDMENT 1 FARRELL", "CHARTER
    AMENDMENT 4 TOWN OF GREENVILLE". Handled as a dedicated ballot-question
    handler; Yes/No rows are parsed by the shared YES/NO logic.
  - **"TOWN OF GREENVILLE"** is Mercer's only non-township/non-borough
    municipal form; the muni normalizer keeps "of" lowercase.
  - **INSPECTOR OF ELECTIONS / JUDGE OF ELECTIONS** headers embed the
    mixed-case precinct name ("JUDGE OF ELECTIONS Clark Borough"). The
    precinct is already on the row, so we strip the trailing precinct
    name via ``extra_office_handlers`` (same pattern as Lebanon).
  - **School Director** headers have the form
    "SCHOOL DIRECTOR <DISTRICT> [A|B|C]" where the trailing single letter
    is a region designator (Commodore Perry, Mercer, Reynolds). Other
    districts have no letter suffix. Handled by a custom
    ``school_director_handler`` that emits "School Director Region A"
    style offices.
  - **Precinct names** are already in title case in the PDF
    ("Clark Borough", "Hermitage NE-1", "Wheatland /Hermitage") so no
    prettifier is needed.
"""

import re
from typing import Optional

from electionware_precinct_np import (
    SMALL_WORDS,
    ElectionwareConfig,
    run_cli,
    title_case,
)


SKIP_PREFIXES = (
    "Precinct Summary Results Report OFFICIAL RESULTS",
    "Precinct Summary Results Report UNOFFICIAL RESULTS",
    "2025 General Election",
    "November 4, 2025 Mercer County",
    "Precinct Summary - ",
    "Report generated with Electionware",
    "TOTAL Election Absentee Provision",  # column header row 1
    "Day al",                              # column header row 2
    "Voter Turnout - Total",
    "Vote For ",
    "Total Votes Cast",
    "Contest Totals",
)


# --------------------------------------------------------------------------
# Retention: "<COURT> COURT RETENTION ELECTION QUESTION - LASTNAME".
# --------------------------------------------------------------------------


MERCER_RETENTION = {
    "SUPREME COURT RETENTION ELECTION QUESTION - DONOHUE": (
        "Supreme Court Retention - Christine Donohue",
        "",
    ),
    "SUPREME COURT RETENTION ELECTION QUESTION - DOUGHERTY": (
        "Supreme Court Retention - Kevin M Dougherty",
        "",
    ),
    "SUPREME COURT RETENTION ELECTION QUESTION - WECHT": (
        "Supreme Court Retention - David Wecht",
        "",
    ),
    "SUPERIOR COURT RETENTION ELECTION QUESTION - DUBOW": (
        "Superior Court Retention - Alice Beck Dubow",
        "",
    ),
    "COMMONWEALTH COURT RETENTION ELECTION QUESTION - WOJCIK": (
        "Commonwealth Court Retention - Michael H Wojcik",
        "",
    ),
}


def mercer_retention(line: str):
    return MERCER_RETENTION.get(line)


# --------------------------------------------------------------------------
# Extra override handlers: Inspector/Judge of Elections, Charter Amendments.
# --------------------------------------------------------------------------


def mercer_overrides(line: str):
    # Inspector/Judge of Elections headers embed the precinct name; strip it.
    for prefix, norm in (
        ("INSPECTOR OF ELECTIONS", "Inspector of Elections"),
        ("JUDGE OF ELECTIONS", "Judge of Elections"),
    ):
        if line == prefix or line.startswith(prefix + " "):
            return (norm, "")
    # Charter Amendment ballot questions.
    m = re.match(r"^CHARTER AMENDMENT\s+(\d+)\s+(.+)$", line)
    if m:
        return (f"Charter Amendment {m.group(1)}", mercer_muni(m.group(2)))
    return None


# --------------------------------------------------------------------------
# Exact county offices.
# --------------------------------------------------------------------------


EXACT_OFFICES = {
    "JUDGE OF THE SUPERIOR COURT": ("Judge of the Superior Court", ""),
    "JUDGE OF THE COMMONWEALTH COURT": ("Judge of the Commonwealth Court", ""),
    "JUDGE OF THE COURT OF COMMON PLEAS": ("Judge of the Court of Common Pleas", ""),
    "CONTROLLER": ("Controller", ""),
    "TREASURER": ("Treasurer", ""),
    "COUNTY TREASURER": ("Treasurer", ""),
    "SHERIFF": ("Sheriff", ""),
    "CORONER": ("Coroner", ""),
    "DISTRICT ATTORNEY": ("District Attorney", ""),
    "REGISTER AND RECORDER": ("Register and Recorder", ""),
    "REGISTER & RECORDER": ("Register and Recorder", ""),
    "PROTHONOTARY": ("Prothonotary", ""),
    "COUNTY COMMISSIONER": ("County Commissioner", ""),
}


# --------------------------------------------------------------------------
# Prefix-style local offices.
#
# Order matters: longest prefix wins. Hyphenated term-token variants
# ("AUDITOR - 4-YEAR", "AUDITOR 2-YEAR") are enumerated explicitly rather
# than trying to generalize the term regex.
# --------------------------------------------------------------------------


LOCAL_OFFICES = [
    ("AUDITOR - 4-YEAR", "Township Auditor (4 Year)"),
    ("AUDITOR 2-YEAR", "Township Auditor (2 Year)"),
    ("BOROUGH AUDITOR", "Borough Auditor"),
    ("BOROUGH COUNCIL", "Borough Council"),
    ("TOWNSHIP AUDITOR", "Township Auditor"),
    ("CITY COMMISSIONER", "City Commissioner"),
    ("CITY TREASURER", "City Treasurer"),
    ("CITY COUNCIL", "City Council"),
    ("TAX COLLECTOR", "Tax Collector"),
    ("SUPERVISOR", "Township Supervisor"),
    ("MAYOR", "Mayor"),
]


# --------------------------------------------------------------------------
# Municipality normalizer.
#
# Handles:
#   "COOLSPRING TWP"            -> "Coolspring Township"
#   "CLARK BOROUGH"             -> "Clark Borough"
#   "TOWN OF GREENVILLE"        -> "Town of Greenville"
#   "GROVE CITY WARD 2"         -> "Grove City Ward 2"
#   "DEER CREEK/NEW LEBANON"    -> "Deer Creek/New Lebanon"
#   "JACKSON/JACKSON CENTER TAX" -> "Jackson/Jackson Center Tax"
# --------------------------------------------------------------------------


def mercer_muni(raw: str) -> str:
    s = re.sub(r"\bTwp\b", "Township", raw, flags=re.IGNORECASE)

    def repl(m: "re.Match[str]") -> str:
        word = m.group(0)
        if m.start() > 0 and word.lower() in SMALL_WORDS:
            return word.lower()
        if len(word) >= 3 and word[:2].upper() == "MC":
            return "Mc" + word[2:].capitalize()
        return word.capitalize()

    return re.sub(r"[A-Za-z]+", repl, s)


# --------------------------------------------------------------------------
# School Director handler.
#
#   "SCHOOL DIRECTOR COMMODORE PERRY A"
#     -> ("School Director Region A", "Commodore Perry")
#   "SCHOOL DIRECTOR MERCER B"
#     -> ("School Director Region B", "Mercer")
#   "SCHOOL DIRECTOR CRAWFORD CENTRAL"
#     -> ("School Director", "Crawford Central")
#   "SCHOOL DIRECTOR WEST MIDDLESEX"
#     -> ("School Director", "West Middlesex")
# --------------------------------------------------------------------------


def school_director(line: str):
    if not line.startswith("SCHOOL DIRECTOR"):
        return None
    core = line[len("SCHOOL DIRECTOR"):].strip()
    tokens = core.split()
    region: Optional[str] = None
    if tokens and len(tokens[-1]) == 1 and tokens[-1].isalpha():
        region = tokens[-1].upper()
        tokens = tokens[:-1]
    district = title_case(" ".join(tokens)) if tokens else ""
    office = "School Director"
    if region:
        office += f" Region {region}"
    return (office, district)


CONFIG = ElectionwareConfig(
    county="Mercer",
    skip_prefixes=SKIP_PREFIXES,
    county_header_suffix="Mercer County",
    exact_offices=EXACT_OFFICES,
    local_offices=LOCAL_OFFICES,
    local_office_orientation="prefix",
    retention_style="retention",  # unused; handled by extra handler
    extra_office_handlers=[mercer_retention, mercer_overrides],
    municipality_normalizer=mercer_muni,
    school_director_handler=school_director,
)


if __name__ == "__main__":
    run_cli(CONFIG)
