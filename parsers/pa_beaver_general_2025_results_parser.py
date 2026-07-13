#!/usr/bin/env python3
"""
Parser for Beaver County, PA 2025 General Election precinct results.

Source: Beaver PA Municipal-Election-Precinct-Results-2025-11-26-25_Official_Final.pdf (Electionware format).

Usage:
    python parsers/pa_beaver_general_2025_results_parser.py \\
        "<input.pdf>" "<output.csv>"

Uses the shared natural-pdf Electionware parser in
``electionware_precinct_np`` -- see that module's docstring for the
ElectionwareConfig hooks available (office_handlers, municipality_normalizer,
prettifier, exact_offices, local_offices, retention_style, etc.)

Beaver-specific quirks:
  - Term tokens are parenthesized with a hyphen ("(6-YR)") rather than the
    bare "6YR" the shared TERM_TOKEN_RE expects; a line_preprocessor
    rewrites them to "6YR" before office normalization.
  - Retention races read "JUDICIAL RETENTION QUESTION <NAME>" rather than
    the shared "<COURT> COURT RETENTION - <NAME>" pattern; handled via an
    explicit name->office map (extra_office_handlers).
  - "INSPECTOR OF ELECTIONS <PRECINCT>" (plural) and "JUDGE OF ELECTION
    <PRECINCT>" (singular) both embed the precinct name; stripped via an
    extra handler (same idea as Franklin's).
  - Local offices ("MEMBER OF COUNCIL", "TOWNSHIP COMMISSIONER",
    "CONTROLLER") are prefix-style with an ALL-CAPS trailing municipality,
    so the default title_case municipality_normalizer is used (unlike
    Franklin, whose trailing munis are already mixed-case).
  - School Director headers: "SCHOOL DIRECTOR (N-YR) <DISTRICT> SCHOOL
    DISTRICT [REGION N]" -- a county-specific handler title-cases the
    ALL-CAPS district name (Franklin's district names were already
    mixed-case, so its handler didn't need to).
"""

import re
from typing import Optional

from electionware_precinct_np import (
    ElectionwareConfig,
    TERM_TOKEN_RE,
    run_cli,
    title_case,
)


SKIP_PREFIXES = (
    "Precinct Summary Results Report OFFICIAL RESULTS",
    "2025 Municipal Election",
    "November 4, 2025 Beaver County",
    "Precinct Summary - ",
    "Report generated with Electionware",
    "Vote For ",
    "Total Votes Cast",
    # Column headers -- Beaver's rotated "Election Day"/"Provisional"
    # column labels get interleaved with adjacent columns by text
    # extraction and wrap across 1-3 lines depending on table width.
    # ("Write-In Totals", "Not Assigned", "Overvotes", "Undervotes" are
    # intentionally NOT skipped here -- the shared engine matches and
    # emits those as real rows further down in parse_precinct_rows.)
    "TOTAL",
    "Election",
    "Day",
    "Mail Votes",
    "Provisional",
    "Pro V",
)


TERM_TOKEN_PAREN_RE = re.compile(r"\((\d+)-YR\)", re.IGNORECASE)


def beaver_line_preprocessor(line: str) -> str:
    return TERM_TOKEN_PAREN_RE.sub(lambda m: f"{m.group(1)}YR", line)


# --------------------------------------------------------------------------
# Retention: "JUDICIAL RETENTION QUESTION <NAME>" -- a fixed set of six
# justices/judges for the 2025 general (three statewide Supreme Court
# retentions, one Superior, one Commonwealth, one local Common Pleas).
# --------------------------------------------------------------------------


BEAVER_RETENTION = {
    "JUDICIAL RETENTION QUESTION CHRISTINE DONOHUE": (
        "Supreme Court Retention - Christine Donohue",
        "",
    ),
    "JUDICIAL RETENTION QUESTION DAVID WECHT": (
        "Supreme Court Retention - David Wecht",
        "",
    ),
    "JUDICIAL RETENTION QUESTION KEVIN M DOUGHERTY": (
        "Supreme Court Retention - Kevin M Dougherty",
        "",
    ),
    "JUDICIAL RETENTION QUESTION ALICE BECK DUBOW": (
        "Superior Court Retention - Alice Beck Dubow",
        "",
    ),
    "JUDICIAL RETENTION QUESTION MICHAEL H WOJCIK": (
        "Commonwealth Court Retention - Michael H Wojcik",
        "",
    ),
    "JUDICIAL RETENTION QUESTION DALE M FOUSE": (
        "Court of Common Pleas Retention - Dale M Fouse",
        "",
    ),
}


