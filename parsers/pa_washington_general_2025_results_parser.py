#!/usr/bin/env python3
"""
Parse Washington County PA 2025 General (Municipal) Election precinct
results.

Source: Washington PA 2025_Municipal_Election_Precinct_Summary_Official.pdf
(1578 pages, 180 precincts, Electionware format with ALL-CAPS headers
and numerous Washington-specific quirks).

Washington-specific quirks:
  - **ALL-CAPS** precinct names, office headers, and candidate names.
    Prettifier title-cases precincts; office normalization is via
    exact_offices + extra handlers + prefix handler.
  - **Mixed-case party codes**: "Dem", "Rep", "Dem/Rep" instead of
    "DEM"/"REP"/"DEM/REP". The shared PARTY_RE is now
    case-insensitive and normalizes to uppercase.
  - **Verbose term tokens**: "2 YEARS", "4 YEARS", "6 YEARS" — line
    preprocessor rewrites them to "2YR"/"4YR"/"6YR".
  - **Typo "AUDTIOR"** for "AUDITOR" in at least one entry (BUFFALO
    2 YEARS). Line preprocessor corrects.
  - **Retention contests named as regular courts with judge surname
    appended**: "JUSTICE OF SUPREME COURT DONOHUE" (not the standard
    "retention question" form). Explicit map.
  - **Court of Common Pleas retention**: "JUDGE OF THE COURT OF
    COMMON PLEAS 27TH JUDICIAL DISTRICT GILMAN". Explicit map.
  - **Wrapped column headers**: "TOTAL Ele D c a t y ion Mail In
    Provisional" with "Day" on a follow-up line.
  - **"Write-In: Scattered"** instead of the usual "Write-In:
    <candidate>"; already handled via the standard "Write-In:" skip.
  - **Bare "COUNCIL" prefix** (not "BOROUGH COUNCIL" or "MEMBER OF
    COUNCIL"): maps to Borough Council.
  - **"COUNCIL AT LARGE <muni>"**: dedicate handler to emit
    "Borough Council At Large" with district.
  - **"CITY COUNCIL <city>"**, **"CITY CONTROLLER <city>"**: custom
    handler for Monongahela and Washington city offices.
  - **"COMMISSIONER EAST BETHLEHEM SECOND WARD"**: custom handler
    emitting "Township Commissioner" + ward-tagged district.
  - **MDJ with doubled prefix**: "MAGISTERIAL DISTRICT JUDGE
    MAGISTERIAL DISTRICT 27-1-03" — custom handler strips the extra
    "MAGISTERIAL DISTRICT" and passes through.
  - **School director** — TWO naming patterns:
      a. Region-style (district name first):
         "AVELLA AREA SCHOOL DIRECTOR REGION I [NYR]"
         "CANON MCMILLAN SCHOOL DIRECTOR CANONSBURG EXCEPT CBG 1-4"
      b. At-large / district-wide (SCHOOL DIRECTOR prefix):
         "SCHOOL DIRECTOR AT LARGE RINGGOLD SCHOOL DISTRICT"
         "SCHOOL DIRECTOR BENTWORTH AREA SCHOOL DISTRICT [NYR]"
      c. Typo: "SCHOOL DISTRICT" used instead of "SCHOOL DIRECTOR"
         in "BURGETTSTOWN AREA SCHOOL DISTRICT REGION I" and
         "TRINITY AREA SCHOOL DISTRICT REGION II".
"""

import re
from typing import Optional

from electionware_precinct_np import (
    ElectionwareConfig,
    TERM_TOKEN_RE,
    identity,
    title_case,
    run_cli,
)


# --------------------------------------------------------------------------
# Line preprocessor.
# --------------------------------------------------------------------------


YEARS_RE = re.compile(r"\b(\d+)\s+YEARS?\b", re.IGNORECASE)


def washington_line_preprocessor(line: str) -> str:
    line = YEARS_RE.sub(r"\1YR", line)
    line = line.replace("AUDTIOR", "AUDITOR")
    return line


# --------------------------------------------------------------------------
# Skip prefixes.
# --------------------------------------------------------------------------


SKIP_PREFIXES = (
    "Summary Results Report OFFICIAL RESULTS",
    "Summary Results Report UNOFFICIAL RESULTS",
    "2025 Municipal Election",
    "November 4, 2025 Washington",
    "Precinct Summary - ",
    "Report generated with Electionware",
    # Column header fragments (many wrapped variants).
    "Provisional",
    "TOTAL Election Day Mail In Provisional",
    "TOTAL Election Day Mail",
    "TOTAL Ele",
    "TOTAL Mail In Provisional",
    "Election",
    "Day",
    "Votes",
    "Mail In",
    "Voter Turnout - Total",
    "Vote For ",
    "Total Votes Cast",
    "Contest Totals",
    "Overvotes",
    "Undervotes",
    "Not Assigned",
)


