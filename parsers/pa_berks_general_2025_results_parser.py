#!/usr/bin/env python3
"""
Parse Berks County PA 2025 General (Municipal) Election precinct results.

Source: Berks PA Official-Precinct-Summary-11-4-2025.pdf
(1196 pages, ~250 precincts, Electionware format with mixed-case office
headers and a verbose "<N> Year Term" term-token spelling).

Berks-specific quirks:
  - **Mixed-case office headers** (not all-caps): "Township Supervisor",
    "Member of Council", "Tax Collector".
  - **Verbose term tokens**: "2 Year Term", "4 Year Term", "6 Year
    Term" — instead of the standard "2YR" / "2yr". A line preprocessor
    rewrites them in place to "2YR/4YR/6YR" so the shared prefix handler
    can pull the term off via the standard ``TERM_TOKEN_RE``. The
    rewrite is also what lets ``Sheriff 2 Year Term`` collapse to a
    single ``Sheriff 2YR`` exact-offices entry.
  - **Cross-filed party "D/R"** (Berks reports many county-row offices
    as cross-filed). Already supported by ``PARTY_CODES``.
  - **Wrapped column header**: "TOTAL Ele D c a t y ion Mail
    Provisional" with the trailing "Day" tokens on a follow-up line.
    Both forms appear in ``skip_prefixes``.
  - **Last-name-only retention headers**: "Supreme Court Retention
    Election Question - Donohue" (etc.) — Berks omits first names so
    we map directly to ``Supreme Court Retention - Donohue`` rather
    than the canonical full-name form used by other counties. Court of
    Common Pleas retention contests for Barrett, Dimitriou Geishauser,
    and Lillis are also handled.
  - **Reading City Council** comes through as
    "City Council District 1/4/5" and "Council President City of
    Reading" — both wired up via ``exact_offices`` with district
    "Reading".
  - **Inspector of Election / Judge of Election** include the
    precinct name on the same line ("Inspector of Election Reading
    1-1"); a custom handler strips the trailing precinct.
  - **School Director** lines always end in "School District" with an
    optional "Region N" suffix:
      "School Director Hamburg Area School District Region 2"
        -> ("School Director Region 2", "Hamburg Area")
      "School Director Muhlenberg School District"
        -> ("School Director", "Muhlenberg")
"""

import re

from electionware_precinct_np import (
    ElectionwareConfig,
    run_cli,
)


SKIP_PREFIXES = (
    "Summary Results Report OFFICIAL RESULTS",
    "Summary Results Report UNOFFICIAL RESULTS",
    "2025 Municipal Election",
    "November 4, 2025 Berks",
    "Precinct Summary - ",
    "Report generated with Electionware",
    # Column headers (wrapped and unwrapped variants).
    "TOTAL Election Day Mail Provisional",
    "TOTAL Ele",  # "TOTAL Ele D c a t y ion Mail Provisional"
    "Day",
    "Election",
    "Voter Turnout - Total",
    "Vote For ",
    "Total Votes Cast",
    "Contest Totals",
    "Overvotes",
    "Undervotes",
    "Not Assigned",
)


# --------------------------------------------------------------------------
# Line preprocessor: rewrite "<N> Year Term" -> "<N>YR" so the shared
# prefix handler can pull the term off via TERM_TOKEN_RE.
# --------------------------------------------------------------------------


YEAR_TERM_RE = re.compile(r"\b(\d+)\s+Year\s+Term\b")


def berks_line_preprocessor(line: str) -> str:
    return YEAR_TERM_RE.sub(r"\1YR", line)


# --------------------------------------------------------------------------
# Retention: explicit map. Berks uses last-name-only headers so the
# shared retention regex (which expects the standard "Christine
# Donohue" full-name form) can't be reused. We also handle the local
# Court of Common Pleas retention slate.
# --------------------------------------------------------------------------


