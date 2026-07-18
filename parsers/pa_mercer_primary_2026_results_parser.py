#!/usr/bin/env python3
"""Parse Mercer County PA 2026 Primary precinct results.

Source: Mercer County Precinct.pdf (standard Electionware primary
format, "DEM "/"REP " prefixed office headers). Uses the shared
``electionware_primary_np`` engine.

Contests: Governor, Lieutenant Governor, Representative in Congress
(16th), Senator in the General Assembly (50th), Representative in the
General Assembly (7th/17th), Member of (Democratic|Republican) State
Committee. Per-precinct committeeman/woman races are skipped.

Usage:
    uv run python parsers/pa_mercer_primary_2026_results_parser.py <input.pdf> <output.csv>
"""

from electionware_precinct_np import SINGLE_TAIL_RE
from electionware_primary_np import PrimaryConfig, run_cli

import re

# Mercer writes district numbers as "DISTRICT 16" rather than the
# standard "16TH DISTRICT". The shared engine's district regex expects
# the ordinal form, so we rewrite lines before the engine sees them.
_DISTRICT_N_RE = re.compile(r"\bDISTRICT\s+(\d+)\b")


def _ordinal(n: int) -> str:
    if 10 <= n % 100 <= 20:
        suffix = "TH"
    else:
        suffix = {1: "ST", 2: "ND", 3: "RD"}.get(n % 10, "TH")
    return f"{n}{suffix}"


def line_preprocessor(line: str) -> str:
    return _DISTRICT_N_RE.sub(lambda m: _ordinal(int(m.group(1))) + " DISTRICT", line)


SKIP_PREFIXES = (
    "Precinct Summary Results Report",
    "Summary Results Report",
    "2026 Primary Election",
    "May 19, 2026 MERCER",
    "May 19, 2026 Mercer",
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
    # Per-precinct committeeman/woman races — local party positions
    # OpenElections doesn't carry.
    "DEM PRECINCT COMMITTEEMAN",
    "DEM PRECINCT COMMITTEEWOMAN",
    "REP PRECINCT COMMITTEEMAN",
    "REP PRECINCT COMMITTEEWOMAN",
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
    county="Mercer",
    skip_prefixes=SKIP_PREFIXES,
    county_header_suffix="MERCER COUNTY, PENNSYLVANIA",
    exact_offices=EXACT_OFFICES,
    local_offices=[],
    local_office_orientation="prefix",
    retention_style="retention",
    vote_tail_re=SINGLE_TAIL_RE,
    vote_breakdown=False,
    line_preprocessor=line_preprocessor,
)


if __name__ == "__main__":
    run_cli(CONFIG)