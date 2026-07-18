#!/usr/bin/env python3
"""
Parse Bucks County PA 2024 Primary precinct results.

Source: Bucks PA County Final EMS Report for 2024 General Primary. Detail and Grand total.pdf
(SOVC-by-geography; party in parens on the contest line. Candidate rows are
4-column with NO vote% token: ``<name> <total> <ED> <MI> <PR>``. No
ballots/registered-voters line per contest.)

Usage:
    python parsers/pa_bucks_primary_2024_results_parser.py <input.pdf> <output.csv>
"""

from sovc_geo_primary_np import (
    PrimarySovcConfig,
    DATA_LINE_NO_PCT_RE,
    run_cli,
)


SKIP_PREFIXES = (
    "Statement of Votes Cast by Geography",
    "Bucks County Final EMS Report",
    "All Precincts, All Districts",
    "Total Ballots Cast:",
    "Choice Votes ED MI PR",
    "Choice Votes",
)


CONFIG = PrimarySovcConfig(
    county="Bucks",
    skip_prefixes=SKIP_PREFIXES,
    countywide_marker="All Precincts",
    emit_registered_voters=False,
    data_line_re=DATA_LINE_NO_PCT_RE,
    contest_skip_contains=(
        "Committeeman",
        "Committeewoman",
        "Commiteeman",
        "Commiteewoman",
    ),
)


if __name__ == "__main__":
    run_cli(CONFIG)