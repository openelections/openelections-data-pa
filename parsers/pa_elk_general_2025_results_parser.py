#!/usr/bin/env python3
"""
Parse Elk County PA 2025 General (Municipal) Election precinct results.

Source: Elk PA 2025_GPrecinct.pdf (Electionware format with several
Elk-specific quirks: suffix-style local offices, space-separated "N YR"
term tokens, duplicated-muni edge cases, and compact "RET <COURT>
COURT - LASTNAME" retention headers).

Usage:
    python parsers/pa_elk_general_2025_results_parser.py \\
        "<input.pdf>" "<output.csv>"

Uses the shared natural-pdf Electionware parser in
``electionware_precinct_np`` and supplies Elk-specific config.

Elk-specific quirks:
  - **Suffix-style local offices** with the municipality FIRST:
    "BENEZETTE TWP TAX COLLECTOR 4 YR",
    "JOHNSONBURG BOROUGH COUNCIL PERSON 4 YR",
    "CITY OF ST MARYS MAYOR 4 YR". Handled by
    ``local_office_orientation="suffix"``.
  - **Space-separated term tokens "N YR"** (e.g. "4 YR", "6 YR") — the
    shared ``TERM_TOKEN_RE`` expects the canonical "NYR" form, so a
    ``line_preprocessor`` collapses the whitespace.
  - **Duplicated trailing municipality**: "HIGHLAND TWP AUDITOR 2 YR
    HIGHLAND TWP", "MILLSTONE TWP SUPERVISOR 6 YR MILLSTONE TWP" — the
    muni token is repeated at the end. The preprocessor strips the
    duplicate via a ``^(.+?) (.+?) (\\d+YR) \\1$`` rewrite.
  - **Retention headers** use the compact form "RET SUPREME COURT -
    DONOHUE" (Elk's own abbreviation). Handled via an explicit map in
    ``extra_office_handlers``.
  - **"COUNCIL PERSON"** is Elk's label for Borough Council; normalized
    to "Borough Council". Ordered before bare "COUNCIL" so the longest
    suffix wins.
  - **"CITY OF ST MARYS" offices** — Elk's only city. Three specific
    exact_offices entries for Council, Mayor, and Tax Collector so they
    come out as "City Council"/"Mayor"/"Tax Collector" with district
    "City of St Marys" instead of being mis-mapped via the borough
    suffix handler.
  - **Singular JUDGE OF ELECTIONS / INSPECTOR OF ELECTIONS** with the
    precinct name embedded as prefix ("BENEZETTE INSPECTOR OF
    ELECTIONS 4 YR"). The precinct is already on each row, so an extra
    handler emits district="" (same pattern as Lebanon / Tioga).
  - **School Director headers** in compound form "<DISTRICT> AREA
    SCHOOL DIRECTOR - <REGION|AT LARGE> <N YR>". Custom
    ``school_director_handler`` parses the pattern.
  - **Ballot question**: "ST MARYS PROPOSED CODE AMENDMENT 1" — a local
    charter amendment in St Marys. Handled as an extra override.
  - **"FOX #1", "FOX #2"** — precinct labels with "#" — passed through
    the Huntingdon-style prettifier unchanged (regex preserves
    punctuation).
"""

import re
from typing import Optional

from electionware_precinct_np import (
    ElectionwareConfig,
    TERM_TOKEN_RE,
    expand_muni_flexible,
    prettify_huntingdon_precinct,
    run_cli,
    title_case,
)


SKIP_PREFIXES = (
    "Summary Results Report OFFICIAL RESULTS",
    "Summary Results Report UNOFFICIAL RESULTS",
    "MUNICIPAL GENERAL",
    "NOVEMBER 4, 2025 Elk",
    "Precinct Summary - ",
    "Report generated with Electionware",
    "Election Absentee/",
    "Day Mail-In",
    "TOTAL Ele",
    "Voter Turnout - Total",
    "Vote For ",
    "Total Votes Cast",
    "Contest Totals",
    "Overvotes",
    "Undervotes",
)


# --------------------------------------------------------------------------
# Line preprocessor: collapse "N YR" -> "NYR" and strip duplicated
# trailing muni tokens.
# --------------------------------------------------------------------------


YR_SPACE_RE = re.compile(r"\b(\d+)\s+YR\b")
DUPLICATED_MUNI_RE = re.compile(r"^(.+?)\s+(.+?)\s+(\d+YR)\s+\1$")


def elk_line_preprocessor(line: str) -> str:
    line = YR_SPACE_RE.sub(r"\1YR", line)
    m = DUPLICATED_MUNI_RE.match(line)
    if m:
        line = f"{m.group(1)} {m.group(2)} {m.group(3)}"
    return line


# --------------------------------------------------------------------------
# Retention map: "RET <COURT> COURT - LASTNAME".
# --------------------------------------------------------------------------


ELK_RETENTION = {
    "RET SUPREME COURT - DONOHUE": (
        "Supreme Court Retention - Christine Donohue",
        "",
    ),
    "RET SUPREME COURT - DOUGHERTY": (
        "Supreme Court Retention - Kevin M Dougherty",
        "",
    ),
    "RET SUPREME COURT - WECHT": (
        "Supreme Court Retention - David Wecht",
        "",
    ),
    "RET SUPERIOR COURT - DUBOW": (
        "Superior Court Retention - Alice Beck Dubow",
        "",
    ),
    "RET COMMONWEALTH COURT - WOJCIK": (
        "Commonwealth Court Retention - Michael H Wojcik",
        "",
    ),
}


def elk_retention(line: str):
    return ELK_RETENTION.get(line)


