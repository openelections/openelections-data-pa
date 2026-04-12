#!/usr/bin/env python3
"""
Parse Northampton County PA 2025 General (Municipal) Election precinct
results.

Source: Northampton PA GE25 Precinct Level Summary Results_2321.pdf
(1054 pages, ~157 precincts, Electionware format with a number of
Northampton-specific quirks).

Usage:
    python parsers/pa_northampton_general_2025_results_parser.py \\
        "<input.pdf>" "<output.csv>"

Northampton-specific quirks:
  - **Numbered precinct codes**: "010001-1 ALLEN TWSP NORTH",
    "040101-1 BETHLEHEM 1-NORTH", "250100-1 PEN ARGYL 1ST WARD".
    A custom prettifier strips the "NNNNNN-N " prefix, title-cases the
    remainder, and expands TWSP -> Township, BORO -> Borough.
  - **County-level executive government**: "COUNTY EXECUTIVE" and
    "COUNTY COUNCIL - AT-LARGE" are unusual (Northampton is a home rule
    county); both in ``exact_offices``.
  - **Prefix-style local offices with mixed-case trailing munis**:
    "BOROUGH COUNCIL Nazareth 1st Ward", "SUPERVISOR 6YR Allen Twsp",
    "MAYOR Bethlehem", "TAX COLLECTOR Lower Mt Bethel Twsp",
    "AUDITOR 6YR Forks Twsp". Handled by the shared prefix handler with
    ``municipality_normalizer=identity``.
  - **Bare "COUNCIL <NYR> <muni>"**: second spelling for Borough Council
    (e.g. "COUNCIL 4YR Bangor Boro"). Mapped to "Borough Council".
  - **Commissioner with ward designator**:
    "COMMISSIONER - 2ND WARD Bethlehem Twsp" (Bethlehem Township is a
    first-class township with ward commissioners). Custom extra handler
    emits office="Township Commissioner" with district
    "Bethlehem Twsp Ward N".
  - **City Council with At-Large or District N designator**:
    "CITY COUNCIL - AT-LARGE Bethlehem", "CITY COUNCIL - DISTRICT 1
    Easton". Custom extra handler.
  - **"CONTROLLER Bethlehem"** — the Bethlehem city controller (distinct
    from the county controller, which is absent). Custom extra handler
    emits "City Controller" with district "Bethlehem".
  - **Court of Common Pleas retention** for Paula A. Roscioli and
    Samuel P. Murray — plus the standard three Supreme / one Superior /
    one Commonwealth retention contests. All handled via an explicit
    map so the "KEVIN M. DOUGHERTY" period stays stripped consistently.
  - **Singular JUDGE OF ELECTION / INSPECTOR OF ELECTION** with the
    precinct code embedded ("JUDGE OF ELECTION 010001-1 ALLEN TWSP
    NORTH"). Normalized to the canonical plural form via an extra
    handler that emits district="".
  - **School Director headers with dash-separated designators**:
      "SCHOOL DIRECTOR - AT-LARGE - Bangor Area School District"
        -> ("School Director At Large", "Bangor Area")
      "SCHOOL DIRECTOR - REGION I - Bethlehem Area School District"
        -> ("School Director Region I", "Bethlehem Area")
      "SCHOOL DIRECTOR - REGION I - 2YR - Pen Argyl Area School District"
        -> ("School Director Region I (2 Year)", "Pen Argyl Area")
      "SCHOOL DIRECTOR - AT-LARGE 2YR - Catasauqua Area School Director"
        (source typo: "Director" for "District")
        -> ("School Director At Large (2 Year)", "Catasauqua Area")
  - **Ballot questions**:
      "WILLIAMS TOWNSHIP EARNED INCOME TAX REFERENDUM"
        -> ("Earned Income Tax Referendum", "Williams Township")
      "DECREASE IN NUMBER OF MEMBERS OF CHAPMAN COUNCIL"
        -> ("Decrease in Number of Members of Chapman Council",
            "Chapman")
  - **Wrapped "Provisional" column header**: column-header text wraps
    mid-row as "TOTAL Election Day Mail Votes Pro V v o is te io s nal".
    Skip-prefixes include both the wrapped and unwrapped variants.
"""

import re
from typing import Optional

from electionware_precinct_np import (
    ElectionwareConfig,
    TERM_TOKEN_RE,
    identity,
    run_cli,
)


SKIP_PREFIXES = (
    "Precinct Summary Results Report OFFICIAL RESULTS",
    "Precinct Summary Results Report UNOFFICIAL RESULTS",
    "MUNICIPAL ELECTION",
    "November 4, 2025 Northampton",
    "Precinct Summary_Official",
    "Precinct Summary - ",
    "Report generated with Electionware",
    # Column headers (wrapped across rows and inline).
    "Provisional",
    "TOTAL Election Day Mail Votes",
    "Votes",
    "Pro V v o is te io s nal",
    "Voter Turnout - Total",
    "Vote For ",
    "Total Votes Cast",
    "Contest Totals",
    "Overvotes",
    "Undervotes",
    "Not Assigned",
)


