#!/usr/bin/env python3
"""Parse Blair County PA 2026 Primary precinct results.

Source: Blair County Official-Precinct-Summary-Results-Report.pdf
(standard Electionware primary format, "DEM "/"REP " prefixed office
headers). Uses the shared ``electionware_primary_np`` engine.

Contests: Governor, Lieutenant Governor, Representative in Congress,
Senator in the General Assembly, Representative in the General Assembly
(79th/80th districts), Member of (Democratic|Republican) State Committee.
Per-precinct Democratic County Committee races are skipped.

Usage:
    uv run python parsers/pa_blair_primary_2026_results_parser.py <input.pdf> <output.csv>
"""

from electionware_primary_np import PrimaryConfig, run_cli


SKIP_PREFIXES = (
    "Precinct Summary Results Report",
    "General Primary Election",
    "May 19, 2026 BLAIR COUNTY",
    "Precinct Summary - ",
    "Report generated with Electionware",
    "Statistics TOTAL",
    "Day Votes",
    "TOTAL Election",
    "Voter Turnout - Total",
    "Voter Turnout - DEMOCRATIC",
    "Voter Turnout - REPUBLICAN",
    "Voter Turnout - NONPARTISAN",
    "Vote For ",
    "Total Votes Cast",
    "Contest Totals",
    "Overvotes",
    "Undervotes",
    "Not Assigned",
    # Per-precinct Democratic County Committee races (e.g.
    # "DEM DEMOCRATIC COUNTY COMMITTEE ALTOONA 1") — local party positions
    # OpenElections doesn't carry.
    "DEM DEMOCRATIC COUNTY COMMITTEE",
)

EXACT_OFFICES = {
    "MEMBER OF DEMOCRATIC STATE COMMITTEE": ("Member of Democratic State Committee", ""),
    "MEMBER OF REPUBLICAN STATE COMMITTEE": ("Member of Republican State Committee", ""),
    "DEMOCRATIC STATE COMMITTEE": ("Member of Democratic State Committee", ""),
    "REPUBLICAN STATE COMMITTEE": ("Member of Republican State Committee", ""),
}


CONFIG = PrimaryConfig(
    county="Blair",
    skip_prefixes=SKIP_PREFIXES,
    county_header_suffix="BLAIR COUNTY, PENNSYLVANIA",
    exact_offices=EXACT_OFFICES,
    local_offices=[],
    local_office_orientation="prefix",
    retention_style="retention",
)


if __name__ == "__main__":
    run_cli(CONFIG)