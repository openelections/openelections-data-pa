#!/usr/bin/env python3
"""
Parse Blair County PA 2025 General (Municipal) Election precinct results.

Source: Blair PA Official-Precinct-Summary-Results-Report.pdf (standard
Electionware format, ALL-CAPS office headers, prefix-style local offices).

Usage:
    python parsers/pa_blair_general_2025_results_parser.py \\
        "<input.pdf>" "<output.csv>"

Uses the shared natural-pdf Electionware parser in
``electionware_precinct_np`` and supplies Blair-specific config.

Blair-specific quirks:
  - **"COUNCIL" is Blair's label for Borough Council** (must order after
    "CITY COUNCIL" since CITY COUNCIL ALTOONA is distinct). Normalized to
    "Borough Council".
  - **Leading ALL-CAPS term tokens**: "SUPERVISOR 2YR ALLEGHENY TWP",
    "AUDITOR 6YR LOGAN TWP", "COUNCIL 4YR WILLIAMSBURG BOROUGH". Handled
    by the shared prefix handler.
  - **Singular JUDGE OF ELECTION / INSPECTOR OF ELECTION** with the
    precinct name appended ("JUDGE OF ELECTION ALTOONA 1"). Normalized
    via an extra handler that strips the trailing precinct.
  - **Multi-municipality compound headers** joined by " - ":
    "MAYOR ALLEGHENY TWP - TUNNELHILL BORO",
    "COUNCIL ALLEGHENY TWP - TUNNELHILL BORO",
    "CONSTABLE ALLEGHENY TWP - TUNNELHILL BORO",
    "TAX COLLECTOR ALLEGHENY TWP - TUNNELHILL BORO".
    Handled naturally by ``expand_muni_flexible``.
  - **Magisterial District Judge with "DISTRICT" keyword**:
    "MAGISTERIAL DISTRICT JUDGE DISTRICT 24-3-01" — the shared MDJ regex
    accepts the optional "DISTRICT" keyword.
  - **School Director** headers ending in "SCHOOL DISTRICT" with optional
    leading NYR term token and optional " - <sub-muni>" qualifier:
      "SCHOOL DIRECTOR ALTOONA AREA SCHOOL DISTRICT"
      "SCHOOL DIRECTOR 2YR CLAYSBURG KIMMEL SCHOOL DISTRICT - GREENFIELD 1"
      "SCHOOL DIRECTOR WILLIAMSBURG COMMUNITY SCHOOL DISTRICT - CATHARINE TWP"
    Handled by a custom ``school_director_handler``.
  - **Title-case precinct names** ("Altoona Ward 1", "Allegheny Twp 1") —
    no prettifier needed.
"""

import re
from typing import Optional

from electionware_precinct_np import (
    ElectionwareConfig,
    TERM_TOKEN_RE,
    expand_muni_flexible,
    run_cli,
)


SKIP_PREFIXES = (
    "BLAIR COUNTY, PENNSYLVANIA OFFICIAL RESULTS",
    "Municipal Election",
    "November 4, 2025 Precinct Summary Results Report",
    "Precinct Summary - ",
    "Report generated with Electionware",
    "Statistics TOTAL",
    "Day Votes",
    "TOTAL Election",
    "Voter Turnout - Total",
    "Vote For ",
    "Total Votes Cast",
    "Contest Totals",
)


# --------------------------------------------------------------------------
# Inspector/Judge of Election overrides (strip embedded precinct name).
# --------------------------------------------------------------------------


def blair_overrides(line: str):
    for prefix, norm in (
        ("INSPECTOR OF ELECTION", "Inspector of Elections"),
        ("JUDGE OF ELECTION", "Judge of Elections"),
    ):
        if line == prefix or line.startswith(prefix + " "):
            return (norm, "")
    return None


# --------------------------------------------------------------------------
# Exact county offices.
# --------------------------------------------------------------------------


