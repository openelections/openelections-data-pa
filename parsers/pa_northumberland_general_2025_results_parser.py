#!/usr/bin/env python3
"""
Parse Northumberland County PA 2025 General (Municipal) Election precinct
results.

Source: Northumberland PA precinct.pdf (534 pages, Electionware format
with an unusual collection of Northumberland-specific quirks).

Usage:
    python parsers/pa_northumberland_general_2025_results_parser.py \\
        "<input.pdf>" "<output.csv>"

Uses the shared natural-pdf Electionware parser in
``electionware_precinct_np`` and supplies Northumberland-specific config.

Northumberland-specific quirks:
  - **Mixed-case office headers** (title case, not ALL-CAPS):
    "Judge of the Superior Court", "Controller",
    "Prothonotary/Clerk of Courts". Unusual for Electionware; handled by
    supplying mixed-case keys in ``exact_offices`` and the local office
    handler.
  - **Retention headers differentiated by typos**. Three separate Supreme
    Court retention contests map to Donohue/Dougherty/Wecht by string:
      "Supreme Court Retention Question"           -> Donohue
      "Supreme Court Retenton Election Question"   -> Dougherty (typo!)
      "Supreme Court Retention Election Question"  -> Wecht
    Plus "Superior Court Retention Election Question",
    "Commonwealth Court Retention Election Question", and the local
    "Judicial Retention Question - Paige Rosini".
  - **Magisterial District Judge with "MDJ" keyword**:
    "Magisterial District Judge MDJ 08-3-02" — the shared MDJ regex would
    keep "MDJ" in the district. Handled by an extra override.
  - **Singular Judge of Election / Inspector of Election** with the
    precinct embedded ("Judge of Election Coal Township 1"). Same pattern
    as Blair/Lebanon/Tioga.
  - **Daniel Wassmer has no party code** on candidate rows (he's the
    Libertarian candidate for Superior Court — "DANIEL WASSMER 9 6 3 0").
    A ``line_preprocessor`` prepends "LBR " so the shared PARTY_RE picks
    him up.
  - **Local offices with duplicated trailing municipality**, with several
    variations:
      "Mayor Herndon Borough Herndon Borough"              (exact dup)
      "Supervisor Lewis Township"                          (no dup)
      "Constable Turbotville Turbotville Borough"          (left muni is bare)
      "Constable Upper Mahanoy Upper Mahanoy Township"     (left missing "Township")
      "Council Member Riverside Boro Riverside Borough"    (Boro vs Borough)
      "Council Member City of Sunbury Sunbury City"        (City of X vs X City)
      "Constable Sunbury Region 4 Sunbury City Region 4"   (with Region N)
      "Council Member Milton Borough Region 5 Milton Borough Region 5"
    Handled by a dedicated ``northumberland_local`` handler that matches
    the trailing municipality against a hardcoded list of the county's
    38 munis with optional "Region N" / "N" / "N-N" suffix.
  - **Plural office labels**: "Auditors Little Mahanoy Township Little
    Mahanoy Township" (plural Auditors), "Commissioners Coal Township
    Coal Township" (first-class township commissioners), "School
    Directors MCASD" (plural).
  - **City offices**: "Controller Shamokin City Shamokin City",
    "Treasurer Shamokin City Shamokin City", "Treasurer Sunbury City
    Sunbury City". Distinct from the county-level Controller/Treasurer;
    normalized to "City Controller"/"City Treasurer".
  - **Council in a City** -> "City Council" (Shamokin City, Sunbury
    City); normal boroughs use "Borough Council".
  - **School Director(s)** headers use district codes (LMSD, MASD, WRSD,
    MCASD, SASD, SCSD) plus optional "Region N" plus optional duplicated
    full district name. Two bare names not using a code (Danville Area,
    Shikellamy). Custom ``school_director_handler``.
  - **Home Rule Charter ballot question**: "City of Shamokin Home Rule
    Charter Referendum Shamokin City" -> handled as an extra override.
  - **"Recorder of Deeds, Register of Wills, and Clerk of the Orphans'
    Court"** — compound county office added to ``exact_offices``.
  - **"Judge of The Court of Common Pleas"** uses capital-T "The"; the
    shared common-pleas check is already case-insensitive.
  - **Precinct labels are mixed case** ("Coal Township 1") — no
    prettifier needed.
"""

