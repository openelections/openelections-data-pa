#!/usr/bin/env python3
"""
Parse Franklin County PA 2025 General (Municipal) Election precinct results.

Source: Franklin PA PrecinctDetail.pdf (576 pages, Electionware format
with several Franklin-specific quirks: extra VOTE % column, mixed-case
candidate names and local office munis, compact precinct labels, and
typo'd municipality names).

Usage:
    python parsers/pa_franklin_general_2025_results_parser.py \\
        "<input.pdf>" "<output.csv>"

Uses the shared natural-pdf Electionware parser in
``electionware_precinct_np`` and supplies Franklin-specific config.

Franklin-specific quirks:
  - **Extra VOTE % column** (like Tioga): candidate and aggregate rows
    include ``N.NN%`` between the total and the election-day column.
    Stripped by a ``line_preprocessor``.
  - **Mixed-case candidate names**: "DEM Brandon Neuman 175 ..." — the
    shared PARTY_RE is case-insensitive on the name tail, so no change
    needed.
  - **Compact precinct labels**: "ANTRIM 1", "CHAMBERSBURG WD 1 DIST 1",
    "ST THOMAS 1", "MONT ALTO", "FANNETT DRY RUN", "SOUTHAMPTON MT ROCK".
    A custom prettifier title-cases ALL-CAPS runs and expands
    WD -> Ward, DIST -> District, ST -> St, MT -> Mt.
  - **Prefix-style local offices with mixed-case trailing munis**:
    "AUDITOR 2YR Hamilton", "TOWNSHIP SUPERVISOR St. Thomas",
    "COUNCILPERSON Chambersburg 1st", "MAYOR Mont Alto". Handled by the
    shared prefix handler with ``municipality_normalizer=identity`` since
    the trailing text is already title-cased.
  - **"COUNCILPERSON"** is Franklin's label for Borough Council;
    normalized to "Borough Council".
  - **"TOWNSHIP SUPERVISOR"** — office is a 2-word prefix; normalized to
    "Township Supervisor".
  - **Header typos** fixed in ``line_preprocessor``:
      "Mercerburg"   -> "Mercersburg"   (office: COUNCILPERSON / MAYOR /
                                         TAX COLLECTOR Mercerburg)
      "Shippenburg"  -> "Shippensburg"  (COUNCILPERSON / TAX COLLECTOR
                                         West End Shippenburg)
  - **Retention headers** include the local "COURT OF COMMON PLEAS
    RETENTION - TODD M. SPONSELLER"; the three Supreme Court justices
    use the compact ALL-CAPS form. Handled via an explicit map.
  - **Magisterial District Judge** header uses a "#" in the district
    number: "MAGISTERIAL DISTRICT JUDGE #39-3-04". The shared MDJ regex
    keeps the "#" in the district, which matches the source.
  - **Singular JUDGE OF ELECTION / INSPECTOR OF ELECTION** with the
    precinct embedded — stripped via an extra handler.
  - **School Director headers** come in three variants:
      "SCHOOL DIRECTOR 2YR Fannett-Metal School District"
        -> ("School Director (2 Year)", "Fannett-Metal")
      "SCHOOL DIRECTOR Chambersburg Area School District Region 1"
        -> ("School Director Region 1", "Chambersburg Area")
      "SCHOOL DIRECTOR Waynesboro Area School District Waynesboro Borough"
        -> ("School Director", "Waynesboro Area - Waynesboro Borough")
"""

import re
from typing import Optional

from electionware_precinct_np import (
    ElectionwareConfig,
    TERM_TOKEN_RE,
    identity,
    run_cli,
    title_case,
)


SKIP_PREFIXES = (
    "Summary Results Report OFFICIAL RESULTS",
    "Summary Results Report UNOFFICIAL RESULTS",
    "Municipal Election",
    "November 4, 2025 FRANKLIN",
    "Precinct Summary - ",
    "Report generated with Electionware",
    # Column headers (wrapped across 2-3 rows).
    "Provisional",
    "TOTAL Election Day Mail Votes",
    "Votes",
    "Election Provisional",
    "TOTAL VOTE %",
    "Day Votes",
    "Voter Turnout - Total",
    "Vote For ",
    "Total Votes Cast",
    "Contest Totals",
    "Overvotes",
    "Undervotes",
    "Not Assigned",
)


