#!/usr/bin/env python3
"""
Parse Cameron County PA 2024 General Election precinct results.

Source: 2024 Cameron County, PA general.pdf (Electionware format).

Usage:
    python parsers/pa_cameron_general_2024_results_parser.py \
        "<input.pdf>" "<output.csv>"

Uses the shared Electionware precinct parser in
``electionware_precinct_np`` with Cameron-specific config.

Cameron-specific notes:
  - Precinct names are already mixed-case in the PDF.
  - Local office headers put the municipality FIRST
    ("DRIFTWOOD BOROUGH MAYOR", "SHIPPEN TOWNSHIP AUDITOR"), so
    local_office_orientation="suffix".
  - Retention uses "SUPREME COURT RETENTION - CHRISTINE DONOHUE" style.
  - No school director races in 2024 general.
"""

import re

from electionware_precinct_np import (
    ElectionwareConfig,
    run_cli,
    title_case,
)


SKIP_PREFIXES = (
    "Summary Results Report",
    "GENERAL ELECTION",
    "November 5, 2024",
    "CAMERON COUNTY",
    "Precinct Summary - ",
    "Report generated with Electionware",
    "TOTAL Election Day Mail Votes Provisional",
    "Election Day Write-ins",
    "Voter Turnout - Total",
    "Vote For ",
    "Write-In Totals",
    "Not Assigned",
    "Total Votes Cast",
    "Overvotes",
    "Undervotes",
    "Contest Totals",
)

EXACT_OFFICES = {
    "PRESIDENTIAL ELECTORS": ("President", ""),
    "UNITED STATES SENATOR": ("U.S. Senate", ""),
    "ATTORNEY GENERAL": ("Attorney General", ""),
    "AUDITOR GENERAL": ("Auditor General", ""),
    "STATE TREASURER": ("State Treasurer", ""),
    "REPRESENTATIVE IN CONGRESS": ("U.S. House", ""),
    "SENATOR IN THE GENERAL ASSEMBLY": ("State Senate", ""),
    "REPRESENTATIVE IN THE GENERAL ASSEMBLY": ("State House", ""),
    "JUDGE OF THE SUPERIOR COURT": ("Judge of the Superior Court", ""),
    "JUDGE OF THE COMMONWEALTH COURT": ("Judge of the Commonwealth Court", ""),
    "SHERIFF": ("Sheriff", ""),
    "PROTHONOTARY": ("Prothonotary", ""),
    "PROTHONOTARY AND CLERK OF COURTS": ("Prothonotary and Clerk of Courts", ""),
    "TREASURER": ("Treasurer", ""),
    "CORONER": ("Coroner", ""),
    "DISTRICT ATTORNEY": ("District Attorney", ""),
    "CONTROLLER": ("Controller", ""),
    "REGISTER AND RECORDER": ("Register and Recorder", ""),
    "COUNTY COMMISSIONER": ("County Commissioner", ""),
}

# Local offices by suffix, longest first.
LOCAL_OFFICES = [
    ("BOROUGH COUNCIL MEMBER", "Borough Council Member"),
    ("BOROUGH COUNCIL", "Borough Council"),
    ("TOWNSHIP SUPERVISOR", "Township Supervisor"),
    ("TOWNSHIP AUDITOR", "Township Auditor"),
    ("JUDGE OF ELECTION", "Judge of Election"),
    ("INSPECTOR OF ELECTION", "Inspector of Election"),
    ("TAX COLLECTOR", "Tax Collector"),
    ("CONSTABLE", "Constable"),
    ("SUPERVISOR", "Supervisor"),
    ("AUDITOR", "Auditor"),
    ("MAYOR", "Mayor"),
    ("TREASURER", "Treasurer"),
]


CONFIG = ElectionwareConfig(
    county="Cameron",
    skip_prefixes=SKIP_PREFIXES,
    county_header_suffix="CAMERON COUNTY",
    exact_offices=EXACT_OFFICES,
    local_offices=LOCAL_OFFICES,
    local_office_orientation="suffix",
    retention_style="retention",
)


if __name__ == "__main__":
    run_cli(CONFIG)
