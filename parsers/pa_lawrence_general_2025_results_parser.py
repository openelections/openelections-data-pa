#!/usr/bin/env python3
"""
Parse Lawrence County PA 2025 General (Municipal) Election precinct
results.

Source: Lawrence PA 2025-GENERAL-ELECTION-SUMMARY-PRECINCT-OFFICIAL-RESULTS.pdf
(718 pages, ~96 precincts, Electionware format with numerous
Lawrence-specific quirks).

Lawrence-specific quirks:
  - **VOTE % column**: like Tioga/Franklin, each candidate row includes
    an inline percentage (e.g. ``57.60%``). Line preprocessor strips it.
  - **"VOTE N" / "VOTEN" infix** in office headers: many local offices
    embed a redundant "VOTE 1" or "VOTE4" indicating the Vote For
    count inside the office header ("BOROUGH COUNCIL 4YR VOTE4
    BESSEMER", "TWP AUDITOR 6YR VOTE 1 UNION"). Line preprocessor
    strips this infix so the shared prefix handler can parse normally.
  - **TWP / BORO prefix variants**: Lawrence differentiates township
    from borough offices via a leading "TWP" or "BORO" token
    ("TWP AUDITOR 6YR UNION", "BORO AUDITOR 6YR ENON VALLEY",
    "TWP TAX COLLECTOR HICKORY", "BORO TAX COLLECTOR ELLPORT").
    Mapped to "Township Auditor"/"Borough Auditor" and "Tax Collector"
    respectively.
  - **Bare "AUDITOR"** also appears for some townships; mapped to
    "Township Auditor".
  - **Retention**: bare "RETAIN <name>" form without the court prefix
    that other counties use. Explicit map.
  - **"REGISTER AND RECORDER"**: unusual combined county office name.
  - **School director**: "SCHOOL DIRECTOR NYR <district name>".
    One anomalous entry: "SCHOOL DIRECTOR BLACK HAWK 4YR BLACKHAWK
    AREA REGION 1" (after VOTE N stripping) — handled via explicit
    extra handler.
  - **CITY COUNCIL NYR NEW CASTLE**: standard prefix handler.
  - **Wrapped column headers**: "ELECTION PROVISIONA / TOTAL MAIL VOTE
    / DAY L VOTE" and "ELECTION PROVISION / TOTAL VOTE % MAIL VOTE /
    DAY AL VOTE".
"""

import re
from typing import Optional

from electionware_precinct_np import (
    ElectionwareConfig,
    title_case,
    run_cli,
)


# --------------------------------------------------------------------------
# Line preprocessor.
# --------------------------------------------------------------------------


PCT_RE = re.compile(r"\s+\d+\.\d+%")
VOTE_N_RE = re.compile(r"\s+VOTE\s*\d+", re.IGNORECASE)


def lawrence_line_preprocessor(line: str) -> str:
    # Strip inline VOTE % column.
    line = PCT_RE.sub("", line)
    # Strip redundant "VOTE N" infix from office headers. Guard against
    # stripping from "Total Votes Cast" / "TOTAL VOTE %" / "Vote For N".
    if not any(
        k in line
        for k in ("Total Votes", "TOTAL VOTE", "Vote For", "Contest Totals")
    ):
        line = VOTE_N_RE.sub("", line)
    return line


# --------------------------------------------------------------------------
# Skip prefixes.
# --------------------------------------------------------------------------


SKIP_PREFIXES = (
    "Summary Results Report OFFICIAL RESULTS",
    "Summary Results Report UNOFFICIAL RESULTS",
    "2025 Municipal Election",
    "November 4, 2025 Lawrence",
    "Precinct Summary - ",
    "Report generated with Electionware",
    # Column header fragments (many wrapped variants).
    "Provisional",
    "PROVISIONA",
    "PROVISION",
    "TOTAL Election Day Mail",
    "TOTAL MAIL VOTE",
    "TOTAL VOTE",
    "TOTAL Ele",
    "ELECTION PROVISIONA",
    "ELECTION PROVISION",
    "ELECTION",
    "DAY L VOTE",
    "DAY AL VOTE",
    "DAY",
    "Votes",
    "L VOTE",
    "AL VOTE",
    "Mail",
    "Voter Turnout - Total",
    "Vote For ",
    "Total Votes Cast",
    "Contest Totals",
    "Overvotes",
    "Undervotes",
    "Not Assigned",
)


