#!/usr/bin/env python3
"""
Parse Wayne County PA 2024 Primary precinct results.

Source: Wayne PA Votes by Precincts PRIMARY2024_202404251321374464.pdf
(SOVC-by-geography; party is in parentheses on the contest line, e.g.
``PRESIDENT OF THE UNITED STATES (DEMOCR) (Vote for 1)``. Candidate rows
are 4-column: total, vote%, ED, MI, PR.)

Usage:
    python parsers/pa_wayne_primary_2024_results_parser.py <input.pdf> <output.csv>
"""

from sovc_geo_primary_np import PrimarySovcConfig, run_cli


SKIP_PREFIXES = (
    "Statement of Votes Cast by Geography",
    "WAYNE COUNTY, APRIL 23, 2024 GENERAL PRIMARY",
    "All Precincts, All Districts",
    "Total Ballots Cast:",
    "35 precincts reported",
    "Choice Votes Vote %",
)


CONFIG = PrimarySovcConfig(
    county="Wayne",
    skip_prefixes=SKIP_PREFIXES,
    countywide_marker="All Precincts",
    emit_registered_voters=True,
    contest_skip_prefixes=(
        # "COMMITTE" matches both COMMITTEE and the COMMITTE typo; "MEMEBER"
        # catches the extraction typo on page 13.
        "MEMBER OF REPUBLICAN COUNTY COMMITTE",
        "MEMEBER OF REPUBLICAN COUNTY COMMITTE",
        "SPECIAL ELECTION",
    ),
)


if __name__ == "__main__":
    run_cli(CONFIG)