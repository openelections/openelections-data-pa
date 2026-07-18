#!/usr/bin/env python3
"""
Parse Lycoming County PA 2024 Primary precinct results.

Source: Lycoming PA 2024 General Primary Precinct Results.pdf
(SOVC-by-geography; party in parens on the contest line, mixed-case office
names. No ballots/registered-voters line — emit_registered_voters=False.)

Usage:
    python parsers/pa_lycoming_primary_2024_results_parser.py <input.pdf> <output.csv>
"""

from sovc_geo_primary_np import PrimarySovcConfig, run_cli


SKIP_PREFIXES = (
    "Official Results by Precinct",
    "April 23, 2024 General Primary",
    "Lycoming County 17:11:31",
    "All Precincts, All Districts",
    "Total Ballots Cast:",
    "81 precincts reported",
    "Choice Votes Vote %",
)


CONFIG = PrimarySovcConfig(
    county="Lycoming",
    skip_prefixes=SKIP_PREFIXES,
    emit_registered_voters=False,
)


if __name__ == "__main__":
    run_cli(CONFIG)