# --------------------------------------------------------------------------
# Retention: explicit map (Lawrence uses bare "RETAIN <name>").
# --------------------------------------------------------------------------


LAWRENCE_RETENTION = {
    "RETAIN CHRISTINE DONOHUE": (
        "Supreme Court Retention - Christine Donohue",
        "",
    ),
    "RETAIN KEVIN M DOUGHERTY": (
        "Supreme Court Retention - Kevin M Dougherty",
        "",
    ),
    "RETAIN DAVID WECHT": (
        "Supreme Court Retention - David Wecht",
        "",
    ),
    "RETAIN ALICE BECK DUBOW": (
        "Superior Court Retention - Alice Beck Dubow",
        "",
    ),
    "RETAIN MICHAEL H WOJCIK": (
        "Commonwealth Court Retention - Michael H Wojcik",
        "",
    ),
}


def lawrence_retention(line: str):
    return LAWRENCE_RETENTION.get(line)


# --------------------------------------------------------------------------
# Inspector / Judge of Election with embedded precinct.
# --------------------------------------------------------------------------


def lawrence_inspector_judge(line: str):
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
# Blackhawk school director (one-off anomaly).
# After VOTE N stripping the line is:
#   "SCHOOL DIRECTOR BLACK HAWK 4YR BLACKHAWK AREA REGION 1"
# --------------------------------------------------------------------------


def lawrence_blackhawk_school(line: str):
    if "BLACK HAWK" in line and "SCHOOL DIRECTOR" in line:
        return ("School Director Region 1 (4 Year)", "Blackhawk Area")
    return None


# --------------------------------------------------------------------------
# School director handler.
#   "SCHOOL DIRECTOR 2YR ELLWOOD CITY AREA"
#   "SCHOOL DIRECTOR 4YR LAUREL"
#   "SCHOOL DIRECTOR 4YR NESHANNOCK TOWNSHIP"
# --------------------------------------------------------------------------


SCHOOL_RE = re.compile(
    r"^SCHOOL DIRECTOR(?:\s+(\d+)YR)?\s+(.+)$", re.IGNORECASE
)


def school_director(line: str):
    m = SCHOOL_RE.match(line)
    if not m:
        return None
    years = m.group(1)
    district = title_case(m.group(2).strip())
    office = "School Director"
    if years:
        office += f" ({years} Year)"
    return (office, district)


# --------------------------------------------------------------------------
# Exact-match offices.
# --------------------------------------------------------------------------


EXACT_OFFICES = {
    "JUDGE OF THE SUPERIOR COURT": ("Judge of the Superior Court", ""),
    "JUDGE OF THE COMMONWEALTH COURT": ("Judge of the Commonwealth Court", ""),
    "JUDGE OF THE COURT OF COMMON PLEAS": ("Judge of the Court of Common Pleas", ""),
    "DISTRICT ATTORNEY": ("District Attorney", ""),
    "SHERIFF": ("Sheriff", ""),
    "REGISTER AND RECORDER": ("Register and Recorder", ""),
}


# --------------------------------------------------------------------------
# Prefix-style local offices. Order: longest prefix first.
# --------------------------------------------------------------------------


LOCAL_OFFICES = [
    ("BORO TAX COLLECTOR", "Tax Collector"),
    ("TWP TAX COLLECTOR", "Tax Collector"),
    ("BORO AUDITOR", "Borough Auditor"),
    ("TWP AUDITOR", "Township Auditor"),
    ("BOROUGH COUNCIL", "Borough Council"),
    ("CITY COUNCIL", "City Council"),
    ("SUPERVISOR", "Township Supervisor"),
    ("AUDITOR", "Township Auditor"),
    ("TAX COLLECTOR", "Tax Collector"),
    ("CONSTABLE", "Constable"),
    ("MAYOR", "Mayor"),
]


CONFIG = ElectionwareConfig(
    county="Lawrence",
    skip_prefixes=SKIP_PREFIXES,
    county_header_suffix="Lawrence",
    exact_offices=EXACT_OFFICES,
    local_offices=LOCAL_OFFICES,
    local_office_orientation="prefix",
    retention_style="retention",  # unused; explicit handler
    municipality_normalizer=title_case,
    extra_office_handlers=[
        lawrence_retention,
        lawrence_inspector_judge,
        lawrence_blackhawk_school,
    ],
    school_director_handler=school_director,
    line_preprocessor=lawrence_line_preprocessor,
    include_magisterial=True,
)


if __name__ == "__main__":
    run_cli(CONFIG)
