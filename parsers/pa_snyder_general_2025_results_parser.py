#!/usr/bin/env python3
"""
Parse Snyder County PA 2025 General (Municipal) Election precinct results.

Source: Snyder PA Precinct-Summary-Official-11.24.2025.pdf (Electionware
format, title-case "Statistics" label, ALL-CAPS precinct names,
prefix-style local office headers, "RETAIN" (not "RETENTION") retention
format).

Usage:
    python parsers/pa_snyder_general_2025_results_parser.py \\
        "<input.pdf>" "<output.csv>"

Uses the shared natural-pdf Electionware parser in
``electionware_precinct_np`` and supplies Snyder-specific config.

Snyder-specific quirks:
  - Retention uses "SUPREME COURT - RETAIN CHRISTINE DONOHUE" format.
  - Local offices are prefix-style with an optional NNYR term token:
    "TOWNSHIP AUDITOR 2YR PENN TWP", "BOROUGH COUNCIL 4YR SELINSGROVE BORO".
  - Municipality uses TWP/BORO abbreviations that need expansion.
  - School Director headers duplicate the district name:
    "MIDD-WEST SCHOOL DIRECTOR 4YR MIDD WEST SCHOOL DIRECTOR"
    -> ("School Director (4 Year)", "Midd-West").
  - Has a COUNTY REGISTER OF WILLS AND RECORDER OF DEEDS office.
  - Precinct names are ALL-CAPS; the MCCLURE BOROUGH case is preserved as
    "McClure Borough".
"""

from electionware_precinct_np import (
    ElectionwareConfig,
    TERM_TOKEN_RE,
    expand_muni_abbrev,
    prettify_all_caps_precinct,
    run_cli,
)


SKIP_PREFIXES = (
    "Summary Results Report UNOFFICIAL RESULTS",
    "Summary Results Report OFFICIAL RESULTS",
    "Municipal Election",
    "November 4, 2025 SNYDER COUNTY",
    "Precinct Summary - ",
    "Report generated with Electionware",
    "TOTAL Election Mail Provision",
    "Day Votes al Votes",
    "Voter Turnout - Total",
    "Vote For ",
    "Total Votes Cast",
    "Contest Totals",
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
    "COUNTY REGISTER OF WILLS AND RECORDER OF DEEDS": (
        "Register of Wills and Recorder of Deeds", ""
    ),
}

LOCAL_OFFICES = [
    ("BOROUGH COUNCIL", "Borough Council"),
    ("BOROUGH MAYOR", "Mayor"),
    ("TOWNSHIP SUPERVISOR", "Township Supervisor"),
    ("TOWNSHIP AUDITOR", "Township Auditor"),
    ("JUDGE OF ELECTIONS", "Judge of Elections"),
    ("INSPECTOR OF ELECTIONS", "Inspector of Elections"),
    ("TAX COLLECTOR", "Tax Collector"),
    ("CONSTABLE", "Constable"),
    ("SUPERVISOR", "Supervisor"),
    ("AUDITOR", "Auditor"),
    ("MAYOR", "Mayor"),
]


def school_director(line: str):
    """Snyder school director: "<DISTRICT> SCHOOL DIRECTOR [NNYR] <DISTRICT> SCHOOL DIRECTOR".

    The district name is duplicated at the tail; strip it and optionally
    emit the term in the office name.
    """
    if "SCHOOL DIRECTOR" not in line:
        return None
    head, _, tail = line.partition("SCHOOL DIRECTOR")
    district_raw = head.strip()
    if not district_raw:
        return None
    tail = tail.strip()
    tail_tokens = tail.split()
    years = None
    if tail_tokens:
        tm = TERM_TOKEN_RE.match(tail_tokens[0])
        if tm:
            years = tm.group(1)
            tail_tokens = tail_tokens[1:]
    office = f"School Director ({years} Year)" if years else "School Director"
    # Preserve hyphens in district name (MIDD-WEST -> Midd-West).
    district = "-".join(
        " ".join(w.capitalize() for w in part.split())
        for part in district_raw.split("-")
    )
    return (office, district)


CONFIG = ElectionwareConfig(
    county="Snyder",
    skip_prefixes=SKIP_PREFIXES,
    county_header_suffix="SNYDER COUNTY",
    exact_offices=EXACT_OFFICES,
    local_offices=LOCAL_OFFICES,
    local_office_orientation="prefix",
    retention_style="retain",
    term_token_re=TERM_TOKEN_RE,
    municipality_normalizer=expand_muni_abbrev,
    school_director_handler=school_director,
    prettify_precinct=prettify_all_caps_precinct,
)


if __name__ == "__main__":
    run_cli(CONFIG)