# --------------------------------------------------------------------------
# Line preprocessor:
#   - strip the extra " N.NN%" VOTE % column
#   - fix "Mercerburg" -> "Mercersburg" and "Shippenburg" -> "Shippensburg"
# --------------------------------------------------------------------------

PCT_RE = re.compile(r"\s+\d+\.\d+%")


def franklin_line_preprocessor(line: str) -> str:
    line = PCT_RE.sub("", line)
    line = line.replace("Mercerburg", "Mercersburg")
    line = line.replace("Shippenburg", "Shippensburg")
    return line


# --------------------------------------------------------------------------
# Retention: ALL-CAPS "<COURT> RETENTION - <NAME>" plus a local Common
# Pleas judge retention.
# --------------------------------------------------------------------------


FRANKLIN_RETENTION = {
    "SUPREME COURT RETENTION - CHRISTINE DONOHUE": (
        "Supreme Court Retention - Christine Donohue",
        "",
    ),
    "SUPREME COURT RETENTION - KEVIN M. DOUGHERTY": (
        "Supreme Court Retention - Kevin M Dougherty",
        "",
    ),
    "SUPREME COURT RETENTION - DAVID WECHT": (
        "Supreme Court Retention - David Wecht",
        "",
    ),
    "SUPERIOR COURT RETENTION - ALICE BECK DUBOW": (
        "Superior Court Retention - Alice Beck Dubow",
        "",
    ),
    "COMMONWEALTH COURT RETENTION - MICHAEL H. WOJCIK": (
        "Commonwealth Court Retention - Michael H Wojcik",
        "",
    ),
    "COURT OF COMMON PLEAS RETENTION - TODD M. SPONSELLER": (
        "Court of Common Pleas Retention - Todd M Sponseller",
        "",
    ),
}


def franklin_retention(line: str):
    return FRANKLIN_RETENTION.get(line)


# --------------------------------------------------------------------------
# Inspector/Judge of Election overrides — strip embedded precinct.
# --------------------------------------------------------------------------


def franklin_inspector_judge(line: str):
    for prefix, norm in (
        ("INSPECTOR OF ELECTION", "Inspector of Elections"),
        ("JUDGE OF ELECTION", "Judge of Elections"),
    ):
        if line == prefix or line.startswith(prefix + " "):
            rest = line[len(prefix):].lstrip()
            if rest.upper().startswith(("THE ", "OF ")):
                continue
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
    "SHERIFF": ("Sheriff", ""),
    "CORONER": ("Coroner", ""),
    "TREASURER": ("Treasurer", ""),
    "COUNTY TREASURER": ("Treasurer", ""),
    "CONTROLLER": ("Controller", ""),
    "COUNTY CONTROLLER": ("Controller", ""),
    "PROTHONOTARY": ("Prothonotary", ""),
    "PROTHONOTARY AND CLERK OF COURTS": ("Prothonotary and Clerk of Courts", ""),
    "REGISTER AND RECORDER": ("Register and Recorder", ""),
    "COUNTY COMMISSIONER": ("County Commissioner", ""),
}


# --------------------------------------------------------------------------
# Prefix-style local offices. Order matters: "TOWNSHIP SUPERVISOR" must
# come before any bare "SUPERVISOR" (although no bare form exists here),
# and "COUNCILPERSON" is the full Borough Council prefix.
# --------------------------------------------------------------------------


LOCAL_OFFICES = [
    ("TOWNSHIP SUPERVISOR", "Township Supervisor"),
    ("COUNCILPERSON", "Borough Council"),
    ("TAX COLLECTOR", "Tax Collector"),
    ("AUDITOR", "Township Auditor"),
    ("CONSTABLE", "Constable"),
    ("MAYOR", "Mayor"),
]


