#!/usr/bin/env python3
"""
Parse Mifflin County PA 2025 General (Municipal) Election precinct results.

Source: Mifflin PA 2025-November-Municipal-Precinct-Final-Results.pdf
(Electionware format, mixed-case office headers, lowercase "2yr" term
tokens, prefix-style local offices, mixed municipality casing).

Usage:
    python parsers/pa_mifflin_general_2025_results_parser.py \\
        "<input.pdf>" "<output.csv>"

Uses the shared natural-pdf Electionware parser in
``electionware_precinct_np`` and supplies Mifflin-specific config.

Mifflin-specific quirks:
  - Office headers are MIXED CASE ("Judge of the Superior Court",
    "Township Supervisor Armagh Twp"), not ALL CAPS.
  - Term tokens are lowercase ("2yr", "4yr", "6yr"). The shared
    TERM_TOKEN_RE is case-insensitive so this works out of the box.
  - Magisterial District Judge lacks the "District" keyword:
    "Magisterial District Judge 58-3-2" (vs. Huntingdon's "MAGISTERIAL
    DISTRICT JUDGE DISTRICT 20-3-01"). The shared regex accepts both.
  - Judge / Inspector of Elections have ALL-CAPS municipalities mixed
    into otherwise mixed-case headers:
    "Judge of Elections ARMAGH TOWNSHIP-EAST",
    "Inspector of Elections BROWN TOWNSHIP-BIG VALLEY_REEDSVILLE".
    The municipality normalizer (``expand_muni_flexible``) handles both.
  - Precinct names are ALL-CAPS with hyphens and underscores
    ("ARMAGH TOWNSHIP-EAST", "BROWN TOWNSHIP-BIG VALLEY_REEDSVILLE").
  - Register & Recorder (not "Register and Recorder").
  - School director headers embed the school district name at the tail:
    "School Director 2yr Mifflin County School District"
    "School Director - Region I Mount Union Area School District"
"""

import re

from electionware_precinct_np import (
    ElectionwareConfig,
    TERM_TOKEN_RE,
    expand_muni_flexible,
    prettify_huntingdon_precinct,
    run_cli,
    title_case,
)


SKIP_PREFIXES = (
    "Summary Results Report UNOFFICIAL RESULTS",
    "Summary Results Report OFFICIAL RESULTS",
    "Municipal Election",
    "November 4, 2025 MIFFLIN COUNTY",
    "Precinct Summary - ",
    "Report generated with Electionware",
    "TOTAL Election",  # "TOTAL Election Mail VotesProvisional"
    "Day Votes",
    "Voter Turnout - Total",
    "Vote For ",
    "Total Votes Cast",
    "Contest Totals",
)

# Mifflin writes these in already-canonical mixed case, so they can just be
# dropped in the exact-match table unchanged.
EXACT_OFFICES = {
    "Judge of the Superior Court": ("Judge of the Superior Court", ""),
    "Judge of the Commonwealth Court": ("Judge of the Commonwealth Court", ""),
    "Judge of the Court of Common Pleas": ("Judge of the Court of Common Pleas", ""),
    "Sheriff": ("Sheriff", ""),
    "Treasurer": ("Treasurer", ""),
    "Coroner": ("Coroner", ""),
    "District Attorney": ("District Attorney", ""),
    "Controller": ("Controller", ""),
    "Register & Recorder": ("Register and Recorder", ""),
    "County Commissioner": ("County Commissioner", ""),
}

# Prefix-style local office headers. All Mifflin local offices are
# prefix-style with the municipality at the end; several have an optional
# NNyr term token between the office name and the municipality.
LOCAL_OFFICES = [
    ("Borough Council", "Borough Council"),
    ("Township Supervisor", "Township Supervisor"),
    ("Township Auditor", "Township Auditor"),
    ("Judge of Elections", "Judge of Elections"),
    ("Inspector of Elections", "Inspector of Elections"),
    ("Tax Collector", "Tax Collector"),
    ("Constable", "Constable"),
    ("Mayor", "Mayor"),
]


# School director headers embed the "<School District> School District"
# name at the tail. Examples:
#   "School Director 2yr Mifflin County School District"
#   "School Director 4yr Mifflin County School District"
#   "School Director - Region I Mount Union Area School District"
def school_director(line: str):
    if not line.startswith("School Director"):
        return None
    if not line.endswith(" School District"):
        return None
    # Strip "School Director" prefix and " School District" suffix.
    core = line[len("School Director"):-len(" School District")].strip()
    # Optional leading dash.
    if core.startswith("- "):
        core = core[2:].strip()
    tokens = core.split()
    # Optional term token at the start (2yr, 4yr, 6yr).
    years = None
    if tokens:
        tm = TERM_TOKEN_RE.match(tokens[0])
        if tm:
            years = tm.group(1)
            tokens = tokens[1:]
    # Optional "Region <X>" designator where <X> is a Roman numeral or digit.
    region = ""
    if len(tokens) >= 2 and tokens[0].lower() == "region":
        region = f"Region {tokens[1].upper()}"
        tokens = tokens[2:]
    # Whatever is left is the school district name.
    district = title_case(" ".join(tokens)) if tokens else ""
    office = "School Director"
    if region:
        office += f" {region}"
    if years:
        office += f" ({years} Year)"
    return (office, district)


CONFIG = ElectionwareConfig(
    county="Mifflin",
    skip_prefixes=SKIP_PREFIXES,
    county_header_suffix="MIFFLIN COUNTY, PENNSYLVANIA",
    exact_offices=EXACT_OFFICES,
    local_offices=LOCAL_OFFICES,
    local_office_orientation="prefix",
    retention_style="retention",
    municipality_normalizer=expand_muni_flexible,
    school_director_handler=school_director,
    prettify_precinct=prettify_huntingdon_precinct,
)


if __name__ == "__main__":
    run_cli(CONFIG)
