#!/usr/bin/env python3
"""
Parse Potter County PA 2025 General (Municipal) Election precinct results.

Source: Potter PA Precinct_Potter_112025.pdf (Electionware format,
ALL-CAPS office headers, mixed-case candidate names, prefix-style local
offices with a middle lowercase ``2yr``/``4yr``/``6yr`` term token).

Usage:
    python parsers/pa_potter_general_2025_results_parser.py \\
        "<input.pdf>" "<output.csv>"

Uses the shared natural-pdf Electionware parser in
``electionware_precinct_np`` and supplies Potter-specific config.

Potter-specific quirks:
  - Local office headers are prefix-style with the municipality LAST:
    "SUPERVISOR ABBOTT TWP", "MAYOR AUSTIN BORO",
    "COUNCILMAN 4yr COUDERSPORT BOROUGH WARD 2",
    "AUDITOR 2yr ABBOTT TWP".
  - Term token (``NNyr``) sits between the office name and the
    municipality; the shared prefix handler already strips a leading
    term token from the remainder, so this works out of the box.
  - Offices are singular: "JUDGE OF ELECTION", "INSPECTOR OF ELECTION"
    (no trailing "S"). Normalized to the canonical plural form.
  - "COUNCILMAN" is Potter's label for Borough Council; normalized to
    "Borough Council".
  - Precinct names include ward designators:
    "COUDERSPORT BOROUGH WARD 1", "GALETON BOROUGH WARD 2".
  - County header suffix is "POTTER COUNTY, PA" (includes state).
  - School Director headers have the form
    "SCHOOL DIRECTOR [NNyr] <district name> SCHOOL [REGION N|AT LARGE]"
    (district ends with "SCHOOL", not "SCHOOL DISTRICT"). Handled by a
    custom ``school_director_handler``.
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
    "November 4, 2025 POTTER COUNTY, PA",
    "Precinct Summary - ",
    "Report generated with Electionware",
    "TOTAL Election Mail Provision",
    "Day al",
    "Voter Turnout - Total",
    "Vote For ",
    "Total Votes Cast",
    "Contest Totals",
)


EXACT_OFFICES = {
    "JUDGE OF THE SUPERIOR COURT": ("Judge of the Superior Court", ""),
    "JUDGE OF THE COMMONWEALTH COURT": ("Judge of the Commonwealth Court", ""),
    "JUDGE OF THE COURT OF COMMON PLEAS": ("Judge of the Court of Common Pleas", ""),
    "DISTRICT ATTORNEY": ("District Attorney", ""),
    "CORONER": ("Coroner", ""),
    "SHERIFF": ("Sheriff", ""),
    "TREASURER": ("Treasurer", ""),
    "COUNTY TREASURER": ("Treasurer", ""),
    "CONTROLLER": ("Controller", ""),
    "REGISTER AND RECORDER": ("Register and Recorder", ""),
    "REGISTER & RECORDER": ("Register and Recorder", ""),
    "PROTHONOTARY": ("Prothonotary", ""),
    "COUNTY COMMISSIONER": ("County Commissioner", ""),
}


# Prefix-style local offices (office name first, municipality last). Order
# matters: longest matching prefix wins, so "INSPECTOR OF ELECTION" must be
# checked before "JUDGE OF ELECTION" etc. Potter uses singular forms
# ("JUDGE OF ELECTION"); we normalize to the canonical plural.
LOCAL_OFFICES = [
    ("INSPECTOR OF ELECTIONS", "Inspector of Elections"),
    ("INSPECTOR OF ELECTION", "Inspector of Elections"),
    ("JUDGE OF ELECTIONS", "Judge of Elections"),
    ("JUDGE OF ELECTION", "Judge of Elections"),
    ("BOROUGH COUNCIL", "Borough Council"),
    ("COUNCILMAN", "Borough Council"),
    ("TOWNSHIP SUPERVISOR", "Township Supervisor"),
    ("SUPERVISOR", "Township Supervisor"),
    ("TOWNSHIP AUDITOR", "Township Auditor"),
    ("BOROUGH AUDITOR", "Borough Auditor"),
    ("AUDITOR", "Auditor"),
    ("TAX COLLECTOR", "Tax Collector"),
    ("CONSTABLE", "Constable"),
    ("MAYOR", "Mayor"),
]


# School Director headers:
#   "SCHOOL DIRECTOR AUSTIN AREA SCHOOL REGION 1"
#     -> ("School Director Region 1", "Austin Area")
#   "SCHOOL DIRECTOR 2yr GALETON AREA SCHOOL REGION 2"
#     -> ("School Director Region 2 (2 Year)", "Galeton Area")
#   "SCHOOL DIRECTOR PORT ALLEGANY SCHOOL AT LARGE"
#     -> ("School Director At Large", "Port Allegany")
#   "SCHOOL DIRECTOR KEYSTONE CENTRAL SCHOOL"
#     -> ("School Director", "Keystone Central")
def school_director(line: str):
    if not line.startswith("SCHOOL DIRECTOR"):
        return None
    core = line[len("SCHOOL DIRECTOR"):].strip()
    tokens = core.split()

    # Optional leading NNyr term token.
    years = None
    if tokens:
        tm = TERM_TOKEN_RE.match(tokens[0])
        if tm:
            years = tm.group(1)
            tokens = tokens[1:]

    # Trailing "REGION N" or "AT LARGE" designator.
    designator = ""
    if len(tokens) >= 2 and tokens[-2].upper() == "REGION":
        designator = f"Region {tokens[-1]}"
        tokens = tokens[:-2]
    elif len(tokens) >= 2 and [t.upper() for t in tokens[-2:]] == ["AT", "LARGE"]:
        designator = "At Large"
        tokens = tokens[:-2]

    # Strip trailing "SCHOOL" (district qualifier).
    if tokens and tokens[-1].upper() == "SCHOOL":
        tokens = tokens[:-1]

    district = title_case(" ".join(tokens)) if tokens else ""

    office = "School Director"
    if designator:
        office += f" {designator}"
    if years:
        office += f" ({years} Year)"
    return (office, district)


CONFIG = ElectionwareConfig(
    county="Potter",
    skip_prefixes=SKIP_PREFIXES,
    county_header_suffix="POTTER COUNTY, PA",
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
