#!/usr/bin/env python3
"""
Parse Tioga County PA 2025 General (Municipal) Election precinct results.

Source: Tioga PA Official-Results-General-Nov-4-2025-Precinct.pdf
(Electionware format with several Tioga-specific twists: an extra
"VOTE %" column in candidate rows, title-case muni names, and unusual
retention / school-district / local-office header styles).

Usage:
    python parsers/pa_tioga_general_2025_results_parser.py \\
        "<input.pdf>" "<output.csv>"

Uses the shared natural-pdf Electionware parser in
``electionware_precinct_np`` and supplies Tioga-specific config.

Tioga-specific quirks:
  - **Extra VOTE % column**: candidate and Write-In / Total Votes Cast
    rows include a percentage between the total and the election-day
    column, e.g. ``REP Maria Battista 92 79.31% 87 5 0``. Handled by a
    ``line_preprocessor`` that strips `` \\d+\\.\\d+%`` from every line.
  - **Trailing term tokens in prefix-style local office headers**:
    "SUPERVISOR Bloss Township 6yr", "AUDITOR Bloss Township 2yr",
    "MEMBER OF COUNCIL Wellsboro Borough Ward One 4yr". The shared
    prefix handler now checks remainder[-1] as a fallback after
    remainder[0], so no county-side enumeration is needed.
  - **Retention Question N <Full Name>** (title case, not ALL-CAPS):
    "Retention Question 1 Christine Donohue", "Retention Question 6
    George W. Wheeler" (local Court of Common Pleas judge). Mapped via
    ``extra_office_handlers``.
  - **School district codes as prefix**: STSD (Southern Tioga), NTSD
    (Northern Tioga), CASD (Canton Area), GASD (Galeton Area), WASD
    (Wellsboro Area). Variants: "STSD Region 2", "GASD Region 2 2YR",
    "WASD Wellsboro Area" (no region). Handled by
    ``school_director_handler``.
  - **"MEMBER OF COUNCIL"** is Tioga's label for Borough Council;
    normalized to "Borough Council".
  - **Singular JUDGE OF ELECTION / INSPECTOR OF ELECTION** with the
    precinct name appended ("JUDGE OF ELECTION Bloss Township").
    Normalized to the canonical plural form via an extra handler that
    strips the trailing precinct (same pattern as Lebanon / Mercer).
  - **Title-case muni names**: most are already title-case
    ("Bloss Township"), but Tax Collector rows use "Wellsboro Boro Ward
    1" (abbreviated). ``expand_muni_flexible`` expands the abbreviation.
  - **"PROTHONOTARY AND CLERK OF COURTS"** — exact office entry.
"""

import re
from typing import Optional

from electionware_precinct_np import (
    ElectionwareConfig,
    TERM_TOKEN_RE,
    expand_muni_flexible,
    run_cli,
    title_case,
)


SKIP_PREFIXES = (
    "Summary Results Report OFFICIAL RESULTS",
    "Summary Results Report UNOFFICIAL RESULTS",
    "General November 4 2025",
    "November 4 2025 Tioga County",
    "Precinct Summary - ",
    "Report generated with Electionware",
    "TOTAL VOTE %",   # column header row 1 (candidate tables)
    "TOTAL Election",  # column header row 1 (statistics table)
    "Day /Mail-in",   # column header row 2
    # NB: the third column-header row is a bare "Ballots" — we can't add
    # it to skip_prefixes or it would also match "Ballots Cast - Total".
    # It's harmless: doesn't match VOTE_TAIL_RE and isn't an office header.
    "Voter Turnout - Total",
    "Vote For ",
    "Total Votes Cast",
    "Contest Totals",
)


# --------------------------------------------------------------------------
# Line preprocessor: strip the extra "VOTE %" column from candidate rows.
# --------------------------------------------------------------------------

PCT_RE = re.compile(r"\s+\d+\.\d+%")


def tioga_strip_pct(line: str) -> str:
    """Remove " 79.31%"-style tokens so rows match the standard
    4-integer VOTE_TAIL_RE (total, election_day, mail, provisional)."""
    return PCT_RE.sub("", line)


# --------------------------------------------------------------------------
# Retention: "Retention Question N <Full Name>" title-case form.
# --------------------------------------------------------------------------


TIOGA_RETENTION = {
    "Retention Question 1 Christine Donohue": (
        "Supreme Court Retention - Christine Donohue",
        "",
    ),
    "Retention Question 2 Kevin M. Dougherty": (
        "Supreme Court Retention - Kevin M Dougherty",
        "",
    ),
    "Retention Question 3 David Wecht": (
        "Supreme Court Retention - David Wecht",
        "",
    ),
    "Retention Question 4 Alice Beck Dubow": (
        "Superior Court Retention - Alice Beck Dubow",
        "",
    ),
    "Retention Question 5 Michael H. Wojcik": (
        "Commonwealth Court Retention - Michael H Wojcik",
        "",
    ),
    "Retention Question 6 George W. Wheeler": (
        "Court of Common Pleas Retention - George W Wheeler",
        "",
    ),
}