import re
from typing import Optional

from electionware_precinct_np import (
    ElectionwareConfig,
    TERM_TOKEN_RE,
    expand_muni_flexible,
    identity,
    run_cli,
    title_case,
)


SKIP_PREFIXES = (
    "Summary Results Report OFFICIAL RESULTS",
    "Summary Results Report UNOFFICIAL RESULTS",
    "2025 General Election",
    "November 5, 2025 Northumberland County",
    "November 4, 2025 Northumberland County",
    "Precinct Summary - ",
    "Report generated with Electionware",
    "TOTAL Election Day Provisional",
    "Mail/Absent",
    "ee",  # wrapped "Mail/Absentee" header fragment
    "Election Mail/Absen",  # wrapped column header row 1
    "Day tee",              # wrapped column header row 2
    "Voter Turnout - Total",
    "Vote For ",
    "Total Votes Cast",
    "Contest Totals",
    "Not Assigned",
    "Overvotes",
    "Undervotes",
)


# --------------------------------------------------------------------------
# Line preprocessor: prepend "LBR" to Daniel Wassmer (Libertarian) rows so
# the shared PARTY_RE parses them.
# --------------------------------------------------------------------------


WASSMER_RE = re.compile(r"^(DANIEL\s+WASSMER\s+\d)")


def northumberland_line_preprocessor(line: str) -> str:
    if WASSMER_RE.match(line):
        return "LBR " + line
    return line


# --------------------------------------------------------------------------
# Retention: typo-differentiated exact matches.
# --------------------------------------------------------------------------


NORTHUMBERLAND_RETENTION = {
    # Supreme Court — three distinct string variants map positionally to
    # the three retained justices. Ordering is stable across all 74
    # precincts (verified during recon).
    "Supreme Court Retention Question": (
        "Supreme Court Retention - Christine Donohue",
        "",
    ),
    "Supreme Court Retenton Election Question": (  # sic: "Retenton"
        "Supreme Court Retention - Kevin M Dougherty",
        "",
    ),
    "Supreme Court Retention Election Question": (
        "Supreme Court Retention - David Wecht",
        "",
    ),
    "Superior Court Retention Election Question": (
        "Superior Court Retention - Alice Beck Dubow",
        "",
    ),
    "Commonwealth Court Retention Election Question": (
        "Commonwealth Court Retention - Michael H Wojcik",
        "",
    ),
    # Local MDJ retention (Magisterial District Judge Paige Rosini).
    "Judicial Retention Question - Paige Rosini": (
        "Magisterial District Judge Retention - Paige Rosini",
        "",
    ),
}


def northumberland_retention(line: str):
    return NORTHUMBERLAND_RETENTION.get(line)


# --------------------------------------------------------------------------
# Ballot questions.
# --------------------------------------------------------------------------


HOME_RULE_RE = re.compile(
    r"^City of Shamokin Home Rule Charter Referendum\s+(.+)$"
)


def northumberland_ballot(line: str):
    m = HOME_RULE_RE.match(line)
    if m:
        return ("Home Rule Charter Referendum", m.group(1).strip())
    return None


# --------------------------------------------------------------------------
# Magisterial District Judge with "MDJ" keyword.
# --------------------------------------------------------------------------


MDJ_RE = re.compile(r"^Magisterial District Judge\s+MDJ\s+(.+)$")


def northumberland_mdj(line: str):
    m = MDJ_RE.match(line)
    if m:
        return ("Magisterial District Judge", m.group(1).strip())
    return None


# --------------------------------------------------------------------------
# Inspector/Judge of Election with embedded precinct -> strip precinct,
# emit district="".
# --------------------------------------------------------------------------


def northumberland_inspector_judge(line: str):
    for prefix, norm in (
        ("Inspector of Elections", "Inspector of Elections"),
        ("Inspector of Election", "Inspector of Elections"),
        ("Judge of Elections", "Judge of Elections"),
        ("Judge of Election", "Judge of Elections"),
    ):
        if line == prefix or line.startswith(prefix + " "):
            # Exclude "Judge of the..." court offices.
            rest = line[len(prefix):].lstrip()
            if rest.lower().startswith(("the ", "of the ", "of ")):
                continue
            return (norm, "")
    return None


