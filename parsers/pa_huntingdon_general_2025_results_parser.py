#!/usr/bin/env python3
"""
Parse Huntingdon County PA 2025 General (Municipal) Election precinct results.

Source: Huntingdon PA Precinct-Summary-with-Provisionals.pdf (Electionware
format, title-case "Statistics" label variant).

Usage:
    python parsers/pa_huntingdon_general_2025_results_parser.py \\
        "<input.pdf>" "<output.csv>"

Uses the shared natural-pdf Electionware parser in
``electionware_precinct_np`` and supplies Huntingdon-specific config.

Quirks handled:
  - Prefix-style local office headers with ``NNYR/N`` term token
    ("MAYOR 4YR/1 ALEXANDRIA"). The term is dropped silently.
  - Loose retention regex accepting initials ("SUPREME COURT RETENTION- D.W.");
    the retention tail is preserved as-is (not title-cased).
  - "JUDGE OF THE COURT OF COMMON PLEAS 20th Judicial District (Huntingdon
    County)" — mixed-case suffix is handled by the shared "Vote For"
    look-ahead office-header detection.
  - ALL-CAPS precinct names title-cased on output, preserving punctuation
    ("HOPEWELL/PUTTSTOWN" -> "Hopewell/Puttstown").
"""

from electionware_precinct_np import (
    ElectionwareConfig,
    TERM_TOKEN_SLASH_RE,
    prettify_huntingdon_precinct,
    run_cli,
    simple_capitalize,
    title_case_narrow,
)


SKIP_PREFIXES = (
    "Precinct Summary UNOFFICIAL RESULTS",
    "Precinct Summary OFFICIAL RESULTS",
    "Municipal 2025 Election Day",
    "November 4, 2025 Huntingdon County",
    "Precinct Summary - ",
    "Report generated with Electionware",
    "TOTAL Election Early/Abs Provision",
    "Day entee al Votes",
    "Total Votes Cast",
    "Contest Totals",
    "Voter Turnout - Total",
    "Vote For ",
)

EXACT_OFFICES = {
    "JUDGE OF THE SUPERIOR COURT": ("Judge of the Superior Court", ""),
    "JUDGE OF THE COMMONWEALTH COURT": ("Judge of the Commonwealth Court", ""),
    "SHERIFF": ("Sheriff", ""),
    "PROTHONOTARY AND CLERK OF COURTS": ("Prothonotary and Clerk of Courts", ""),
    "TREASURER": ("Treasurer", ""),
    "CORONER": ("Coroner", ""),
    "DISTRICT ATTORNEY": ("District Attorney", ""),
    "CONTROLLER": ("Controller", ""),
    "REGISTER AND RECORDER": ("Register and Recorder", ""),
    "COUNTY COMMISSIONER": ("County Commissioner", ""),
    "SUPERIOR COURT RETENTION": ("Superior Court Retention", ""),
    "COMMONWEALTH COURT RETENTION": ("Commonwealth Court Retention", ""),
}

# Prefix-style local offices with optional ``NNYR/N`` term token.
LOCAL_OFFICES = [
    ("BOROUGH COUNCIL", "Borough Council"),
    ("TOWNSHIP SUPERVISOR", "Township Supervisor"),
    ("JUDGE OF ELECTION", "Judge of Election"),
    ("INSPECTOR OF ELECTION", "Inspector of Election"),
    ("SCHOOL DIRECTOR", "School Director"),
    ("TAX COLLECTOR", "Tax Collector"),
    ("TAX ASSESSOR", "Tax Assessor"),
    ("CONSTABLE", "Constable"),
    ("AUDITOR", "Auditor"),
    ("MAYOR", "Mayor"),
    ("TREASURER", "Treasurer"),
]


CONFIG = ElectionwareConfig(
    county="Huntingdon",
    skip_prefixes=SKIP_PREFIXES,
    county_header_suffix="Huntingdon County",
    exact_offices=EXACT_OFFICES,
    local_offices=LOCAL_OFFICES,
    local_office_orientation="prefix",
    retention_style="retention-loose",
    title_case_retention_tail=False,
    term_token_re=TERM_TOKEN_SLASH_RE,
    drop_term_token=True,
    municipality_normalizer=simple_capitalize,
    prettify_precinct=prettify_huntingdon_precinct,
    fallback_title_case=title_case_narrow,
)


if __name__ == "__main__":
    run_cli(CONFIG)
