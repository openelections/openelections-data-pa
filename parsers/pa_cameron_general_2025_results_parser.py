#!/usr/bin/env python3
"""
Parse Cameron County PA 2025 General (Municipal) Election precinct results.

Source: Cameron PA Nov 2025 Municipal Precinct Summary.pdf (Electionware
format, title-case "Statistics" label, "TOTAL / Election Day / Mail /
Provisional" column layout, municipality-prefixed local office names).

Usage:
    python parsers/pa_cameron_general_2025_results_parser.py \\
        "<input.pdf>" "<output.csv>"

Uses the shared natural-pdf Electionware parser in
``electionware_precinct_np`` and supplies Cameron-specific config.

Cameron-specific quirks:
  - Precinct names are already mixed-case in the PDF, so no prettification.
  - Local office headers put the municipality FIRST
    ("DRIFTWOOD BOROUGH MAYOR", "SHIPPEN TOWNSHIP AUDITOR"), so
    local_office_orientation="suffix".
  - Retention uses "SUPREME COURT RETENTION - CHRISTINE DONOHUE" style with
    full judge names that are title-cased on output.
  - School Director headers carry term info and no municipality
    ("SCHOOL DIRECTOR AT LARGE 2YR" -> "School Director At Large (2 Year)").
"""

import re

from electionware_precinct_np import (
    ElectionwareConfig,
    run_cli,
    title_case,
)


SKIP_PREFIXES = (
    "Summary Results Report UNOFFICIAL RESULTS",
    "Summary Results Report OFFICIAL RESULTS",
    "Municipal Election PRECINCT SUMMARY",
    "November 4, 2025 CAMERON COUNTY",
    "Precinct Summary - ",
    "Report generated with Electionware",
    "TOTAL Election Mail Provision",
    "Day al",
    "Voter Turnout - Total",
    "Vote For ",
)

EXACT_OFFICES = {
    "JUDGE OF THE SUPERIOR COURT": ("Judge of the Superior Court", ""),
    "JUDGE OF THE COMMONWEALTH COURT": ("Judge of the Commonwealth Court", ""),
    "SHERIFF": ("Sheriff", ""),
    "PROTHONOTARY": ("Prothonotary", ""),
    "PROTHONOTARY AND CLERK OF COURTS": ("Prothonotary and Clerk of Courts", ""),
    "TREASURER": ("Treasurer", ""),
    "CORONER": ("Coroner", ""),
    "DISTRICT ATTORNEY": ("District Attorney", ""),
    "CONTROLLER": ("Controller", ""),
    "REGISTER AND RECORDER": ("Register and Recorder", ""),
    "COUNTY COMMISSIONER": ("County Commissioner", ""),
}

# Local offices by suffix, longest first.
LOCAL_OFFICES = [
    ("BOROUGH COUNCIL", "Borough Council"),
    ("TOWNSHIP SUPERVISOR", "Township Supervisor"),
    ("TOWNSHIP AUDITOR", "Township Auditor"),
    ("JUDGE OF ELECTION", "Judge of Election"),
    ("INSPECTOR OF ELECTION", "Inspector of Election"),
    ("TAX COLLECTOR", "Tax Collector"),
    ("CONSTABLE", "Constable"),
    ("SUPERVISOR", "Supervisor"),
    ("AUDITOR", "Auditor"),
    ("MAYOR", "Mayor"),
    ("TREASURER", "Treasurer"),
]


def school_director(line: str):
    """Cameron school director: no municipality, optional NNYR term.

    Examples:
      "SCHOOL DIRECTOR AT LARGE 2YR"  -> ("School Director At Large (2 Year)", "")
      "SCHOOL DIRECTOR AT LARGE 4YR"  -> ("School Director At Large (4 Year)", "")
      "SCHOOL DIRECTOR REGION II"     -> ("School Director Region II", "")
    """
    if not line.startswith("SCHOOL DIRECTOR"):
        return None
    core = line[len("SCHOOL DIRECTOR"):].strip()
    core = re.sub(r"\b(\d+)YR\b", r"(\1 Year)", core)
    core_tc = title_case(core) if core else ""
    office = "School Director" + (f" {core_tc}" if core_tc else "")
    return (office, "")


CONFIG = ElectionwareConfig(
    county="Cameron",
    skip_prefixes=SKIP_PREFIXES,
    county_header_suffix="CAMERON COUNTY",
    exact_offices=EXACT_OFFICES,
    local_offices=LOCAL_OFFICES,
    local_office_orientation="suffix",
    retention_style="retention",
    school_director_handler=school_director,
)


if __name__ == "__main__":
    run_cli(CONFIG)