# --------------------------------------------------------------------------
# Local offices.
#
# Strategy: Northumberland headers have the form "<OFFICE> <LEFT_MUNI>
# <RIGHT_MUNI>", where RIGHT_MUNI is a canonical full municipality name
# (possibly with "Region N" or "N" suffix), and LEFT_MUNI is either equal
# to it, a prefix of it, or absent entirely. Match the trailing canonical
# muni from a hardcoded list, then identify the office prefix on the left.
# --------------------------------------------------------------------------


NORTHUMBERLAND_MUNIS = [
    "Coal Township",
    "Delaware Township",
    "Diamond/Exchange Township",
    "East Cameron Township",
    "East Chillisquaque Township",
    "Herndon Borough",
    "Jackson Township",
    "Jordan Township",
    "Kulpmont Borough",
    "Lewis Township",
    "Little Mahanoy Township",
    "Locust Gap Township",
    "Lower Augusta Township",
    "Lower Mahanoy Township",
    "Marion Heights Borough",
    "McEwensville Borough",
    "Milton Borough",
    "Mount Carmel Borough",
    "Mount Carmel Township",
    "Natalie/Strong Township",
    "Northumberland Borough",
    "Point Township",
    "Ralpho Township",
    "Riverside Borough",
    "Rockefeller Township",
    "Rush Township",
    "Shamokin City",
    "Shamokin Township",
    "Snydertown Borough",
    "Sunbury City",
    "Turbot Township",
    "Turbotville Borough",
    "Upper Augusta Township",
    "Upper Mahanoy Township",
    "Washington Township",
    "Watsontown Borough",
    "West Cameron Township",
    "West Chillisquaque Township",
    "West Township",
    "Zerbe Township",
]
# Longest first so greedy matching doesn't mis-assign "Mount Carmel
# Borough" to "Mount Carmel Township".
NORTHUMBERLAND_MUNIS.sort(key=len, reverse=True)


MUNI_SUFFIX_RE = r"(?:\s+Region\s+\d+|\s+\d+(?:-\d+)?)?"
MUNI_MATCH_PATTERNS = [
    re.compile(rf"^(.*?)\s+({re.escape(m)}{MUNI_SUFFIX_RE})$")
    for m in NORTHUMBERLAND_MUNIS
]


# Prefix -> normalized office. Longest first so "Council Member" matches
# before any bare "Council" and "Tax Collector" before "Tax".
LOCAL_PREFIXES = [
    ("Council Member", "council"),
    ("Tax Collector", "tax_collector"),
    ("Commissioners", "commissioner"),
    ("Supervisor", "supervisor"),
    ("Auditors", "auditor"),
    ("Auditor", "auditor"),
    ("Constable", "constable"),
    ("Controller", "city_controller"),
    ("Treasurer", "city_treasurer"),
    ("Mayor", "mayor"),
]


def _normalize_local(kind: str, district: str) -> str:
    if kind == "council":
        # Cities get "City Council"; boroughs get "Borough Council".
        # Districts like "Sunbury City Region 4" also count as City.
        head = district.split()
        if "City" in head:
            return "City Council"
        return "Borough Council"
    return {
        "tax_collector": "Tax Collector",
        "commissioner": "Township Commissioner",
        "supervisor": "Township Supervisor",
        "auditor": "Township Auditor",
        "constable": "Constable",
        "city_controller": "City Controller",
        "city_treasurer": "City Treasurer",
        "mayor": "Mayor",
    }[kind]


def northumberland_local(line: str):
    for pat in MUNI_MATCH_PATTERNS:
        m = pat.match(line)
        if not m:
            continue
        left = m.group(1).strip()
        district = m.group(2).strip()
        for prefix, kind in LOCAL_PREFIXES:
            if left == prefix or left.startswith(prefix + " "):
                return (_normalize_local(kind, district), district)
        # Trailing muni matched but office prefix unknown — fall through.
        return None
    return None