# --------------------------------------------------------------------------
# Retention: explicit map (standard Supreme/Superior/Commonwealth plus
# the two Court of Common Pleas judges).
# --------------------------------------------------------------------------


NORTHAMPTON_RETENTION = {
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
    "COURT OF COMMON PLEAS RETENTION - PAULA A. ROSCIOLI": (
        "Court of Common Pleas Retention - Paula A Roscioli",
        "",
    ),
    "COURT OF COMMON PLEAS RETENTION - SAMUEL P. MURRAY": (
        "Court of Common Pleas Retention - Samuel P Murray",
        "",
    ),
}


def northampton_retention(line: str):
    return NORTHAMPTON_RETENTION.get(line)


# --------------------------------------------------------------------------
# Inspector/Judge of Election with embedded precinct code+name.
# --------------------------------------------------------------------------


def northampton_inspector_judge(line: str):
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
# Commissioner with ward designator:
#   "COMMISSIONER - 2ND WARD Bethlehem Twsp"
#     -> ("Township Commissioner", "Bethlehem Twsp Ward 2")
# --------------------------------------------------------------------------


COMMISSIONER_WARD_RE = re.compile(
    r"^COMMISSIONER\s*-\s*(\d+)(?:ST|ND|RD|TH)\s+WARD\s+(.+)$",
    re.IGNORECASE,
)


def northampton_commissioner(line: str):
    m = COMMISSIONER_WARD_RE.match(line)
    if not m:
        return None
    ward = m.group(1)
    muni = m.group(2).strip()
    return ("Township Commissioner", f"{muni} Ward {ward}")


# --------------------------------------------------------------------------
# City Council with designator:
#   "CITY COUNCIL - AT-LARGE Bethlehem"
#     -> ("City Council At Large", "Bethlehem")
#   "CITY COUNCIL - DISTRICT 1 Easton"
#     -> ("City Council District 1", "Easton")
# --------------------------------------------------------------------------


CITY_COUNCIL_ATLARGE_RE = re.compile(
    r"^CITY COUNCIL\s*-\s*AT-?LARGE\s+(.+)$", re.IGNORECASE
)
CITY_COUNCIL_DISTRICT_RE = re.compile(
    r"^CITY COUNCIL\s*-\s*DISTRICT\s+(\S+)\s+(.+)$", re.IGNORECASE
)


def northampton_city_council(line: str):
    m = CITY_COUNCIL_ATLARGE_RE.match(line)
    if m:
        return ("City Council At Large", m.group(1).strip())
    m = CITY_COUNCIL_DISTRICT_RE.match(line)
    if m:
        return (f"City Council District {m.group(1)}", m.group(2).strip())
    return None


# --------------------------------------------------------------------------
# "CONTROLLER Bethlehem" -> city controller.
# --------------------------------------------------------------------------


def northampton_city_controller(line: str):
    if line.startswith("CONTROLLER ") and line != "CONTROLLER":
        return ("City Controller", line[len("CONTROLLER "):].strip())
    return None


# --------------------------------------------------------------------------
# Ballot questions.
# --------------------------------------------------------------------------


BALLOT_QUESTIONS = {
    "WILLIAMS TOWNSHIP EARNED INCOME TAX REFERENDUM": (
        "Earned Income Tax Referendum",
        "Williams Township",
    ),
    "DECREASE IN NUMBER OF MEMBERS OF CHAPMAN COUNCIL": (
        "Decrease in Number of Members of Chapman Council",
        "Chapman",
    ),
}


def northampton_ballot(line: str):
    return BALLOT_QUESTIONS.get(line)


# --------------------------------------------------------------------------
# School Director handler.
#
#   "SCHOOL DIRECTOR - AT-LARGE - Bangor Area School District"
#   "SCHOOL DIRECTOR - REGION I - Bethlehem Area School District"
#   "SCHOOL DIRECTOR - REGION I - 2YR - Pen Argyl Area School District"
#   "SCHOOL DIRECTOR - AT-LARGE 2YR - Catasauqua Area School Director" (sic)
#
# Form: "SCHOOL DIRECTOR - <designator>[ <NYR>] - <district>"
# where <district> ends in "School District" or "School Director" (typo).
# --------------------------------------------------------------------------


SCHOOL_RE = re.compile(
    r"^SCHOOL DIRECTOR\s*-\s*(.+?)\s*-\s*(.+?)\s+School\s+(?:District|Director)\s*$",
    re.IGNORECASE,
)


