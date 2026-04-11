#!/usr/bin/env python3
"""
Parse Clearfield County PA 2025 General (Municipal) Election precinct results.

Source: Clearfield PA 2025 mun election precinct official dec 10_...pdf
(508 pages, standard Electionware format, ALL-CAPS headers, prefix-style
local offices).

Usage:
    python parsers/pa_clearfield_general_2025_results_parser.py \\
        "<input.pdf>" "<output.csv>"

Uses the shared natural-pdf Electionware parser in
``electionware_precinct_np`` and supplies Clearfield-specific config.

Clearfield-specific quirks:
  - **"MEMBER OF COUNCIL"** is Clearfield's label for Borough Council;
    normalized to "Borough Council". "MEMBER OF COUNCIL CITY OF DUBOIS"
    is the only city — normalized to "City Council" with district
    "Dubois City".
  - **Space-separated "N YR" term tokens** mid-header: "AUDITOR 2 YR
    BECCARIA TOWNSHIP", "SUPERVISOR 6 YR BLOOM TOWNSHIP", "MEMBER OF
    COUNCIL BRISBIN BOROUGH 2 YR". A ``line_preprocessor`` collapses
    "N YR" -> "NYR" so the shared handler picks them up (leading for
    AUDITOR/SUPERVISOR, trailing for MEMBER OF COUNCIL).
  - **Header typos** fixed in the line_preprocessor:
      "MEMVER OF COUNCIL IRVONA BOROUGH"   -> MEMBER
      "MEMBER OF COUNCIL BURSIDE BOROUGH"  -> BURNSIDE
      "MEMBER OF COUNCIL NEWBURGH BOROUGH" -> NEWBURG
  - **Retention headers** use the unusual "STATE SUPREME COURT RETENTION
    QUESTION #N - <NAME>" form plus a typo ("DONAHUE" for Donohue).
    Handled via an explicit map in ``extra_office_handlers``.
  - **"PROTHONOTARY and CLERK OF COURTS"** uses lowercase "and".
  - **"CITY TREASURER DUBOIS CITY"** — local city treasurer office.
    Handled via ``CITY TREASURER`` prefix in the local offices list.
  - **Singular JUDGE OF ELECTION / INSPECTOR OF ELECTION** with the
    precinct name embedded — normalized to the canonical plural form via
    an extra handler that emits district="".
  - **Precinct labels** include compound forms like
    "BECCARIA TOWNSHIP - 1ST PRECINCT", "CLEARFIELD BOROUGH - 1ST WARD",
    "SANDY TOWNSHIP - FALLS CREEK". A custom prettifier title-cases
    ALL-CAPS runs and lowers ordinal suffixes ("1St" -> "1st").
  - **School Director headers**: "SCHOOL DIRECTOR <DISTRICT> AREA SCHOOL
    DISTRICT [<AT LARGE | - DISTRICT X | - REGION X>] [N YR]".
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
    "Precinct Results Report OFFICIAL RESULTS",
    "Precinct Results Report UNOFFICIAL RESULTS",
    "2025 Municipal Election",
    "November 4, 2025 Clearfield",
    "Precinct Summary - ",
    "Report generated with Electionware",
    "ELECTION ABSENTEE/",
    "DAY MAIL-IN",
    "TOTAL",
    "Voter Turnout - Total",
    "Vote For ",
    "Total Votes Cast",
    "Contest Totals",
    "Overvotes",
    "Undervotes",
    "Not Assigned",
)


# --------------------------------------------------------------------------
# Line preprocessor: collapse "N YR" -> "NYR" and fix header typos.
# --------------------------------------------------------------------------


YR_SPACE_RE = re.compile(r"\b(\d+)\s+YR\b")


def clearfield_line_preprocessor(line: str) -> str:
    line = YR_SPACE_RE.sub(r"\1YR", line)
    # Typos in council headers.
    line = line.replace("MEMVER OF COUNCIL", "MEMBER OF COUNCIL")
    line = line.replace(
        "MEMBER OF COUNCIL BURSIDE BOROUGH",
        "MEMBER OF COUNCIL BURNSIDE BOROUGH",
    )
    line = line.replace(
        "MEMBER OF COUNCIL NEWBURGH BOROUGH",
        "MEMBER OF COUNCIL NEWBURG BOROUGH",
    )
    return line


# --------------------------------------------------------------------------
# Retention: "STATE SUPREME COURT RETENTION QUESTION #N - <NAME>" plus
# non-Supreme variants. One justice name is a typo ("DONAHUE").
# --------------------------------------------------------------------------


CLEARFIELD_RETENTION = {
    "STATE SUPREME COURT RETENTION QUESTION #1 - CHRISTINE DONAHUE": (
        "Supreme Court Retention - Christine Donohue",
        "",
    ),
    "STATE SUPREME COURT RETENTION QUESTION #2 - KEVIN M DOUGHERTY": (
        "Supreme Court Retention - Kevin M Dougherty",
        "",
    ),
    "STATE SUPREME COURT RETENTION QUESTION #3 - DAVID WECHT": (
        "Supreme Court Retention - David Wecht",
        "",
    ),
    "SUPERIOR COURT RETENTION QUESTION - ALICE BECK DUBOW": (
        "Superior Court Retention - Alice Beck Dubow",
        "",
    ),
    "COMMONWEALTH COURT RETENTION QUESTION - MICHAEL H WOJCIK": (
        "Commonwealth Court Retention - Michael H Wojcik",
        "",
    ),
}


def clearfield_retention(line: str):
    return CLEARFIELD_RETENTION.get(line)


# --------------------------------------------------------------------------
# Inspector/Judge of Election overrides — strip embedded precinct.
# --------------------------------------------------------------------------


def clearfield_inspector_judge(line: str):
    for prefix, norm in (
        ("INSPECTOR OF ELECTION", "Inspector of Elections"),
        ("JUDGE OF ELECTION", "Judge of Elections"),
    ):
        if line == prefix or line.startswith(prefix + " "):
            # Avoid swallowing "JUDGE OF THE ..." court offices.
            rest = line[len(prefix):].lstrip()
            if rest.upper().startswith(("THE ", "OF ")):
                continue
            return (norm, "")
    return None


# --------------------------------------------------------------------------
# "MEMBER OF COUNCIL CITY OF DUBOIS" -> City Council for DuBois City.
# --------------------------------------------------------------------------


def clearfield_dubois_council(line: str):
    if line == "MEMBER OF COUNCIL CITY OF DUBOIS":
        return ("City Council", "Dubois City")
    return None


# --------------------------------------------------------------------------
# Exact county / city offices.
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
    "COUNTY CONTROLLER": ("Controller", ""),
    "PROTHONOTARY": ("Prothonotary", ""),
    "PROTHONOTARY and CLERK OF COURTS": ("Prothonotary and Clerk of Courts", ""),
    "PROTHONOTARY AND CLERK OF COURTS": ("Prothonotary and Clerk of Courts", ""),
    "REGISTER AND RECORDER": ("Register and Recorder", ""),
    "COUNTY COMMISSIONER": ("County Commissioner", ""),
}


# --------------------------------------------------------------------------
# Prefix-style local offices. Order matters: longest prefix wins, so
# "MEMBER OF COUNCIL" must come before any bare "COUNCIL", and
# "CITY TREASURER" before "TREASURER" (though TREASURER is already in
# exact_offices so the order matters only for MEMBER OF COUNCIL).
# --------------------------------------------------------------------------


LOCAL_OFFICES = [
    ("MEMBER OF COUNCIL", "Borough Council"),
    ("CITY TREASURER", "City Treasurer"),
    ("TAX COLLECTOR", "Tax Collector"),
    ("SUPERVISOR", "Township Supervisor"),
    ("AUDITOR", "Township Auditor"),
    ("CONSTABLE", "Constable"),
    ("MAYOR", "Mayor"),
]


# --------------------------------------------------------------------------
# School Director handler.
#
#   "SCHOOL DIRECTOR CLEARFIELD AREA SCHOOL DISTRICT AT LARGE"
#     -> ("School Director At Large", "Clearfield Area")
#   "SCHOOL DIRECTOR CLEARFIELD AREA SCHOOL DISTRICT AT LARGE 2YR"
#     -> ("School Director At Large (2 Year)", "Clearfield Area")
#   "SCHOOL DIRECTOR DUBOIS AREA SCHOOL DISTRICT - DISTRICT A"
#     -> ("School Director District A", "Dubois Area")
#   "SCHOOL DIRECTOR GLENDALE AREA SCHOOL DISTRICT - REGION II"
#     -> ("School Director Region II", "Glendale Area")
#   "SCHOOL DIRECTOR PHILIPSBURG-OSCEOLA AREA SCHOOL DISTRICT - REGION 7"
#     -> ("School Director Region 7", "Philipsburg-Osceola Area")
# --------------------------------------------------------------------------


SCHOOL_RE = re.compile(
    r"^SCHOOL DIRECTOR\s+(.+?)\s+SCHOOL DISTRICT(?:\s*-\s*(.+?))?(?:\s+AT LARGE)?(?:\s+(\d+YR))?$"
)
# More permissive: run two-phase extraction.
SCHOOL_PREFIX_RE = re.compile(r"^SCHOOL DIRECTOR\s+(.+)$")


def school_director(line: str):
    m = SCHOOL_PREFIX_RE.match(line)
    if not m:
        return None
    rest = m.group(1).strip()

    # Trailing NNYR term token.
    years: Optional[str] = None
    tm = re.search(r"\s+(\d+YR)$", rest)
    if tm:
        years = tm.group(1)[:-2]
        rest = rest[: tm.start()].strip()

    # Split on "SCHOOL DISTRICT".
    parts = re.split(r"\s+SCHOOL DISTRICT\b", rest, maxsplit=1)
    district_raw = parts[0].strip()
    tail = parts[1].strip() if len(parts) > 1 else ""

    # Designator (AT LARGE / - DISTRICT X / - REGION X) comes from the
    # tail OR from the end of district_raw if no tail.
    designator = ""
    if tail:
        t = tail.lstrip("- ").strip()
        if t.upper().startswith("AT LARGE"):
            designator = "At Large"
        elif t.upper().startswith("DISTRICT "):
            designator = "District " + t[len("DISTRICT "):].strip()
        elif t.upper().startswith("REGION "):
            designator = "Region " + t[len("REGION "):].strip()
        else:
            designator = title_case(t)
    else:
        # "AT LARGE" might precede "SCHOOL DISTRICT"... but here it doesn't.
        pass

    # district_raw might still have trailing "AT LARGE" if the header was
    # "<NAME> SCHOOL DISTRICT AT LARGE" (in which case the split put "AT
    # LARGE" into tail, handled above).
    district = expand_muni_flexible(district_raw + " Area").replace(
        " Area Area", " Area"
    )
    # expand_muni_flexible title-cases; district_raw already ends with "AREA"
    # so we don't actually need to append. Redo:
    district = expand_muni_flexible(district_raw)

    office = "School Director"
    if designator:
        office += f" {designator}"
    if years:
        office += f" ({years} Year)"
    return (office, district)


# --------------------------------------------------------------------------
# Precinct prettifier.
# --------------------------------------------------------------------------


ORDINAL_RE = re.compile(r"(\d)(St|Nd|Rd|Th)\b")


def prettify_clearfield_precinct(name: str) -> str:
    s = prettify_huntingdon_precinct(name)
    # "1St Precinct" -> "1st Precinct"
    s = ORDINAL_RE.sub(lambda m: m.group(1) + m.group(2).lower(), s)
    return s


CONFIG = ElectionwareConfig(
    county="Clearfield",
    skip_prefixes=SKIP_PREFIXES,
    county_header_suffix="Clearfield County",
    exact_offices=EXACT_OFFICES,
    local_offices=LOCAL_OFFICES,
    local_office_orientation="prefix",
    retention_style="retention",  # unused; handled by extra handler
    extra_office_handlers=[
        clearfield_retention,
        clearfield_dubois_council,
        clearfield_inspector_judge,
    ],
    municipality_normalizer=expand_muni_flexible,
    school_director_handler=school_director,
    line_preprocessor=clearfield_line_preprocessor,
    prettify_precinct=prettify_clearfield_precinct,
)


if __name__ == "__main__":
    run_cli(CONFIG)
