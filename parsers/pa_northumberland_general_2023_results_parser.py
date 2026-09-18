#!/usr/bin/env python3
"""
Parse Northumberland County PA 2023 General (Municipal) Election precinct
results.

Source: Northumberland County Official Precinct Results 2023 General.pdf
(407 pages, Electionware).  The pdftotext -layout output (work2023/txt/)
separates the columns cleanly, so this parser works on text rather than the
natural-pdf engines.

Structure: one section per PRECINCT (74 precincts), each repeating the
countywide contests plus its own local contests.  Same format family as
pa_northumberland_general_2025_results_parser.py.

Northumberland-specific quirks handled here:
  * **Two identically-titled "SUPERIOR COURT RETENTION" sections per
    precinct** — the 2023 statewide judicial retentions, printed in ballot
    order (verified: the first matches Jack Panella's ~65% yes statewide
    margin and Montour's report names them in this order): #1 = Superior
    Court Retention - Jack Panella, #2 = Commonwealth Court Retention -
    Victor P Stabile.  Disambiguated by occurrence counter (reset on any
    other header).
  * **"COUNTY DISTRICT ATTORNEY"**, "COUNTY TREASURER" (-> "Treasurer"),
    "COUNTY SHERIFF" (-> "Sheriff") prefixed county offices.
  * **School Director headers** of the form
    "<DISTRICT CODE> SCHOOL DIRECTOR <Name> School District [Region N]":
    SHIKELLAMY/SHAMOKIN/MOUNT CARMEL/DANVILLE AREA/SOUTHERN COLUMBIA (no
    region) and LINE MOUNTAIN AREA/MILTON AREA/WARRIOR RUN AREA (with
    "Region N" -> "School Director Region N").
  * **"REFERENDUM QUESTION Shikellamy School District"** — a school-district
    ballot question whose text is not printed in the report; emitted as
    office "Referendum Question", district "Shikellamy".
  * "CITY COUNCIL/CITY CONTROLLER <City>" -> City Council / City Controller;
    "TOWNSHIP COMMISSIONER" -> Township Commissioner; plain "AUDITOR" ->
    "Township Auditor" (matches the 2025 parser's convention, even for
    boroughs).
  * Mixed-case party prefixes ("Dem", "Rep", "Dem/Rep") — matched
    case-insensitively.
  * Wrapped statistics rows ("Ballots Cast - Total" with numbers on the
    preceding line) and "Mail/Absent" column-header fragments.

Usage:
    python parsers/pa_northumberland_general_2023_results_parser.py \\
        <input.txt-or-.pdf> <output.csv> [--county Northumberland]
"""

import re

try:
    from electionware_txt_2023 import run
except ImportError:
    import sys, os
    sys.path.insert(0, os.path.dirname(__file__))
    from electionware_txt_2023 import run


COUNTY = "Northumberland"
PAGE_HEADER_KEY = "Northumberland County"
HEADER_SKIP = ("STATISTICS",)

EXACT_OFFICES = {
    "JUSTICE OF THE SUPREME COURT": ("Justice of the Supreme Court", ""),
    "JUDGE OF THE SUPERIOR COURT": ("Judge of the Superior Court", ""),
    "JUDGE OF THE COMMONWEALTH COURT": ("Judge of the Commonwealth Court", ""),
    "JUDGE OF THE COURT OF COMMON PLEAS": ("Judge of the Court of Common Pleas", ""),
    "COUNTY COMMISSIONER": ("County Commissioner", ""),
    "COUNTY TREASURER": ("Treasurer", ""),
    "COUNTY SHERIFF": ("Sheriff", ""),
    "COUNTY DISTRICT ATTORNEY": ("District Attorney", ""),
}

MDJ_RE = re.compile(r"^MAGISTERIAL DISTRICT JUDGE (\d{2}-\d-\d{2})$")
RETENTION = "SUPERIOR COURT RETENTION"

SCHOOL_RE = re.compile(r"^(.+?) SCHOOL DIRECTOR (.+)$")
SCHOOL_NAMES = {
    "SHIKELLAMY": "Shikellamy",
    "SHAMOKIN": "Shamokin Area",
    "MOUNT CARMEL": "Mount Carmel Area",
    "DANVILLE AREA": "Danville Area",
    "SOUTHERN COLUMBIA": "Southern Columbia",
    "LINE MOUNTAIN AREA": "Line Mountain",
    "MILTON AREA": "Milton Area",
    "WARRIOR RUN AREA": "Warrior Run",
}
REGION_RE = re.compile(r"Region (\d+)$")

# Prefix-style local offices, longest first.
LOCAL_PREFIXES = [
    ("CITY CONTROLLER", "City Controller"),
    ("TOWNSHIP COMMISSIONER", "Township Commissioner"),
    ("TOWNSHIP SUPERVISOR", "Township Supervisor"),
    ("BOROUGH COUNCIL", "Borough Council"),
    ("CITY COUNCIL", "City Council"),
    ("TAX COLLECTOR", "Tax Collector"),
    ("REFERENDUM QUESTION", "Referendum Question"),
    ("AUDITOR", "Township Auditor"),
    ("CONSTABLE", "Constable"),
    ("MAYOR", "Mayor"),
]


def _strip_school_district(name: str) -> str:
    return re.sub(r"\s*School District\s*$", "", name).strip()


def _make_counter_map():
    """Positional disambiguation of the two identical retention headers."""
    state = {"n": 0}

    def map_header(header: str):
        h = header.strip()
        if h in EXACT_OFFICES:
            state["n"] = 0
            return EXACT_OFFICES[h]
        m = MDJ_RE.match(h)
        if m:
            state["n"] = 0
            return ("Magisterial District Judge", m.group(1))
        if h == RETENTION:
            state["n"] += 1
            if state["n"] % 2 == 1:
                return ("Superior Court Retention - Jack Panella", "")
            return ("Commonwealth Court Retention - Victor P Stabile", "")
        m = SCHOOL_RE.match(h)
        if m and m.group(1).strip() in SCHOOL_NAMES:
            state["n"] = 0
            district = SCHOOL_NAMES[m.group(1).strip()]
            rest = m.group(2).strip()
            rm = REGION_RE.search(rest)
            office = "School Director"
            if rm:
                office += f" Region {rm.group(1)}"
            return (office, district)
        m = re.match(r"^REFERENDUM QUESTION\s+(.+)$", h)
        if m:
            state["n"] = 0
            return ("Referendum Question", _strip_school_district(m.group(1)))
        for prefix, office in LOCAL_PREFIXES:
            if h == prefix or h.startswith(prefix + " "):
                state["n"] = 0
                return (office, h[len(prefix):].strip())
        return None

    return map_header


def northumberland_map(header: str):
    return _MAPPER(header)


_MAPPER = _make_counter_map()


def _is_header(line: str) -> bool:
    h = line.strip()
    if h in EXACT_OFFICES or h == RETENTION:
        return True
    return bool(
        MDJ_RE.match(h)
        or SCHOOL_RE.match(h)
        or any(h == p or h.startswith(p + " ") for p, _ in LOCAL_PREFIXES)
    )


if __name__ == "__main__":
    run(COUNTY, northumberland_map,
        page_header_key=PAGE_HEADER_KEY,
        header_skip=HEADER_SKIP,
        is_header=_is_header)