def tioga_retention(line: str):
    return TIOGA_RETENTION.get(line)


# --------------------------------------------------------------------------
# Inspector/Judge of Election overrides (strip embedded precinct name).
# --------------------------------------------------------------------------


def tioga_overrides(line: str):
    for prefix, norm in (
        ("INSPECTOR OF ELECTION", "Inspector of Elections"),
        ("JUDGE OF ELECTION", "Judge of Elections"),
    ):
        if line == prefix or line.startswith(prefix + " "):
            return (norm, "")
    return None


# --------------------------------------------------------------------------
# School district codes -> full district names.
# --------------------------------------------------------------------------


TIOGA_SCHOOLS = {
    "STSD": "Southern Tioga",
    "NTSD": "Northern Tioga",
    "CASD": "Canton Area",
    "GASD": "Galeton Area",
    "WASD": "Wellsboro Area",
}


def school_director(line: str):
    """Parse Tioga school-director headers.

    Variants:
      "STSD Region 2"          -> ("School Director Region 2", "Southern Tioga")
      "GASD Region 2 2YR"      -> ("School Director Region 2 (2 Year)",
                                    "Galeton Area")
      "WASD Wellsboro Area"    -> ("School Director", "Wellsboro Area")
    """
    m = re.match(r"^(STSD|NTSD|CASD|GASD|WASD)\b\s*(.*)$", line)
    if not m:
        return None
    district = TIOGA_SCHOOLS[m.group(1)]
    rest = m.group(2).strip()
    tokens = rest.split()

    # Optional trailing NNYR term token.
    years: Optional[str] = None
    if tokens:
        tm = TERM_TOKEN_RE.match(tokens[-1])
        if tm:
            years = tm.group(1)
            tokens = tokens[:-1]

    # Optional leading "Region N".
    region: Optional[str] = None
    if len(tokens) >= 2 and tokens[0].upper() == "REGION":
        region = tokens[1]
        # (remaining tokens, if any, are district-name text we ignore since
        # district already comes from the code map)

    office = "School Director"
    if region:
        office += f" Region {region}"
    if years:
        office += f" ({years} Year)"
    return (office, district)


# --------------------------------------------------------------------------
# Exact county offices.
# --------------------------------------------------------------------------


EXACT_OFFICES = {
    "JUDGE OF THE SUPERIOR COURT": ("Judge of the Superior Court", ""),
    "JUDGE OF THE COMMONWEALTH COURT": ("Judge of the Commonwealth Court", ""),
    "JUDGE OF THE COURT OF COMMON PLEAS": ("Judge of the Court of Common Pleas", ""),
    "PROTHONOTARY AND CLERK OF COURTS": ("Prothonotary and Clerk of Courts", ""),
    "SHERIFF": ("Sheriff", ""),
    "COUNTY TREASURER": ("Treasurer", ""),
    "TREASURER": ("Treasurer", ""),
    "CORONER": ("Coroner", ""),
    "DISTRICT ATTORNEY": ("District Attorney", ""),
    "CONTROLLER": ("Controller", ""),
    "REGISTER AND RECORDER": ("Register and Recorder", ""),
    "REGISTER & RECORDER": ("Register and Recorder", ""),
    "COUNTY COMMISSIONER": ("County Commissioner", ""),
}


# --------------------------------------------------------------------------
# Prefix-style local offices. Trailing term tokens like "6yr" are stripped
# by the shared handler now that it checks remainder[-1] as a fallback.
# --------------------------------------------------------------------------


LOCAL_OFFICES = [
    ("INSPECTOR OF ELECTIONS", "Inspector of Elections"),
    ("INSPECTOR OF ELECTION", "Inspector of Elections"),
    ("JUDGE OF ELECTIONS", "Judge of Elections"),
    ("JUDGE OF ELECTION", "Judge of Elections"),
    ("MEMBER OF COUNCIL", "Borough Council"),
    ("TAX COLLECTOR", "Tax Collector"),
    ("SUPERVISOR", "Township Supervisor"),
    ("AUDITOR", "Township Auditor"),
    ("MAYOR", "Mayor"),
]


CONFIG = ElectionwareConfig(
    county="Tioga",
    skip_prefixes=SKIP_PREFIXES,
    county_header_suffix="Tioga County",
    exact_offices=EXACT_OFFICES,
    local_offices=LOCAL_OFFICES,
    local_office_orientation="prefix",
    retention_style="retention",  # unused; handled by extra handler
    extra_office_handlers=[tioga_retention, tioga_overrides],
    municipality_normalizer=expand_muni_flexible,
    school_director_handler=school_director,
    line_preprocessor=tioga_strip_pct,
)


if __name__ == "__main__":
    run_cli(CONFIG)
