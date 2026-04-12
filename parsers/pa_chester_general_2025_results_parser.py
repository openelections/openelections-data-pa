#!/usr/bin/env python3
"""
Parse Chester County PA 2025 General (Municipal) Election precinct results.

Source: CHESTER PA 2025GENERAL_OFFICIAL_PRECINCT_RESULTS.pdf
(1440 pages, 230 precincts, Electionware format with mixed-case
office headers).

Chester-specific quirks:
  - **Numbered precinct labels**: "005 Atglen", "020 E Bradford N-1".
    The 3-digit code is stripped by the prettifier; the rest is left
    as-is.
  - **Wrapped column headers**: column-header text wraps as
    "Election Provisional / TOTAL Mail Votes / Day Votes". All three
    fragments are in ``skip_prefixes``.
  - **Unexpired-term contests**: every office row appears twice on the
    same precinct when there is an unexpired-term seat. Source spelling
    is "Township Supervisor Unexpired 2 Year Term Charlestown
    Township", "Member of Council Unexpired 2 Year Term Kennett Square
    Borough", "School Director Unexpired 2 Year Term Octorara Region
    1", "Prothonotary Unexpired 2 Year Term", etc. A custom extra
    handler matches the "Unexpired N Year Term" infix anywhere in the
    line, strips it, recursively normalizes the cleaned remainder
    (against the same local-prefix table, school-director regex, and
    exact-offices map this parser uses), and tags the resulting office
    with " (Unexpired N Year)" so it stays distinct from the regular
    contest of the same name in the same precinct.
  - **Township Supervisor At Large**: "Township Supervisor At Large
    Tredyffrin Township" - special infix that the standard prefix
    handler can't tease apart, so a dedicated extra handler emits
    office "Township Supervisor At Large" with the trailing muni as
    district.
  - **Phoenixville/West Chester ward names** ("Phoenixville E Ward",
    "West Chester 1st Ward"): the standard prefix handler emits these
    correctly via ``Member of Council`` -> ``Borough Council``.
  - **Retention** uses the verbose "Judicial Retention Question" form
    with full first-and-last names ("Supreme Court Judicial Retention
    Question - Christine Donohue"); explicit map covers the standard
    Supreme/Superior/Commonwealth slate plus the local Court of Common
    Pleas seat for Allison Bell Royer (the source contains the typo
    "Court of Common Please" - mapped explicitly).
  - **Inspector / Judge of Election** lines include the precinct code
    and name on the same line ("Inspector of Election 005 Atglen").
    Custom handler.
  - **School director** headers omit the "School District" suffix
    other counties use; the form is "School Director [At Large
    ]<district name>[ Region X]" where X may be a digit ("1"/"2"/"3")
    or letter ("A"/"B"/"C"). Custom handler.
  - **Ballot question**: "West Pikeland Township: Police Service Tax
    Referendum".
"""

import re
from typing import Optional

from electionware_precinct_np import (
    ElectionwareConfig,
    run_cli,
)


# Chester source uses "DEMREP" (no slash) as a party prefix on at least
# one row (David Prosser, Member of Council W Grove Borough). Normalize
# it to the standard "DEM/REP" so the shared PARTY_RE picks it up.
DEMREP_RE = re.compile(r"^DEMREP ")


def chester_line_preprocessor(line: str) -> str:
    return DEMREP_RE.sub("DEM/REP ", line)


SKIP_PREFIXES = (
    "Precinct Summary Results Report OFFICIAL RESULTS",
    "Precinct Summary Results Report UNOFFICIAL RESULTS",
    "2025 General Election",
    "November 4, 2025 Chester",
    "Precinct Summary - ",
    "Report generated with Electionware",
    # Column header fragments (wrapped across rows).
    "Provisional",
    "Election Provisional",
    "TOTAL Election Day Mail Votes",
    "TOTAL Mail Votes",
    "Day Votes",
    "Votes",
    "Voter Turnout - Total",
    "Vote For ",
    "Total Votes Cast",
    "Contest Totals",
    "Overvotes",
    "Undervotes",
    "Not Assigned",
)


