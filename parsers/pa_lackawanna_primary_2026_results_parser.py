#!/usr/bin/env python3
"""Parse Lackawanna County PA 2026 Primary precinct results.

Source: Lackawanna County P26.Precinct.Certified.pdf (Electionware precinct
summary). Uses the shared ``electionware_primary_np`` engine with a
county-specific ``PrimaryConfig``. The 2025 general parser is a standalone
script without an ``ElectionwareConfig`` object, so it can't be reused via
the generic ``pa_electionware_primary_2026.py`` adapter; this file supplies
the config directly.

Contest list: Governor, Lieutenant Governor, Representative in Congress,
Senator in the General Assembly, Representative in the General Assembly,
Member of (Democratic|Republican) State Committee, and per-precinct
District Committeeman / Committeewoman races.

Usage:
    uv run python parsers/pa_lackawanna_primary_2026_results_parser.py <input.pdf> <output.csv>
"""

import re

from electionware_primary_np import PrimaryConfig, run_cli


# Lackawanna's PDF inserts a "VOTE %" column between TOTAL and Election Day
# (e.g. "JOSH SHAPIRO 224 97.82% 150 73 1"). Strip the percentage so the
# row matches the standard 4-integer VOTE_TAIL_RE.
_PCT_RE = re.compile(r"\s+\d+(?:\.\d+)?%")


def strip_pct(line: str) -> str:
    return _PCT_RE.sub(" ", line)


SKIP_PREFIXES = (
    "Summary Results Report CERTIFIED RESULTS",
    "Summary Results Report OFFICIAL RESULTS",
    "Summary Results Report UNOFFICIAL RESULTS",
    "GENERAL PRIMARY",
    "General Primary",
    "May 19, 2026 LACKAWANNA COUNTY",
    "May 19, 2026 Lackawanna County",
    "Precinct Summary - ",
    "Report generated with Electionware",
    "LACKAWANNA PRECINCT -",
    "TOTAL VOTE % Election",
    "TOTAL Mail Votes",
    "Day Votes",
    "Election Mail VotesProvisional",
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
)

EXACT_OFFICES = {
    "MEMBER OF DEMOCRATIC STATE COMMITTEE": ("Member of Democratic State Committee", ""),
    "MEMBER OF REPUBLICAN STATE COMMITTEE": ("Member of Republican State Committee", ""),
    "DEMOCRATIC STATE COMMITTEE": ("Member of Democratic State Committee", ""),
    "REPUBLICAN STATE COMMITTEE": ("Member of Republican State Committee", ""),
}

LOCAL_OFFICES = [
    # Per-precinct committee races: "DISTRICT COMMITTEEMAN <precinct>",
    # "DISTRICT COMMITTEEWOMAN <precinct>".
    ("DISTRICT COMMITTEEMAN", "Committeeman"),
    ("DISTRICT COMMITTEEWOMAN", "Committeewoman"),
]


CONFIG = PrimaryConfig(
    county="Lackawanna",
    skip_prefixes=SKIP_PREFIXES,
    county_header_suffix="LACKAWANNA COUNTY",
    exact_offices=EXACT_OFFICES,
    local_offices=LOCAL_OFFICES,
    local_office_orientation="suffix",
    retention_style="retention",
    line_preprocessor=strip_pct,
)


if __name__ == "__main__":
    run_cli(CONFIG)