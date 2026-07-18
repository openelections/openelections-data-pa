#!/usr/bin/env python3
"""
Parser for Cumberland County, PA 2025 General Election precinct results.

Source: Cumberland County Precinct Report - Democratic.pdf (Electionware format).

Usage:
    python parsers/pa_cumberland_general_2025_results_parser.py \\
        "<input.pdf>" "<output.csv>"

Uses the shared natural-pdf Electionware parser in
``electionware_precinct_np`` -- see that module's docstring for the
ElectionwareConfig hooks available (office_handlers, municipality_normalizer,
prettifier, exact_offices, local_offices, retention_style, etc.)

Cumberland-specific quirks:
  - TODO: fill in after comparing parsed output to the source PDF.
"""

from electionware_precinct_np import (
    ElectionwareConfig,
    run_cli,
)


SKIP_PREFIXES = (
    'Election',
    'STATISTICS',
    'Vote For 1',
    '2026 GENERAL PRIMARY',
    'Voter Turnout - Total',
    'TOTAL ProvisionalMail',
    'Write-InTotals',
    'Write-In Totals',
    'Precinct Results Report',
    'May 19, 2026',
    'Contest Totals',
    'Total Votes Cast',
    'Not Assigned',
)

# TODO: verify against the actual PDF -- these are common Electionware
# office headers; add/remove as needed for Cumberland.
EXACT_OFFICES = {
    "JUDGE OF THE SUPERIOR COURT": ("Judge of the Superior Court", ""),
    "JUDGE OF THE COMMONWEALTH COURT": ("Judge of the Commonwealth Court", ""),
    "SHERIFF": ("Sheriff", ""),
    "DISTRICT ATTORNEY": ("District Attorney", ""),
    "TREASURER": ("Treasurer", ""),
    "CORONER": ("Coroner", ""),
    "PROTHONOTARY": ("Prothonotary", ""),
    "REGISTER AND RECORDER": ("Register and Recorder", ""),
    "COUNTY COMMISSIONER": ("County Commissioner", ""),
}

# Local offices by suffix, longest first. TODO: adjust to match this
# county's local_office_orientation ("suffix" or "prefix").
LOCAL_OFFICES = [
    ("TOWNSHIP SUPERVISOR", "Township Supervisor"),
    ("BOROUGH COUNCIL", "Borough Council"),
    ("JUDGE OF ELECTION", "Judge of Election"),
    ("INSPECTOR OF ELECTION", "Inspector of Election"),
    ("TAX COLLECTOR", "Tax Collector"),
    ("CONSTABLE", "Constable"),
    ("AUDITOR", "Auditor"),
    ("MAYOR", "Mayor"),
]


CONFIG = ElectionwareConfig(
    county="Cumberland",
    skip_prefixes=SKIP_PREFIXES,
    county_header_suffix="Cumberland County",
    exact_offices=EXACT_OFFICES,
    local_offices=LOCAL_OFFICES,
    local_office_orientation="suffix",  # TODO: verify -- "suffix" or "prefix"
    retention_style="retention",  # TODO: verify against ELECTIONWARE_PARSER_DEVELOPMENT.md variants
)


if __name__ == "__main__":
    run_cli(CONFIG)