# --------------------------------------------------------------------------
# Other Elk-specific overrides.
# --------------------------------------------------------------------------


INSPECTOR_SUFFIX_RE = re.compile(
    r"\bINSPECTOR OF ELECTIONS(?:\s+\d+YR)?$"
)
JUDGE_SUFFIX_RE = re.compile(
    r"\bJUDGE OF ELECTIONS(?:\s+\d+YR)?$"
)
BALLOT_QUESTION_RE = re.compile(
    r"^ST MARYS PROPOSED CODE AMENDMENT\s+(\d+)$"
)


def elk_overrides(line: str):
    # Ballot question (runs before suffix match so the muni context is preserved).
    m = BALLOT_QUESTION_RE.match(line)
    if m:
        return (f"Proposed Code Amendment {m.group(1)}", "City of St Marys")

    # Inspector/Judge of Elections with embedded precinct — emit district="".
    if INSPECTOR_SUFFIX_RE.search(line):
        tm = re.search(r"\b(\d+)YR$", line)
        years = tm.group(1) if tm else None
        office = "Inspector of Elections"
        if years:
            office += f" ({years} Year)"
        return (office, "")
    if JUDGE_SUFFIX_RE.search(line):
        tm = re.search(r"\b(\d+)YR$", line)
        years = tm.group(1) if tm else None
        office = "Judge of Elections"
        if years:
            office += f" ({years} Year)"
        return (office, "")

    return None


# --------------------------------------------------------------------------
# Exact county/city offices (after line_preprocessor rewrites "N YR" -> "NYR").
# --------------------------------------------------------------------------


EXACT_OFFICES = {
    "JUDGE OF THE SUPERIOR COURT": ("Judge of the Superior Court", ""),
    "JUDGE OF THE COMMONWEALTH COURT": ("Judge of the Commonwealth Court", ""),
    "JUDGE OF THE COURT OF COMMON PLEAS": ("Judge of the Court of Common Pleas", ""),
    "DISTRICT ATTORNEY": ("District Attorney", ""),
    "SHERIFF": ("Sheriff", ""),
    "CORONER": ("Coroner", ""),
    "TREASURER": ("Treasurer", ""),
    "COUNTY TREASURER": ("Treasurer", ""),
    "CONTROLLER": ("Controller", ""),
    "PROTHONOTARY": ("Prothonotary", ""),
    "COUNTY COMMISSIONER": ("County Commissioner", ""),
    # Elk's sole city (St Marys) — override the generic borough suffix handling.
    "CITY OF ST MARYS COUNCIL 4YR": ("City Council (4 Year)", "City of St Marys"),
    "CITY OF ST MARYS MAYOR 4YR": ("Mayor (4 Year)", "City of St Marys"),
    "CITY OF ST MARYS TAX COLLECTOR 4YR": (
        "Tax Collector (4 Year)",
        "City of St Marys",
    ),
}


# --------------------------------------------------------------------------
# Suffix-style local offices. Order matters: longest suffix wins, so
# "COUNCIL PERSON" must be checked before bare "COUNCIL".
# --------------------------------------------------------------------------


LOCAL_OFFICES = [
    ("COUNCIL PERSON", "Borough Council"),
    ("TAX COLLECTOR", "Tax Collector"),
    ("SUPERVISOR", "Township Supervisor"),
    ("AUDITOR", "Township Auditor"),
    ("CONSTABLE", "Constable"),
    ("MAYOR", "Mayor"),
]


# --------------------------------------------------------------------------
# School Director handler.
#
#   "BROCKWAY AREA SCHOOL DIRECTOR - REGION 2 4YR"
#     -> ("School Director Region 2 (4 Year)", "Brockway Area")
#   "JOHNSONBURG AREA SCHOOL DIRECTOR - AT LARGE 4YR"
#     -> ("School Director At Large (4 Year)", "Johnsonburg Area")
#   "ST MARYS AREA SCHOOL DIRECTOR - REGION 1 2YR"
#     -> ("School Director Region 1 (2 Year)", "St Marys Area")
# --------------------------------------------------------------------------


SCHOOL_RE = re.compile(
    r"^(.+?)\s+SCHOOL DIRECTOR\s*-\s*(.+?)(?:\s+(\d+YR))?$"
)


def school_director(line: str):
    m = SCHOOL_RE.match(line)
    if not m:
        return None
    district = expand_muni_flexible(m.group(1).strip())
    designator_raw = m.group(2).strip()
    years = m.group(3)

    # Normalize designator: "REGION N", "REGION B", "AT LARGE".
    if designator_raw.upper().startswith("REGION "):
        designator = "Region " + designator_raw.split(None, 1)[1]
    elif designator_raw.upper() == "AT LARGE":
        designator = "At Large"
    else:
        designator = title_case(designator_raw)

    office = f"School Director {designator}"
    if years:
        office += f" ({years[:-2]} Year)"
    return (office, district)


CONFIG = ElectionwareConfig(
    county="Elk",
    skip_prefixes=SKIP_PREFIXES,
    county_header_suffix="Elk",
    exact_offices=EXACT_OFFICES,
    local_offices=LOCAL_OFFICES,
    local_office_orientation="suffix",
    retention_style="retention",  # unused; handled by extra handler
    extra_office_handlers=[elk_retention, elk_overrides],
    municipality_normalizer=expand_muni_flexible,
    school_director_handler=school_director,
    line_preprocessor=elk_line_preprocessor,
    prettify_precinct=prettify_huntingdon_precinct,
)


if __name__ == "__main__":
    run_cli(CONFIG)