BERKS_RETENTION = {
    "Supreme Court Retention Election Question - Donohue": (
        "Supreme Court Retention - Donohue",
        "",
    ),
    "Supreme Court Retention Election Question - Dougherty": (
        "Supreme Court Retention - Dougherty",
        "",
    ),
    "Supreme Court Retention Election Question - Wecht": (
        "Supreme Court Retention - Wecht",
        "",
    ),
    "Superior Court Retention Election Question - Beck Dubow": (
        "Superior Court Retention - Beck Dubow",
        "",
    ),
    "Commonwealth Court Retention Election Question - Wojcik": (
        "Commonwealth Court Retention - Wojcik",
        "",
    ),
    "Court of Common Pleas Retention Election Question - Barrett": (
        "Court of Common Pleas Retention - Barrett",
        "",
    ),
    "Court of Common Pleas Retention Election Question - Dimitriou Geishauser": (
        "Court of Common Pleas Retention - Dimitriou Geishauser",
        "",
    ),
    "Court of Common Pleas Retention Election Question - Lillis": (
        "Court of Common Pleas Retention - Lillis",
        "",
    ),
}


def berks_retention(line: str):
    return BERKS_RETENTION.get(line)


# --------------------------------------------------------------------------
# Inspector / Judge of Election with precinct on the same line.
# --------------------------------------------------------------------------


def berks_inspector_judge(line: str):
    for prefix, norm in (
        ("Inspector of Election", "Inspector of Elections"),
        ("Judge of Election", "Judge of Elections"),
    ):
        if line == prefix:
            return (norm, "")
        if line.startswith(prefix + " "):
            rest = line[len(prefix) + 1:].lstrip()
            # Disambiguate from "Judge of Election" vs "Judge of the ..."
            if rest.lower().startswith(("the ", "of ")):
                continue
            return (norm, "")
    return None


# --------------------------------------------------------------------------
# School Director: "School Director <name> School District[ Region N]".
# --------------------------------------------------------------------------


SCHOOL_RE = re.compile(
    r"^School Director(?:\s+(\d+)YR)? (.+?) School District(?:\s+Region\s+(\d+))?$"
)


def school_director(line: str):
    m = SCHOOL_RE.match(line)
    if not m:
        return None
    years = m.group(1)
    district = m.group(2).strip()
    region = m.group(3)
    office = "School Director"
    if region:
        office += f" Region {region}"
    if years:
        office += f" ({years} Year)"
    return (office, district)


# --------------------------------------------------------------------------
# Exact-match offices. Keys are matched AFTER the line preprocessor, so
# "Sheriff 2 Year Term" arrives here as "Sheriff 2YR".
# --------------------------------------------------------------------------


EXACT_OFFICES = {
    "Judge of the Superior Court": ("Judge of the Superior Court", ""),
    "Judge of the Commonwealth Court": ("Judge of the Commonwealth Court", ""),
    "Judge of the Court of Common Pleas": ("Judge of the Court of Common Pleas", ""),
    "County Prothonotary": ("Prothonotary", ""),
    "County Treasurer": ("County Treasurer", ""),
    "County Coroner": ("Coroner", ""),
    "Sheriff 2YR": ("Sheriff", ""),
    "Sheriff": ("Sheriff", ""),
    "District Attorney": ("District Attorney", ""),
    "Council President City of Reading": ("Council President", "Reading"),
    "City Council District 1": ("City Council District 1", "Reading"),
    "City Council District 4": ("City Council District 4", "Reading"),
    "City Council District 5": ("City Council District 5", "Reading"),
}


# --------------------------------------------------------------------------
# Prefix-style local offices. Order matters: longer prefix wins.
# --------------------------------------------------------------------------


LOCAL_OFFICES = [
    ("Township Supervisor", "Township Supervisor"),
    ("Township Commissioner", "Township Commissioner"),
    ("Member of Council", "Borough Council"),
    ("Tax Collector", "Tax Collector"),
    ("Auditor", "Township Auditor"),
    ("Constable", "Constable"),
    ("Mayor", "Mayor"),
]


CONFIG = ElectionwareConfig(
    county="Berks",
    skip_prefixes=SKIP_PREFIXES,
    county_header_suffix="Berks County",
    exact_offices=EXACT_OFFICES,
    local_offices=LOCAL_OFFICES,
    local_office_orientation="prefix",
    retention_style="retention",  # unused; explicit handler below
    extra_office_handlers=[
        berks_retention,
        berks_inspector_judge,
    ],
    school_director_handler=school_director,
    line_preprocessor=berks_line_preprocessor,
)


if __name__ == "__main__":
    run_cli(CONFIG)