def beaver_retention(line: str):
    return BEAVER_RETENTION.get(line)


# --------------------------------------------------------------------------
# Inspector/Judge of Election overrides -- strip embedded precinct.
# --------------------------------------------------------------------------


def beaver_inspector_judge(line: str):
    for prefix, norm in (
        ("INSPECTOR OF ELECTIONS", "Inspector of Elections"),
        ("JUDGE OF ELECTION", "Judge of Election"),
    ):
        if line == prefix or line.startswith(prefix + " "):
            rest = line[len(prefix):].strip()
            return (norm, "") if not rest else (norm, "")
    return None


# --------------------------------------------------------------------------
# Exact county (statewide) offices.
# --------------------------------------------------------------------------


EXACT_OFFICES = {
    "JUDGE OF THE SUPERIOR COURT": ("Judge of the Superior Court", ""),
    "JUDGE OF THE COMMONWEALTH COURT": ("Judge of the Commonwealth Court", ""),
}


# --------------------------------------------------------------------------
# Prefix-style local offices, ALL-CAPS trailing municipality (default
# title_case municipality_normalizer handles casing).
# --------------------------------------------------------------------------


LOCAL_OFFICES = [
    ("TOWNSHIP SUPERVISOR", "Township Supervisor"),
    ("TOWNSHIP COMMISSIONER", "Township Commissioner"),
    ("MEMBER OF COUNCIL", "Borough Council"),
    ("TAX COLLECTOR", "Tax Collector"),
    ("CONTROLLER", "Controller"),
    ("AUDITOR", "Auditor"),
    ("MAYOR", "Mayor"),
]


# --------------------------------------------------------------------------
# School Director handler.
#
# Variants:
#   "SCHOOL DIRECTOR 2YR ALIQUIPPA SCHOOL DISTRICT"
#     -> ("School Director (2 Year)", "Aliquippa")
#   "SCHOOL DIRECTOR 4YR BLACKHAWK SCHOOL DISTRICT REGION 1"
#     -> ("School Director Region 1 (4 Year)", "Blackhawk")
# --------------------------------------------------------------------------


SCHOOL_PREFIX_RE = re.compile(r"^SCHOOL DIRECTOR\s+(.+)$")
SCHOOL_DISTRICT_MARKER_RE = re.compile(r"\s+SCHOOL DISTRICT\b", re.IGNORECASE)


def beaver_school_director(line: str):
    m = SCHOOL_PREFIX_RE.match(line)
    if not m:
        return None
    rest = m.group(1).strip()

    years: Optional[str] = None
    tokens = rest.split()
    if tokens:
        tm = TERM_TOKEN_RE.match(tokens[0])
        if tm:
            years = tm.group(1)
            rest = " ".join(tokens[1:])

    parts = SCHOOL_DISTRICT_MARKER_RE.split(rest, maxsplit=1)
    district_name = title_case(parts[0].strip().rstrip(","))
    tail = parts[1].strip() if len(parts) > 1 else ""

    designator = ""
    if tail:
        m2 = re.match(r"^REGION\s+(\S+)\s*$", tail, re.IGNORECASE)
        if m2:
            designator = f"Region {m2.group(1)}"

    office = "School Director"
    if designator:
        office += f" {designator}"
    if years:
        office += f" ({years} Year)"
    return (office, district_name)


CONFIG = ElectionwareConfig(
    county="Beaver",
    skip_prefixes=SKIP_PREFIXES,
    county_header_suffix="Beaver County",
    exact_offices=EXACT_OFFICES,
    local_offices=LOCAL_OFFICES,
    local_office_orientation="prefix",
    extra_office_handlers=[beaver_retention, beaver_inspector_judge],
    school_director_handler=beaver_school_director,
    line_preprocessor=beaver_line_preprocessor,
    prettify_precinct=title_case,
)


if __name__ == "__main__":
    run_cli(CONFIG)
