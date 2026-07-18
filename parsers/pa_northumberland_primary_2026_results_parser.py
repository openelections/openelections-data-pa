#!/usr/bin/env python3
"""Parse Northumberland County PA 2026 Primary precinct results.

Source: Northumberland County Precinct.pdf (standard Electionware primary
format, "DEM "/"REP " prefixed office headers). Uses the shared
``electionware_primary_np`` engine.

Contests: Governor, Lieutenant Governor, Representative in Congress (9th),
Representative in the General Assembly (107th), Member of (Democratic|
Republican) State Committee. Per-precinct committeeperson races
(``DEM Committeeperson - Female/Male <precinct>``,
``REP County Committeeperson <precinct>``) are skipped.

Usage:
    uv run python parsers/pa_northumberland_primary_2026_results_parser.py <input.pdf> <output.csv>
"""

from electionware_primary_np import PrimaryConfig, run_cli


SKIP_PREFIXES = (
    "Precinct Summary Results Report",
    "Summary Results Report",
    "GENERAL PRIMARY ELECTION",
    "May 19, 2026 Northumberland",
    "Precinct Summary - ",
    "Report generated with Electionware",
    "STATISTICS",
    "Mail/Absent",
    "TOTAL Election",
    "Voter Turnout - Total",
    "Voter Turnout - DEMOCRATIC PARTY",
    "Voter Turnout - REPUBLICAN PARTY",
    "Voter Turnout - NONPARTISAN",
    "Vote For ",
    "Total Votes Cast",
    "Contest Totals",
    "Overvotes",
    "Undervotes",
    "Not Assigned",
    # Per-precinct committeeperson races — local party positions
    # OpenElections doesn't carry.
    "DEM Committeeperson",
    "REP County Committeeperson",
)

EXACT_OFFICES = {
    "MEMBER OF DEMOCRATIC STATE COMMITTEE": ("Member of Democratic State Committee", ""),
    "MEMBER OF REPUBLICAN STATE COMMITTEE": ("Member of Republican State Committee", ""),
    "MEMBER OF THE DEMOCRATIC STATE COMMITTEE": ("Member of Democratic State Committee", ""),
    "MEMBER OF THE REPUBLICAN STATE COMMITTEE": ("Member of Republican State Committee", ""),
    "DEMOCRATIC STATE COMMITTEE": ("Member of Democratic State Committee", ""),
    "REPUBLICAN STATE COMMITTEE": ("Member of Republican State Committee", ""),
}


CONFIG = PrimaryConfig(
    county="Northumberland",
    skip_prefixes=SKIP_PREFIXES,
    county_header_suffix="NORTHUMBERLAND COUNTY, PENNSYLVANIA",
    exact_offices=EXACT_OFFICES,
    local_offices=[],
    local_office_orientation="prefix",
    retention_style="retention",
)


if __name__ == "__main__":
    run_cli(CONFIG)