# --------------------------------------------------------------------------
# Retention: explicit map. Chester uses the "Judicial Retention Question"
# spelling and full names. The Court of Common Pleas line in the source
# contains a typo ("Please" instead of "Pleas") which is preserved as the
# matching key.
# --------------------------------------------------------------------------


CHESTER_RETENTION = {
    "Supreme Court Judicial Retention Question - Christine Donohue": (
        "Supreme Court Retention - Christine Donohue",
        "",
    ),
    "Supreme Court Judicial Retention Question - Kevin M. Dougherty": (
        "Supreme Court Retention - Kevin M Dougherty",
        "",
    ),
    "Supreme Court Judicial Retention Question - David Wecht": (
        "Supreme Court Retention - David Wecht",
        "",
    ),
    "Superior Court Judicial Retention Question - Alice Beck Dubow": (
        "Superior Court Retention - Alice Beck Dubow",
        "",
    ),
    "Commonwealth Court Judicial Retention Question - Michael H. Wojcik": (
        "Commonwealth Court Retention - Michael H Wojcik",
        "",
    ),
    "Court of Common Please Judicial Retention Question - Allison Bell Royer": (
        "Court of Common Pleas Retention - Allison Bell Royer",
        "",
    ),
    # Defensive: in case the typo is corrected in a later draft.
    "Court of Common Pleas Judicial Retention Question - Allison Bell Royer": (
        "Court of Common Pleas Retention - Allison Bell Royer",
        "",
    ),
}


def chester_retention(line: str):
    return CHESTER_RETENTION.get(line)


# --------------------------------------------------------------------------
# Inspector / Judge of Election with embedded precinct.
# --------------------------------------------------------------------------


def chester_inspector_judge(line: str):
    for prefix, norm in (
        ("Inspector of Election", "Inspector of Elections"),
        ("Judge of Election", "Judge of Elections"),
    ):
        if line == prefix:
            return (norm, "")
        if line.startswith(prefix + " "):
            rest = line[len(prefix) + 1:].lstrip()
            if rest.lower().startswith(("the ", "of ")):
                continue
            return (norm, "")
    return None


# --------------------------------------------------------------------------
# Ballot questions.
# --------------------------------------------------------------------------


BALLOT_QUESTIONS = {
    "West Pikeland Township: Police Service Tax Referendum": (
        "Police Service Tax Referendum",
        "West Pikeland Township",
    ),
}


def chester_ballot(line: str):
    return BALLOT_QUESTIONS.get(line)


# --------------------------------------------------------------------------
# School director.
#
# Form: "School Director [At Large ]<district>[ Region X]"
#   - X may be a digit (1/2/3/4/6/8) or letter (A/B/C).
#   - District names DO NOT contain the literal "Region " sequence
#     themselves, so the regex's optional " Region X" tail is safe.
# --------------------------------------------------------------------------


SCHOOL_RE = re.compile(
    r"^School Director (At Large )?(.+?)(?: Region (\S+))?$"
)


def school_director(line: str):
    m = SCHOOL_RE.match(line)
    if not m:
        return None
    at_large = m.group(1) is not None
    district = m.group(2).strip()
    region = m.group(3)
    office = "School Director"
    if at_large:
        office += " At Large"
    if region:
        office += f" Region {region}"
    return (office, district)


# --------------------------------------------------------------------------
# "Township Supervisor At Large <muni>" - special infix.
# --------------------------------------------------------------------------


def chester_supervisor_at_large(line: str):
    if line.startswith("Township Supervisor At Large "):
        muni = line[len("Township Supervisor At Large "):].strip()
        return ("Township Supervisor At Large", muni)
    return None


# --------------------------------------------------------------------------
# Exact-match offices.
# --------------------------------------------------------------------------