def school_director(line: str):
    m = SCHOOL_RE.match(line)
    if not m:
        return None
    designator_raw = m.group(1).strip()
    district = m.group(2).strip()

    # Extract optional trailing NYR term token from the designator.
    years: Optional[str] = None
    toks = designator_raw.split()
    if toks:
        tm = TERM_TOKEN_RE.match(toks[-1])
        if tm:
            years = tm.group(1)
            toks = toks[:-1]
    designator_raw = " ".join(toks)

    # Normalize designator.
    up = designator_raw.upper()
    if up in ("AT-LARGE", "AT LARGE"):
        designator = "At Large"
    elif up.startswith("REGION "):
        designator = "Region " + designator_raw.split(None, 1)[1]
    else:
        designator = designator_raw.title()

    office = "School Director"
    if designator:
        office += f" {designator}"
    if years:
        office += f" ({years} Year)"
    return (office, district)


# --------------------------------------------------------------------------
# Exact county/city offices.
# --------------------------------------------------------------------------


EXACT_OFFICES = {
    "JUDGE OF THE SUPERIOR COURT": ("Judge of the Superior Court", ""),
    "JUDGE OF THE COMMONWEALTH COURT": ("Judge of the Commonwealth Court", ""),
    "JUDGE OF THE COURT OF COMMON PLEAS": ("Judge of the Court of Common Pleas", ""),
    "DISTRICT ATTORNEY": ("District Attorney", ""),
    "SHERIFF": ("Sheriff", ""),
    "CORONER": ("Coroner", ""),
    "COUNTY EXECUTIVE": ("County Executive", ""),
    "COUNTY COUNCIL - AT-LARGE": ("County Council At Large", ""),
    "COUNTY COUNCIL - AT LARGE": ("County Council At Large", ""),
}


# --------------------------------------------------------------------------
# Prefix-style local offices. Order matters: longest prefix wins.
# "BOROUGH COUNCIL" must precede any bare "COUNCIL".
# --------------------------------------------------------------------------


LOCAL_OFFICES = [
    ("BOROUGH COUNCIL", "Borough Council"),
    ("COUNCIL", "Borough Council"),
    ("TAX COLLECTOR", "Tax Collector"),
    ("SUPERVISOR", "Township Supervisor"),
    ("AUDITOR", "Township Auditor"),
    ("CONSTABLE", "Constable"),
    ("MAYOR", "Mayor"),
]


# --------------------------------------------------------------------------
# Precinct prettifier.
#
#   "010001-1 ALLEN TWSP NORTH"   -> "Allen Twsp North"
#   "040101-1 BETHLEHEM 1-NORTH"  -> "Bethlehem 1-North"
#   "250100-1 PEN ARGYL 1ST WARD" -> "Pen Argyl 1st Ward"
#   "140001-1 HANOVER #1"         -> "Hanover #1"
# --------------------------------------------------------------------------


PRECINCT_CODE_RE = re.compile(r"^\d{6}-\d+\s+")
ORDINAL_RE = re.compile(r"\b(\d+)(St|Nd|Rd|Th)\b")
PRECINCT_TOKEN_MAP = {
    "TWSP": "Twsp",
    "TWP": "Twp",
    "BORO": "Boro",
    "MT.": "Mt.",
    "MT": "Mt",
}


def prettify_northampton_precinct(name: str) -> str:
    name = PRECINCT_CODE_RE.sub("", name, count=1)
    out = []
    for t in name.split():
        up = t.upper()
        if up in PRECINCT_TOKEN_MAP:
            out.append(PRECINCT_TOKEN_MAP[up])
        elif t.startswith("#"):
            out.append(t)
        elif "-" in t:
            # e.g. "1-NORTH", "14-2", "8TH"
            parts = t.split("-")
            out.append("-".join(p.capitalize() for p in parts))
        else:
            out.append(t.capitalize())
    s = " ".join(out)
    # Lower the ordinal suffix: "1St" -> "1st".
    s = ORDINAL_RE.sub(lambda m: m.group(1) + m.group(2).lower(), s)
    return s


CONFIG = ElectionwareConfig(
    county="Northampton",
    skip_prefixes=SKIP_PREFIXES,
    county_header_suffix="Northampton County",
    exact_offices=EXACT_OFFICES,
    local_offices=LOCAL_OFFICES,
    local_office_orientation="prefix",
    retention_style="retention",  # unused; handled by extra handler
    extra_office_handlers=[
        northampton_retention,
        northampton_ballot,
        northampton_commissioner,
        northampton_city_council,
        northampton_city_controller,
        northampton_inspector_judge,
    ],
    municipality_normalizer=identity,  # trailing munis already mixed case
    school_director_handler=school_director,
    prettify_precinct=prettify_northampton_precinct,
)


if __name__ == "__main__":
    run_cli(CONFIG)
