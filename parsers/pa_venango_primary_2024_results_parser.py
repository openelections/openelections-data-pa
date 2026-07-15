#!/usr/bin/env python3
"""
Parse Venango County PA 2024 Primary precinct results.

Source: Venango PA 2024 Precinct Summary Report - with write-ins.pdf
(Electionware primary; 5-column candidate rows: total, vote%, election_day,
mail, provisional. A line_preprocessor strips the vote% token so the rows
match the standard 4-integer tail.)

Usage:
    python parsers/pa_venango_primary_2024_results_parser.py <input.pdf> <output.csv>
"""

import re

from electionware_primary_np import PrimaryConfig, run_cli
from electionware_precinct_np import prettify_all_caps_precinct


PCT_RE = re.compile(r"\s+\d+\.\d+%")


def venango_strip_pct(line: str) -> str:
    return PCT_RE.sub("", line)


SKIP_PREFIXES = (
    "Precinct Summary Report",
    "Venango County General Primary 2024",
    "April 23, 2024 Venango County",
    "Primary Election",
    "Precinct Summary - ",
    "Report generated with Electionware",
    "Election Provisional",
    "TOTAL Mail Votes",
    "TOTAL VOTE %",
    "Day Votes",
    "Voter Turnout - Total",
    "Vote For ",
    "Total Votes Cast",
    "Contest Totals",
)


CONFIG = PrimaryConfig(
    county="Venango",
    skip_prefixes=SKIP_PREFIXES,
    county_header_suffix="Venango County",
    local_office_orientation="suffix",
    retention_style="retention",
    prettify_precinct=prettify_all_caps_precinct,
    line_preprocessor=venango_strip_pct,
)


if __name__ == "__main__":
    run_cli(CONFIG)