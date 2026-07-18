#!/usr/bin/env python3
"""Parse Indiana County PA 2026 Primary precinct results.

Source: Indiana County May-19-2026-Precinct-Summary-Second-Signing.pdf
(Electionware precinct summary). Uses the shared ``electionware_primary_np``
engine with a county-specific ``PrimaryConfig``. Indiana's 2025 general
parser (``pa_indiana_general_2025_results_parser.py``) is a standalone
regex script without an ``ElectionwareConfig`` object, so it can't be
reused via the generic ``pa_electionware_primary_2026.py`` adapter; this
file supplies the config directly.

Usage:
    python parsers/pa_indiana_primary_2026_results_parser.py <input.pdf> <output.csv>
"""

import re

from electionware_primary_np import PrimaryConfig, run_cli


# Indiana's PDF abbreviates "Representative in the General Assembly" as
# "REP IN GEN 62ND DIST" (and similarly for county commissioner: "REP
# COUNTY COMM <muni>"). Map both to standard office names.
REP_GEN_RE = re.compile(r"^REP IN GEN\s+(\d+)\w*\s+DIST", re.IGNORECASE)
REP_COUNTY_COMM_RE = re.compile(r"^REP COUNTY COMM\s+(.+)$", re.IGNORECASE)
DEM_COUNTY_COMM_RE = re.compile(r"^DEM COUNTY COMM\s+(.+)$", re.IGNORECASE)


def indiana_office_handler(line: str):
    m = REP_GEN_RE.match(line)
    if m:
        return ("State House", str(int(m.group(1))))
    m = REP_COUNTY_COMM_RE.match(line)
    if m:
        return (f"County Commissioner {m.group(1).title()}", "")
    m = DEM_COUNTY_COMM_RE.match(line)
    if m:
        return (f"County Commissioner {m.group(1).title()}", "")
    return None


SKIP_PREFIXES = (
    'Vote For 1',
    'Election Absentee',
    'Day /Mail-in',
    'TOTAL Election',
    'TOTAL Provisional',
    'Overvotes',
    'Undervotes',
    'Not Assigned',
    'Write-In Totals',
    'Write-In:',
    'Contest Totals',
    'Total Votes Cast',
    'Voter Turnout - Total',
    '2026 General Primary Election',
    'May 19, 2026',
    'Summary Results Report',
    'Precinct Summary - ',
    'Report generated with Electionware',
)

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


CONFIG = PrimaryConfig(
    county="Indiana",
    skip_prefixes=SKIP_PREFIXES,
    county_header_suffix="Indiana County",
    exact_offices=EXACT_OFFICES,
    local_offices=LOCAL_OFFICES,
    local_office_orientation="suffix",
    retention_style="retention",
    extra_office_handlers=[indiana_office_handler],
)


if __name__ == "__main__":
    run_cli(CONFIG)