# --------------------------------------------------------------------------
# School Director handler.
#
# Variants:
#   "SCHOOL DIRECTOR 2YR Fannett-Metal School District"
#     -> ("School Director (2 Year)", "Fannett-Metal")
#   "SCHOOL DIRECTOR Chambersburg Area School District Region 1"
#     -> ("School Director Region 1", "Chambersburg Area")
#   "SCHOOL DIRECTOR Waynesboro Area School District North End"
#     -> ("School Director", "Waynesboro Area - North End")
#   "SCHOOL DIRECTOR Shippensburg Area School District"
#     -> ("School Director", "Shippensburg Area")
# --------------------------------------------------------------------------


SCHOOL_PREFIX_RE = re.compile(r"^SCHOOL DIRECTOR\s+(.+)$")
SCHOOL_DISTRICT_MARKER_RE = re.compile(r"\s+School District\b", re.IGNORECASE)


def school_director(line: str):
    m = SCHOOL_PREFIX_RE.match(line)
    if not m:
        return None
    rest = m.group(1).strip()

    # Optional leading NYR term token.
    years: Optional[str] = None
    tm = TERM_TOKEN_RE.match(rest.split()[0]) if rest else None
    if tm:
        years = tm.group(1)
        rest = rest[len(rest.split()[0]):].strip()

    # Split "<name> School District [tail]".
    parts = SCHOOL_DISTRICT_MARKER_RE.split(rest, maxsplit=1)
    district_name = parts[0].strip().rstrip(",")
    tail = parts[1].strip() if len(parts) > 1 else ""

    # District name: drop trailing " School" if any; remove trailing
    # "Area"? No — keep it (matches other counties).
    district = district_name

    designator = ""
    sub = ""
    if tail:
        m2 = re.match(r"^Region\s+(\S+)\s*$", tail, re.IGNORECASE)
        if m2:
            designator = f"Region {m2.group(1)}"
        else:
            sub = tail

    office = "School Director"
    if designator:
        office += f" {designator}"
    if years:
        office += f" ({years} Year)"
    if sub:
        district = f"{district} - {sub}"
    return (office, district)


# --------------------------------------------------------------------------
# Precinct prettifier.
#   "ANTRIM 1"                   -> "Antrim 1"
#   "CHAMBERSBURG WD 1 DIST 1"   -> "Chambersburg Ward 1 District 1"
#   "ST THOMAS 1"                -> "St Thomas 1"
#   "MONT ALTO"                  -> "Mont Alto"
#   "SOUTHAMPTON MT ROCK"        -> "Southampton Mt Rock"
#   "WEST END SHIPPENSBURG"      -> "West End Shippensburg"
# --------------------------------------------------------------------------


PRECINCT_TOKEN_MAP = {
    "WD": "Ward",
    "DIST": "District",
}


def prettify_franklin_precinct(name: str) -> str:
    out = []
    for t in name.split():
        up = t.upper()
        if up in PRECINCT_TOKEN_MAP:
            out.append(PRECINCT_TOKEN_MAP[up])
        elif t.isdigit():
            out.append(t)
        else:
            out.append(t.capitalize())
    return " ".join(out)


CONFIG = ElectionwareConfig(
    county="Franklin",
    skip_prefixes=SKIP_PREFIXES,
    county_header_suffix="FRANKLIN COUNTY",
    exact_offices=EXACT_OFFICES,
    local_offices=LOCAL_OFFICES,
    local_office_orientation="prefix",
    retention_style="retention",  # unused; handled by extra handler
    extra_office_handlers=[franklin_retention, franklin_inspector_judge],
    municipality_normalizer=identity,  # trailing munis are already title-case
    school_director_handler=school_director,
    line_preprocessor=franklin_line_preprocessor,
    prettify_precinct=prettify_franklin_precinct,
)


if __name__ == "__main__":
    run_cli(CONFIG)