# --------------------------------------------------------------------------
# Retention: explicit map.
# --------------------------------------------------------------------------


WASHINGTON_RETENTION = {
    "JUSTICE OF SUPREME COURT DONOHUE": (
        "Supreme Court Retention - Donohue",
        "",
    ),
    "JUSTICE OF SUPREME COURT DOUGHERTY": (
        "Supreme Court Retention - Dougherty",
        "",
    ),
    "JUSTICE OF SUPREME COURT WECHT": (
        "Supreme Court Retention - Wecht",
        "",
    ),
    "JUDGE OF THE SUPERIOR COURT DUBOW": (
        "Superior Court Retention - Dubow",
        "",
    ),
    "JUDGE OF THE COMMONWEALTH COURT WOJCIK": (
        "Commonwealth Court Retention - Wojcik",
        "",
    ),
    "JUDGE OF THE COURT OF COMMON PLEAS 27TH JUDICIAL DISTRICT GILMAN": (
        "Court of Common Pleas Retention - Gilman",
        "",
    ),
}


def washington_retention(line: str):
    return WASHINGTON_RETENTION.get(line)


# --------------------------------------------------------------------------
# Inspector / Judge of Election with embedded precinct.
# --------------------------------------------------------------------------


def washington_inspector_judge(line: str):
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
# "COUNCIL AT LARGE <muni>".
# --------------------------------------------------------------------------


def washington_council_at_large(line: str):
    if line.startswith("COUNCIL AT LARGE "):
        muni = line[len("COUNCIL AT LARGE "):].strip()
        return ("Borough Council At Large", title_case(muni))
    return None


# --------------------------------------------------------------------------
# "CITY COUNCIL <city>" / "CITY CONTROLLER <city>".
# --------------------------------------------------------------------------


def washington_city(line: str):
    if line.startswith("CITY COUNCIL "):
        muni = line[len("CITY COUNCIL "):].strip()
        return ("City Council", title_case(muni))
    if line.startswith("CITY CONTROLLER "):
        muni = line[len("CITY CONTROLLER "):].strip()
        return ("City Controller", title_case(muni))
    return None


# --------------------------------------------------------------------------
# "COMMISSIONER EAST BETHLEHEM SECOND WARD" - first-class township
# commissioner with ward.
# --------------------------------------------------------------------------


COMMISSIONER_WARD_RE = re.compile(
    r"^COMMISSIONER\s+(.+?)\s+(FIRST|SECOND|THIRD|FOURTH|FIFTH|SIXTH)\s+WARD$",
    re.IGNORECASE,
)
WARD_MAP = {
    "FIRST": "1",
    "SECOND": "2",
    "THIRD": "3",
    "FOURTH": "4",
    "FIFTH": "5",
    "SIXTH": "6",
}


def washington_commissioner(line: str):
    m = COMMISSIONER_WARD_RE.match(line)
    if not m:
        return None
    muni = title_case(m.group(1))
    ward = WARD_MAP.get(m.group(2).upper(), m.group(2))
    return ("Township Commissioner", f"{muni} Ward {ward}")


# --------------------------------------------------------------------------
# MDJ with doubled prefix:
#   "MAGISTERIAL DISTRICT JUDGE MAGISTERIAL DISTRICT 27-1-03"
# --------------------------------------------------------------------------


MDJ_RE = re.compile(
    r"^MAGISTERIAL DISTRICT JUDGE(?:\s+MAGISTERIAL DISTRICT)?\s+(.+)$",
    re.IGNORECASE,
)


def washington_mdj(line: str):
    m = MDJ_RE.match(line)
    if not m:
        return None
    return ("Magisterial District Judge", m.group(1).strip())


# --------------------------------------------------------------------------
# School director handler.
#
# Two patterns:
#
# A) District-first (region style):
#    "<DISTRICT> SCHOOL DIRECTOR <geography> [NYR]"
#    "<DISTRICT> SCHOOL DIRECTOR REGION I/II/III [NYR]"
#    Also handles the typo "<DISTRICT> SCHOOL DISTRICT REGION I" where
#    "SCHOOL DISTRICT" should be "SCHOOL DIRECTOR".
#
# B) "SCHOOL DIRECTOR" prefix:
#    "SCHOOL DIRECTOR AT LARGE <district> SCHOOL DISTRICT"
#    "SCHOOL DIRECTOR <district> SCHOOL DISTRICT [NYR]"
# --------------------------------------------------------------------------


