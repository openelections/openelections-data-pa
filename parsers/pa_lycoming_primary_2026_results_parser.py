#!/usr/bin/env python3
"""Parse Lycoming County PA 2026 Primary precinct results.

Source: Lycoming County 2026GPPrecinct.pdf (SOVC-by-geography; party in
parens on the contest line, e.g. ``Governor (Dem) (Vote for 1)``).
Candidate rows are 5-column: total, vote%, ED, MI, PR. Per-precinct
committee races (``Anthony Dem Committee (M)`` etc.) are skipped.

Usage:
    uv run python parsers/pa_lycoming_primary_2026_results_parser.py <input.pdf> <output.csv>
"""

from sovc_geo_primary_np import PrimarySovcConfig, run_cli


SKIP_PREFIXES = (
    "Official Results by Precinct",
    "May 19, 2026 General Primary",
    "Lycoming County 14:29:38",
    "All Precincts, All Districts",
    "Total Ballots Cast:",
    "80 precincts reported",
    "Choice Votes Vote %",
)


CONFIG = PrimarySovcConfig(
    county="Lycoming",
    skip_prefixes=SKIP_PREFIXES,
    emit_registered_voters=False,
    contest_skip_contains=(
        # Per-precinct committee races: "<Precinct> Dem Committee (M)/(F)"
        "DEM COMMITTEE",
        "REP COMMITTEE",
        "REPUBLICAN COMMITTEE",
        "DEMOCRATIC COMMITTEE",
    ),
)


if __name__ == "__main__":
    run_cli(CONFIG)