#!/usr/bin/env python3
"""
Parse Juniata County PA 2024 Primary precinct results.

Source: Juniata PA 2024-Official-Primary-Precinct-Results.pdf
(Electionware primary format; party code on the office header line.)

Usage:
    python parsers/pa_juniata_primary_2024_results_parser.py <input.pdf> <output.csv>

Reuses the shared primary-mode engine in ``electionware_primary_np`` and the
Juniata-specific prettifier / local-office handling from the 2025 general
parser.
"""

import re

from electionware_primary_np import PrimaryConfig, run_cli
from electionware_precinct_np import (
    expand_muni_flexible,
    prettify_huntingdon_precinct,
)


SKIP_PREFIXES = (
    "Summary Results Report OFFICIAL RESULTS",
    "Summary Results Report UNOFFICIAL RESULTS",
    "Primary Election",
    "April 23, 2024 Juniata County",
    "Precinct Summary - ",
    "Report generated with Electionware",
    "Election Provisional",
    "TOTAL Mail Votes",
    "Day Votes",
    "Voter Turnout - Total",
    "Vote For ",
    "Total Votes Cast",
    "Contest Totals",
)


# Local offices (party-nominated local contests that appear on the primary
# ballot). Reused from the 2025 general parser since the office names are
# identical in Juniata's primary report.
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


CONFIG = PrimaryConfig(
    county="Juniata",
    skip_prefixes=SKIP_PREFIXES,
    county_header_suffix="Juniata County",
    exact_offices={
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
    },
    local_offices=LOCAL_OFFICES,
    local_office_orientation="suffix",
    retention_style="retention",
    municipality_normalizer=expand_muni_flexible,
    school_director_handler=school_director,
    prettify_precinct=prettify_huntingdon_precinct,
)


if __name__ == "__main__":
    run_cli(CONFIG)