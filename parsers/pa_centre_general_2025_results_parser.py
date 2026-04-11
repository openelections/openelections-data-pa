#!/usr/bin/env python3
"""
Parse Centre County PA 2025 General (Municipal) Election precinct results.

Source: Centre PA Precinct Summary_202511181456403510.pdf (Electionware
format with several Centre-specific quirks — most notably, no party
codes on candidate rows at all).

Usage:
    python parsers/pa_centre_general_2025_results_parser.py \\
        "<input.pdf>" "<output.csv>"

Uses the shared natural-pdf Electionware parser in
``electionware_precinct_np`` and supplies Centre-specific config.

Centre-specific quirks:
  - **No party codes on candidate rows.** The PDF omits party prefixes
    entirely ("BRANDON NEUMAN 168 135 33 0", not "DEM BRANDON NEUMAN").
    Enabled via the ``party_optional`` config flag added to the shared
    template; rows falling through the party regex are emitted with an
    empty party field.
  - **Retention headers use "LASTNAME" only**: "SUPREME COURT RETENTION
    - DONOHUE", plus an unusual "COMMON PLEAS COURT RETENTION - OLIVER"
    for the local Court of Common Pleas judge. Handled via an explicit
    map (same pattern as Lebanon / Mercer / Tioga).
  - **Hyphenated "N-YEAR INTERIM" term tokens**: "AUDITOR 2-YEAR INTERIM
    BURNSIDE TOWNSHIP", "COUNCIL MEMBER 2-YEAR INTERIM MILESBURG
    BOROUGH", "SCHOOL DIRECTOR 2-YEAR INTERIM STATE COLLEGE SCHOOL
    DISTRICT". A ``line_preprocessor`` rewrites "N-YEAR INTERIM" to the
    canonical "NYR" form so the shared prefix handler picks it up.
  - **Magisterial District Judge format**: "DISTRICT JUDGE MAGISTERIAL
    DISTRICT 49-3-02" — word order reversed from the standard
    "MAGISTERIAL DISTRICT JUDGE". Handled by an extra override.
  - **Singular JUDGE/INSPECTOR OF ELECTIONS with "N <precinct>" suffix**:
    "JUDGE OF ELECTIONS 1 BELLEFONTE NORTH". Normalized by stripping the
    trailing precinct number/name.
  - **"COUNCIL MEMBER WARD N" duplicated**: "COUNCIL MEMBER WARD 1
    BELLEFONTE WARD 1". Handled by an extra override emitting office
    "Borough Council" with district "Bellefonte Borough Ward 1".
  - **"SUPERVISOR AT LARGE <muni>"**: Ferguson Township's at-large
    township supervisor. Handled via an extra override.
  - **School Director variants** with duplicated region/district/at-large
    designators: "SCHOOL DIRECTOR REGION 1 BALD EAGLE SCHOOL DISTRICT
    REGION 1", "SCHOOL DIRECTOR DISTRICT 3 PHILIPSBURG-OSCEOLA SCHOOL
    DISTRICT, DISTRICT 3", "SCHOOL DIRECTOR AT LARGE PENNS VALLEY
    SCHOOL DISTRICT AT LARGE". Custom ``school_director_handler``.
  - **Precinct labels are numbered**: "1 BELLEFONTE NORTH",
    "24 STATE COLLEGE EAST 1 - PSU". Prettified via the Huntingdon-style
    ALL-CAPS title-caser.
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
    "PRECINCT SUMMARY REPORT OFFICIAL RESULTS",
    "PRECINCT SUMMARY REPORT UNOFFICIAL RESULTS",
    "2025 MUNICIPAL ELECTION",
    "NOVEMBER 4, 2025 CENTRE COUNTY",
    "Precinct Summary - ",
    "Report generated with Electionware",
    "Statistics TOTAL",          # column header row 1
    "N DAY NAL",                 # column header row 2 (wrapped)
    "TOTAL ELECTIO",             # column header row 1 (per-office)
    "Voter Turnout - Total",
    "Vote For ",
    "Total Votes Cast",
    "Contest Totals",
)


# --------------------------------------------------------------------------
# Line preprocessor: rewrite "N-YEAR INTERIM" term tokens to canonical NYR.
# --------------------------------------------------------------------------


INTERIM_TERM_RE = re.compile(r"\b(\d+)-YEAR\s+INTERIM\b")


def centre_line_preprocessor(line: str) -> str:
    return INTERIM_TERM_RE.sub(r"\1YR", line)


# --------------------------------------------------------------------------
# Retention: lastname-only form, includes a local Court of Common Pleas.
# --------------------------------------------------------------------------


CENTRE_RETENTION = {
    "SUPREME COURT RETENTION - DONOHUE": (
        "Supreme Court Retention - Christine Donohue",
        "",
    ),
    "SUPREME COURT RETENTION - DOUGHERTY": (
        "Supreme Court Retention - Kevin M Dougherty",
        "",
    ),
    "SUPREME COURT RETENTION - WECHT": (
        "Supreme Court Retention - David Wecht",
        "",
    ),
    "SUPERIOR COURT RETENTION - DUBOW": (
        "Superior Court Retention - Alice Beck Dubow",
        "",
    ),
    "COMMONWEALTH COURT RETENTION - WOJCIK": (
        "Commonwealth Court Retention - Michael H Wojcik",
        "",
    ),
    "COMMON PLEAS COURT RETENTION - OLIVER": (
        "Court of Common Pleas Retention - Jonathan D Grine Oliver",
        "",
    ),
}


def centre_retention(line: str):
    return CENTRE_RETENTION.get(line)


# --------------------------------------------------------------------------
# Other Centre-specific overrides.
# --------------------------------------------------------------------------


MDJ_RE = re.compile(r"^DISTRICT JUDGE MAGISTERIAL DISTRICT\s+(.+)$")
COUNCIL_WARD_RE = re.compile(r"^COUNCIL MEMBER WARD (\d+)\s+(.+?)\s+WARD \1$")
SUPERVISOR_AT_LARGE_RE = re.compile(r"^SUPERVISOR AT LARGE\s+(.+)$")


def centre_overrides(line: str):
    # Magisterial District Judge: reversed word order.
    m = MDJ_RE.match(line)
    if m:
        return ("Magisterial District Judge", m.group(1).strip())

    # Inspector/Judge of Elections with "N <precinct>" suffix — strip it.
    for prefix, norm in (
        ("INSPECTOR OF ELECTIONS", "Inspector of Elections"),
        ("INSPECTOR OF ELECTION", "Inspector of Elections"),
        ("JUDGE OF ELECTIONS", "Judge of Elections"),
        ("JUDGE OF ELECTION", "Judge of Elections"),
    ):
        if line == prefix or line.startswith(prefix + " "):
            return (norm, "")

    # "COUNCIL MEMBER WARD 1 BELLEFONTE WARD 1" — ward is duplicated.
    m = COUNCIL_WARD_RE.match(line)
    if m:
        ward = m.group(1)
        muni = expand_muni_flexible(m.group(2).strip())
        return ("Borough Council", f"{muni} Ward {ward}")

    # "SUPERVISOR AT LARGE FERGUSON TOWNSHIP"
    m = SUPERVISOR_AT_LARGE_RE.match(line)
    if m:
        muni = expand_muni_flexible(m.group(1).strip())
        return ("Township Supervisor At Large", muni)

    return None


# --------------------------------------------------------------------------
# Exact county offices.
# --------------------------------------------------------------------------


EXACT_OFFICES = {
    "JUDGE OF THE SUPERIOR COURT": ("Judge of the Superior Court", ""),
    "JUDGE OF THE COMMONWEALTH COURT": ("Judge of the Commonwealth Court", ""),
    "JUDGE OF THE COURT OF COMMON PLEAS": ("Judge of the Court of Common Pleas", ""),
    "DISTRICT ATTORNEY": ("District Attorney", ""),
    "JURY COMMISSIONER": ("Jury Commissioner", ""),
    "SHERIFF": ("Sheriff", ""),
    "CORONER": ("Coroner", ""),
    "TREASURER": ("Treasurer", ""),
    "COUNTY TREASURER": ("Treasurer", ""),
    "CONTROLLER": ("Controller", ""),
    "REGISTER AND RECORDER": ("Register and Recorder", ""),
    "PROTHONOTARY": ("Prothonotary", ""),
    "COUNTY COMMISSIONER": ("County Commissioner", ""),
}


# --------------------------------------------------------------------------
# Prefix-style local offices. Order matters: longest prefix wins.
# --------------------------------------------------------------------------


LOCAL_OFFICES = [
    ("COUNCIL MEMBER", "Borough Council"),
    ("TAX COLLECTOR", "Tax Collector"),
    ("SUPERVISOR", "Township Supervisor"),
    ("AUDITOR", "Township Auditor"),
    ("CONSTABLE", "Constable"),
    ("MAYOR", "Mayor"),
]


# --------------------------------------------------------------------------
# School Director handler.
#
#   "SCHOOL DIRECTOR BELLEFONTE SCHOOL DISTRICT"
#     -> ("School Director", "Bellefonte")
#   "SCHOOL DIRECTOR 2YR STATE COLLEGE SCHOOL DISTRICT"
#     -> ("School Director (2 Year)", "State College")
#   "SCHOOL DIRECTOR REGION 1 BALD EAGLE SCHOOL DISTRICT REGION 1"
#     -> ("School Director Region 1", "Bald Eagle")
#   "SCHOOL DIRECTOR DISTRICT 3 PHILIPSBURG-OSCEOLA SCHOOL DISTRICT, DISTRICT 3"
#     -> ("School Director District 3", "Philipsburg-Osceola")
#   "SCHOOL DIRECTOR AT LARGE PENNS VALLEY SCHOOL DISTRICT AT LARGE"
#     -> ("School Director At Large", "Penns Valley")
# --------------------------------------------------------------------------


AT_LARGE_RE = re.compile(r"^AT LARGE\b\s*(.*)$")
REGION_RE = re.compile(r"^REGION\s+(\d+)\b\s*(.*)$")
DISTRICT_RE = re.compile(r"^DISTRICT\s+(\d+)\b\s*(.*)$")


def school_director(line: str):
    if not line.startswith("SCHOOL DIRECTOR"):
        return None
    core = line[len("SCHOOL DIRECTOR"):].strip()

    # Optional leading NYR term token (after line_preprocessor rewrite).
    years: Optional[str] = None
    tokens = core.split()
    if tokens:
        tm = TERM_TOKEN_RE.match(tokens[0])
        if tm:
            years = tm.group(1)
            core = " ".join(tokens[1:])

    # Optional leading designator (REGION N / DISTRICT N / AT LARGE).
    designator = ""
    m = REGION_RE.match(core)
    if m:
        designator = f"Region {m.group(1)}"
        core = m.group(2).strip()
    else:
        m = DISTRICT_RE.match(core)
        if m:
            designator = f"District {m.group(1)}"
            core = m.group(2).strip()
        else:
            m = AT_LARGE_RE.match(core)
            if m:
                designator = "At Large"
                core = m.group(1).strip()

    # Strip the "SCHOOL DISTRICT" marker and any trailing duplicate designator.
    core = re.sub(r"\s*SCHOOL DISTRICT\b.*$", "", core).strip().rstrip(",")

    district = expand_muni_flexible(core) if core else ""

    office = "School Director"
    if designator:
        office += f" {designator}"
    if years:
        office += f" ({years} Year)"
    return (office, district)


CONFIG = ElectionwareConfig(
    county="Centre",
    skip_prefixes=SKIP_PREFIXES,
    county_header_suffix="CENTRE COUNTY",
    exact_offices=EXACT_OFFICES,
    local_offices=LOCAL_OFFICES,
    local_office_orientation="prefix",
    retention_style="retention",  # unused; handled by extra handler
    extra_office_handlers=[centre_retention, centre_overrides],
    municipality_normalizer=expand_muni_flexible,
    school_director_handler=school_director,
    include_magisterial=False,  # handled by centre_overrides
    line_preprocessor=centre_line_preprocessor,
    prettify_precinct=prettify_huntingdon_precinct,
    party_optional=True,
)


if __name__ == "__main__":
    run_cli(CONFIG)
