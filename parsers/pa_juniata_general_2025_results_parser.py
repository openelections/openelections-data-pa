#!/usr/bin/env python3
"""
Parse Juniata County PA 2025 General (Municipal) Election precinct results.

Source: Juniata PA 2025-Official-Municipal-Election-Precinct-Results.pdf
(Electionware format, ALL-CAPS office headers, suffix-style local offices
with a trailing lowercase ``2yr`` / ``4yr`` term token).

Usage:
    python parsers/pa_juniata_general_2025_results_parser.py \\
        "<input.pdf>" "<output.csv>"

Uses the shared natural-pdf Electionware parser in
``electionware_precinct_np`` and supplies Juniata-specific config.

Juniata-specific quirks:
  - Local office headers are suffix-style with the municipality FIRST:
    "BEALE TOWNSHIP SUPERVISOR", "FAYETTE TOWNSHIP AUDITOR",
    "MIFFLIN BOROUGH COUNCILMAN", "TUSCARORA TWP/E. WATERFORD JUDGE OF
    ELECTIONS".
  - Optional TRAILING term token in lowercase ``NNyr`` form:
    "FERMANAGH TOWNSHIP AUDITOR 4yr", "WALKER TOWNSHIP SUPERVISOR 2yr",
    "MIFFLIN BOROUGH AUDITOR 2yr". The shared suffix handler was extended
    to strip a trailing term token and emit "(N Year)".
  - "COUNCILMAN" is Juniata's label for Borough Council; normalized to
    "Borough Council".
  - "COUNTY TREASURER" (not "TREASURER") — exact office entry.
  - "REGISTER & RECORDER" (ampersand) — normalized to "Register and
    Recorder".
  - No Court of Common Pleas contest; ``include_common_pleas`` is left
    enabled (harmless).
  - School board headers are region-prefixed with optional trailing term:
    "REGION 1 SCHOOL BOARD DIRECTOR", "REGION 2 SCHOOL BOARD DIRECTOR 2YR".
    Handled by a custom ``school_director_handler``.
  - Precinct names are ALL-CAPS and include one with a slash
    ("TUSCARORA TWP/E. WATERFORD"); ``prettify_huntingdon_precinct``
    handles that correctly.
"""

import re

from electionware_precinct_np import (
    ElectionwareConfig,
    TERM_TOKEN_RE,
    expand_muni_flexible,
    prettify_huntingdon_precinct,
    run_cli,
    title_case,
)


SKIP_PREFIXES = (
    "Summary Results Report UNOFFICIAL RESULTS",
    "Summary Results Report OFFICIAL RESULTS",
    "Municipal Election",
    "November 4, 2025 Juniata County",
    "Precinct Summary - ",
    "Report generated with Electionware",
    "Election Provisional",  # 2-line column header (row 1)
    "TOTAL Mail Votes",      # 2-line column header (row 2)
    "Day Votes",
    "Voter Turnout - Total",
    "Vote For ",
    "Total Votes Cast",
    "Contest Totals",
)


EXACT_OFFICES = {
    "JUDGE OF THE SUPERIOR COURT": ("Judge of the Superior Court", ""),
    "JUDGE OF THE COMMONWEALTH COURT": ("Judge of the Commonwealth Court", ""),
    "SHERIFF": ("Sheriff", ""),
    "COUNTY TREASURER": ("Treasurer", ""),
    "TREASURER": ("Treasurer", ""),
    "CORONER": ("Coroner", ""),
    "DISTRICT ATTORNEY": ("District Attorney", ""),
    "CONTROLLER": ("Controller", ""),
    "REGISTER & RECORDER": ("Register and Recorder", ""),
    "REGISTER AND RECORDER": ("Register and Recorder", ""),
    "COUNTY COMMISSIONER": ("County Commissioner", ""),
}


# Suffix-style local offices (municipality comes first). Longest suffixes
# first so "INSPECTOR OF ELECTIONS" matches before "SUPERVISOR" etc. The
# shared suffix handler strips an optional trailing NNyr term token before
# matching, so there's no need to enumerate "AUDITOR 2YR" variants here.
LOCAL_OFFICES = [
    ("INSPECTOR OF ELECTIONS", "Inspector of Elections"),
    ("JUDGE OF ELECTIONS", "Judge of Elections"),
    ("BOROUGH COUNCIL", "Borough Council"),
    ("TOWNSHIP SUPERVISOR", "Township Supervisor"),
    ("TOWNSHIP AUDITOR", "Township Auditor"),
    ("COUNCILMAN", "Borough Council"),
    ("TAX COLLECTOR", "Tax Collector"),
    ("SUPERVISOR", "Township Supervisor"),
    ("AUDITOR", "Auditor"),
    ("CONSTABLE", "Constable"),
    ("MAYOR", "Mayor"),
]


# School board headers: "REGION N SCHOOL BOARD DIRECTOR [NNYR]".
# Emits office="School Director Region N" (plus "(N Year)" when present),
# district="".
def school_director(line: str):
    m = re.match(
        r"^REGION\s+(\S+)\s+SCHOOL\s+BOARD\s+DIRECTOR(?:\s+(\d+)YR)?$",
        line,
        re.IGNORECASE,
    )
    if not m:
        return None
    region = m.group(1).upper()
    years = m.group(2)
    office = f"School Director Region {region}"
    if years:
        office += f" ({years} Year)"
    return (office, "")


CONFIG = ElectionwareConfig(
    county="Juniata",
    skip_prefixes=SKIP_PREFIXES,
    county_header_suffix="Juniata County",
    exact_offices=EXACT_OFFICES,
    local_offices=LOCAL_OFFICES,
    local_office_orientation="suffix",
    retention_style="retention",
    municipality_normalizer=expand_muni_flexible,
    school_director_handler=school_director,
    prettify_precinct=prettify_huntingdon_precinct,
)


if __name__ == "__main__":
    run_cli(CONFIG)