EXACT_OFFICES = {
    "JUDGE OF THE SUPERIOR COURT": ("Judge of the Superior Court", ""),
    "JUDGE OF THE COMMONWEALTH COURT": ("Judge of the Commonwealth Court", ""),
    "JUDGE OF THE COURT OF COMMON PLEAS": ("Judge of the Court of Common Pleas", ""),
    "DISTRICT ATTORNEY": ("District Attorney", ""),
    "PROTHONOTARY AND CLERK OF COURTS": ("Prothonotary and Clerk of Courts", ""),
    "SHERIFF": ("Sheriff", ""),
    "CORONER": ("Coroner", ""),
    "TREASURER": ("Treasurer", ""),
    "COUNTY TREASURER": ("Treasurer", ""),
    "CONTROLLER": ("Controller", ""),
    "REGISTER AND RECORDER": ("Register and Recorder", ""),
    "REGISTER & RECORDER": ("Register and Recorder", ""),
    "COUNTY COMMISSIONER": ("County Commissioner", ""),
}


# --------------------------------------------------------------------------
# Prefix-style local offices. Order matters: longest prefix wins, so
# "CITY COUNCIL" must appear before "COUNCIL".
# --------------------------------------------------------------------------


LOCAL_OFFICES = [
    ("INSPECTOR OF ELECTIONS", "Inspector of Elections"),
    ("INSPECTOR OF ELECTION", "Inspector of Elections"),
    ("JUDGE OF ELECTIONS", "Judge of Elections"),
    ("JUDGE OF ELECTION", "Judge of Elections"),
    ("CITY COUNCIL", "City Council"),
    ("COUNCIL", "Borough Council"),
    ("TAX COLLECTOR", "Tax Collector"),
    ("SUPERVISOR", "Township Supervisor"),
    ("AUDITOR", "Township Auditor"),
    ("CONSTABLE", "Constable"),
    ("MAYOR", "Mayor"),
]


# --------------------------------------------------------------------------
# School Director handler.
#
#   "SCHOOL DIRECTOR ALTOONA AREA SCHOOL DISTRICT"
#     -> ("School Director", "Altoona Area")
#   "SCHOOL DIRECTOR 2YR CLAYSBURG KIMMEL SCHOOL DISTRICT - GREENFIELD 1"
#     -> ("School Director (2 Year)", "Claysburg Kimmel - Greenfield 1")
#   "SCHOOL DIRECTOR WILLIAMSBURG COMMUNITY SCHOOL DISTRICT - CATHARINE TWP"
#     -> ("School Director", "Williamsburg Community - Catharine Township")
# --------------------------------------------------------------------------


def school_director(line: str):
    if not line.startswith("SCHOOL DIRECTOR"):
        return None
    core = line[len("SCHOOL DIRECTOR"):].strip()
    tokens = core.split()

    # Optional leading NYR term token.
    years: Optional[str] = None
    if tokens:
        tm = TERM_TOKEN_RE.match(tokens[0])
        if tm:
            years = tm.group(1)
            tokens = tokens[1:]

    # Strip the literal "SCHOOL DISTRICT" marker wherever it appears.
    # The marker may be followed by " - <sub-muni>" qualifier.
    remainder = " ".join(tokens)
    remainder = re.sub(r"\s*SCHOOL DISTRICT\s*", " ", remainder).strip()
    # Collapse double spaces and clean up leading/trailing dashes.
    remainder = re.sub(r"\s+", " ", remainder)
    remainder = remainder.strip(" -")

    district = expand_muni_flexible(remainder) if remainder else ""

    office = "School Director"
    if years:
        office += f" ({years} Year)"
    return (office, district)


CONFIG = ElectionwareConfig(
    county="Blair",
    skip_prefixes=SKIP_PREFIXES,
    county_header_suffix="BLAIR COUNTY, PENNSYLVANIA OFFICIAL RESULTS",
    exact_offices=EXACT_OFFICES,
    local_offices=LOCAL_OFFICES,
    local_office_orientation="prefix",
    retention_style="retention",
    extra_office_handlers=[blair_overrides],
    municipality_normalizer=expand_muni_flexible,
    school_director_handler=school_director,
)


if __name__ == "__main__":
    run_cli(CONFIG)