# --------------------------------------------------------------------------
# School Director handler.
#
# Header forms:
#   "School Director LMSD Region 2 Line Mountain School District Region 2"
#   "School Director MASD Region 3"
#   "School Directors MCASD"
#   "School Directors SCSD Southern Columbia School District"
#   "School Directors Shikellamy School District"
#   "School Directors Danville Area School District Danville Area School District"
#     -> ("School Director [Region N]", "<District Name>")
# --------------------------------------------------------------------------


NORTHUMBERLAND_SCHOOLS = {
    "LMSD": "Line Mountain",
    "MASD": "Milton Area",
    "WRSD": "Warrior Run",
    "MCASD": "Mount Carmel Area",
    "SASD": "Shamokin Area",
    "SCSD": "Southern Columbia",
}
# Fallbacks for headers that don't use a code.
NORTHUMBERLAND_BARE_SCHOOLS = {
    "Danville Area School District": "Danville Area",
    "Shikellamy School District": "Shikellamy",
}


SCHOOL_PREFIX_RE = re.compile(
    r"^School Directors?\s+(.+)$"
)


def school_director(line: str):
    m = SCHOOL_PREFIX_RE.match(line)
    if not m:
        return None
    rest = m.group(1).strip()

    # Bare district (no code).
    for bare, name in NORTHUMBERLAND_BARE_SCHOOLS.items():
        if rest.startswith(bare):
            return ("School Director", name)

    # Code-prefixed.
    toks = rest.split()
    if not toks or toks[0] not in NORTHUMBERLAND_SCHOOLS:
        return None
    district = NORTHUMBERLAND_SCHOOLS[toks[0]]
    toks = toks[1:]

    region: Optional[str] = None
    if len(toks) >= 2 and toks[0] == "Region" and toks[1].isdigit():
        region = toks[1]

    office = "School Director"
    if region:
        office += f" Region {region}"
    return (office, district)


# --------------------------------------------------------------------------
# Exact county offices (mixed-case).
# --------------------------------------------------------------------------


EXACT_OFFICES = {
    "Judge of the Superior Court": ("Judge of the Superior Court", ""),
    "Judge of the Commonwealth Court": ("Judge of the Commonwealth Court", ""),
    "Judge of The Court of Common Pleas": ("Judge of the Court of Common Pleas", ""),
    "Judge of the Court of Common Pleas": ("Judge of the Court of Common Pleas", ""),
    "District Attorney": ("District Attorney", ""),
    "Sheriff": ("Sheriff", ""),
    "Coroner": ("Coroner", ""),
    "Controller": ("Controller", ""),
    "Treasurer": ("Treasurer", ""),
    "County Treasurer": ("Treasurer", ""),
    "Prothonotary/Clerk of Courts": ("Prothonotary and Clerk of Courts", ""),
    "Prothonotary and Clerk of Courts": ("Prothonotary and Clerk of Courts", ""),
    "Recorder of Deeds, Register of Wills, and Clerk of the Orphans' Court": (
        "Recorder of Deeds, Register of Wills, and Clerk of the Orphans' Court",
        "",
    ),
    "County Commissioner": ("County Commissioner", ""),
    "County Commissioners": ("County Commissioner", ""),
}


CONFIG = ElectionwareConfig(
    county="Northumberland",
    skip_prefixes=SKIP_PREFIXES,
    county_header_suffix="Northumberland County",
    exact_offices=EXACT_OFFICES,
    local_offices=[],  # all local-office handling goes through the extra handler
    local_office_orientation="prefix",
    retention_style="retention",  # unused; handled by extra handler
    extra_office_handlers=[
        northumberland_retention,
        northumberland_ballot,
        northumberland_mdj,
        northumberland_inspector_judge,
        northumberland_local,
    ],
    municipality_normalizer=identity,  # PDF already title-case
    school_director_handler=school_director,
    include_magisterial=False,  # handled by northumberland_mdj
    line_preprocessor=northumberland_line_preprocessor,
    prettify_precinct=identity,  # already title-case
)


if __name__ == "__main__":
    run_cli(CONFIG)