# Pattern B: SCHOOL DIRECTOR prefix
SCHOOL_PREFIX_RE = re.compile(
    r"^SCHOOL DIRECTOR (AT LARGE )?(.+?)(?:\s+SCHOOL DISTRICT(?:RICT)?)?"
    r"(?:\s+(\d+)YR)?$",
    re.IGNORECASE,
)

# Pattern A: <DISTRICT> SCHOOL DIRECTOR <geography>
#            <DISTRICT> SCHOOL DISTRICT <geography>  (typo)
SCHOOL_SUFFIX_RE = re.compile(
    r"^(.+?)\s+SCHOOL\s+(?:DIRECTOR|DISTRICT)\s+(.+?)(?:\s+(\d+)YR)?$",
    re.IGNORECASE,
)


def school_director(line: str):
    # Pattern B first (more specific prefix).
    m = SCHOOL_PREFIX_RE.match(line)
    if m and m.group(2):
        at_large = m.group(1) is not None
        district = m.group(2).strip()
        years = m.group(3)
        # Remove trailing "SCHOOL DISTRICT" if the regex captured it
        # inside group(2).
        district = re.sub(r"\s+SCHOOL DISTRICT$", "", district, flags=re.IGNORECASE)
        district = title_case(district)
        office = "School Director"
        if at_large:
            office += " At Large"
        if years:
            office += f" ({years} Year)"
        return (office, district)

    # Pattern A: district-first.
    m = SCHOOL_SUFFIX_RE.match(line)
    if m:
        district_name = title_case(m.group(1).strip())
        geography = m.group(2).strip()
        years = m.group(3)
        # Geography can be "REGION I", "REGION II", "CANONSBURG EXCEPT
        # CBG 1-4", "CECIL AND CBG 1-4", "NORTH STRABANE", etc.
        office = "School Director"
        # Detect "REGION <roman numeral>" vs freeform geo.
        region_m = re.match(r"^REGION\s+(\S+)$", geography, re.IGNORECASE)
        if region_m:
            office += f" Region {region_m.group(1)}"
        else:
            office += f" {title_case(geography)}"
        if years:
            office += f" ({years} Year)"
        return (office, district_name)

    return None


# --------------------------------------------------------------------------
# Exact-match offices (after line preprocessor).
# --------------------------------------------------------------------------


EXACT_OFFICES = {
    "JUDGE OF THE SUPERIOR COURT": ("Judge of the Superior Court", ""),
    "JUDGE OF THE COMMONWEALTH COURT": ("Judge of the Commonwealth Court", ""),
    "COUNTY CONTROLLER": ("County Controller", ""),
    "RECORDER OF DEEDS": ("Recorder of Deeds", ""),
    "SHERIFF": ("Sheriff", ""),
    "DISTRICT ATTORNEY": ("District Attorney", ""),
    "REGISTER OF WILLS": ("Register of Wills", ""),
}


# --------------------------------------------------------------------------
# Prefix-style local offices.
# --------------------------------------------------------------------------


LOCAL_OFFICES = [
    ("SUPERVISOR", "Township Supervisor"),
    ("COUNCIL", "Borough Council"),
    ("TAX COLLECTOR", "Tax Collector"),
    ("AUDITOR", "Township Auditor"),
    ("CONSTABLE", "Constable"),
    ("MAYOR", "Mayor"),
]


# --------------------------------------------------------------------------
# Precinct prettifier: title-case the ALL-CAPS name.
# --------------------------------------------------------------------------


def prettify_washington_precinct(name: str) -> str:
    out = []
    for t in name.split():
        up = t.upper()
        if up == "MT":
            out.append("Mt")
        elif t.startswith("#"):
            out.append(t)
        elif t.isdigit() or re.match(r"^\d+[A-Z]$", t):
            # "1W", "2W", bare digits
            out.append(t)
        elif len(t) <= 2 and t.isalpha() and t.isupper():
            # "1W" handled above; keep short alpha like "W" uppercase
            out.append(t)
        else:
            out.append(t.capitalize())
    s = " ".join(out)
    return s


CONFIG = ElectionwareConfig(
    county="Washington",
    skip_prefixes=SKIP_PREFIXES,
    county_header_suffix="Washington",
    exact_offices=EXACT_OFFICES,
    local_offices=LOCAL_OFFICES,
    local_office_orientation="prefix",
    retention_style="retention",  # unused; explicit handler
    municipality_normalizer=title_case,
    extra_office_handlers=[
        washington_retention,
        washington_inspector_judge,
        washington_council_at_large,
        washington_city,
        washington_commissioner,
        washington_mdj,
    ],
    school_director_handler=school_director,
    prettify_precinct=prettify_washington_precinct,
    line_preprocessor=washington_line_preprocessor,
    include_common_pleas=False,   # handled by retention map
    include_magisterial=False,    # handled by washington_mdj
)


if __name__ == "__main__":
    run_cli(CONFIG)