EXACT_OFFICES = {
    "Judge of the Superior Court": ("Judge of the Superior Court", ""),
    "Judge of the Commonwealth Court": ("Judge of the Commonwealth Court", ""),
    "Judge of the Court of Common Pleas": ("Judge of the Court of Common Pleas", ""),
    "District Attorney": ("District Attorney", ""),
    "Sheriff": ("Sheriff", ""),
    "Coroner": ("Coroner", ""),
    "Treasurer": ("County Treasurer", ""),
    "Controller": ("County Controller", ""),
    "Prothonotary": ("Prothonotary", ""),
    "Clerk of Courts": ("Clerk of Courts", ""),
    "Register of Wills": ("Register of Wills", ""),
    "Recorder of Deeds": ("Recorder of Deeds", ""),
}


# --------------------------------------------------------------------------
# Local offices (prefix orientation).
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


# --------------------------------------------------------------------------
# Unexpired-term handler.
#
# Strips "Unexpired N Year Term" from anywhere in the line, then
# normalizes the cleaned line against the same local-prefix table /
# school-director regex / exact-offices map this parser uses, and
# appends " (Unexpired N Year)" to the resulting office name so it
# stays distinct from the regular contest of the same name.
#
# Implemented as a free-standing handler (not via the shared term-token
# logic) because the term phrase appears as an INFIX, not as a leading
# or trailing token, and is multi-word.
# --------------------------------------------------------------------------


UNEXPIRED_RE = re.compile(r"\s+Unexpired\s+(\d+)\s+Year\s+Term\b")


def _normalize_cleaned(cleaned: str) -> Optional[tuple[str, str]]:
    """Resolve a line that has had its "Unexpired N Year Term" infix
    stripped. Mirrors (a subset of) the shared normalize_office logic
    for the constructs Chester actually emits in unexpired form."""
    if cleaned in EXACT_OFFICES:
        return EXACT_OFFICES[cleaned]
    sd = school_director(cleaned)
    if sd is not None:
        return sd
    # Local-prefix matching: longest prefix wins. Loop in declared order
    # since LOCAL_OFFICES is already arranged with longer entries first.
    for prefix, norm in LOCAL_OFFICES:
        if cleaned == prefix:
            return (norm, "")
        if cleaned.startswith(prefix + " "):
            district = cleaned[len(prefix) + 1:].strip()
            return (norm, district)
    return None


def chester_unexpired(line: str):
    m = UNEXPIRED_RE.search(line)
    if not m:
        return None
    years = m.group(1)
    cleaned = (line[: m.start()] + line[m.end():]).strip()
    cleaned = re.sub(r"\s{2,}", " ", cleaned)
    base = _normalize_cleaned(cleaned)
    if base is None:
        return None
    office, district = base
    return (f"{office} (Unexpired {years} Year)", district)


# --------------------------------------------------------------------------
# Precinct prettifier: strip leading "NNN " code.
# --------------------------------------------------------------------------


PRECINCT_CODE_RE = re.compile(r"^\d{3}\s+")


def prettify_chester_precinct(name: str) -> str:
    return PRECINCT_CODE_RE.sub("", name, count=1)


CONFIG = ElectionwareConfig(
    county="Chester",
    skip_prefixes=SKIP_PREFIXES,
    county_header_suffix="Chester County",
    exact_offices=EXACT_OFFICES,
    local_offices=LOCAL_OFFICES,
    local_office_orientation="prefix",
    retention_style="retention",  # unused; explicit handler below
    extra_office_handlers=[
        chester_retention,
        chester_ballot,
        chester_inspector_judge,
        chester_unexpired,
        chester_supervisor_at_large,
    ],
    school_director_handler=school_director,
    prettify_precinct=prettify_chester_precinct,
    line_preprocessor=chester_line_preprocessor,
)


if __name__ == "__main__":
    run_cli(CONFIG)
