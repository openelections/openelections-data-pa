#!/usr/bin/env python3
"""
Parse Montour County PA 2023 General (Municipal) Election results.

Source: Montour County Official Precinct Summary 2023 General.pdf (208
pages, Electionware "Precinct Report").  The pdftotext -layout output
(work2023/txt/) separates the columns cleanly, so this parser works on text
rather than the natural-pdf engines.

Structure: one section per MUNICIPALITY (15 municipalities; the finest
granularity Montour reports), each repeating the countywide contests plus
its own local contests.  Municipality names are used as the ``precinct``
value.  Candidate rows carry an extra ``VOTE %`` column, which is stripped
before parsing.

Montour-specific header quirks (same family as the 2025 parser):
  * "COUNCILMAN" is Montour's label for Borough Council.
  * Local office headers use TWP/BORO abbreviations and sometimes drop the
    municipality type ("SUPERVISOR MAHONING"); expanded/normalized here.
  * "AUDITOR"/"COUNCILMAN" for Washingtonville Borough map to
    "Borough Auditor"/"Borough Council"; townships get the Township- forms.
  * School Director headers lead with the district name:
      "DANVILLE AREA SCHOOL DISTRICT SCHOOL DIRECTOR 4YR"
        -> School Director (4 Year) / Danville Area
      "WARRIOR RUN SCHOOL DISTRICT SCHOOL DIRECTOR"
        -> School Director / Warrior Run
  * "PROTHONOTARY AND CLERK OF THE SEVERAL COURTS" ->
    "Prothonotary and Clerk of Courts";
    "REGISTER OF WILLS AND RECORDER OF DEEDS" ->
    "Register of Wills and Recorder of Deeds".
  * "MAGISTERIAL DISTRICT JUDGE" carries no district number in this source;
    district is left empty.
  * Retention headers are named: "SUPERIOR COURT RETENTION - JACK PANELLA"
    -> Superior Court Retention - Jack Panella; "SUPERIOR COURT RETENTION -
    VICTOR P. STABILE" -> Commonwealth Court Retention - Victor P Stabile
    (the report's "SUPERIOR COURT" label on Stabile is a mislabel; he is a
    Commonwealth Court judge).
  * Precinct names are ALL-CAPS; title-cased with Roman numeral preservation.

Usage:
    python parsers/pa_montour_general_2023_results_parser.py \\
        <input.txt-or-.pdf> <output.csv> [--county Montour]
"""

import re

try:
    from electionware_txt_2023 import run, ROMAN_RE
except ImportError:
    import sys, os
    sys.path.insert(0, os.path.dirname(__file__))
    from electionware_txt_2023 import run
    try:
        from electionware_txt_2023 import ROMAN_RE
    except ImportError:
        ROMAN_RE = re.compile(r"^[IVX]+$")


COUNTY = "Montour"
PAGE_HEADER_KEY = "Montour County, Pennsylvania"
HEADER_SKIP = ("Statistics",)

EXACT_OFFICES = {
    "JUSTICE OF THE SUPREME COURT": ("Justice of the Supreme Court", ""),
    "JUDGE OF THE SUPERIOR COURT": ("Judge of the Superior Court", ""),
    "JUDGE OF THE COMMONWEALTH COURT": ("Judge of the Commonwealth Court", ""),
    "JUDGE OF THE COURT OF COMMON PLEAS": ("Judge of the Court of Common Pleas", ""),
    "COUNTY COMMISSIONER": ("County Commissioner", ""),
    "COUNTY AUDITOR": ("County Auditor", ""),
    "PROTHONOTARY AND CLERK OF THE SEVERAL COURTS":
        ("Prothonotary and Clerk of Courts", ""),
    "REGISTER OF WILLS AND RECORDER OF DEEDS":
        ("Register of Wills and Recorder of Deeds", ""),
    "DISTRICT ATTORNEY": ("District Attorney", ""),
    "SHERIFF": ("Sheriff", ""),
    "CORONER": ("Coroner", ""),
}

MAGISTERIAL_RE = re.compile(r"^MAGISTERIAL DISTRICT JUDGE\s*$")

RETENTION_OFFICES = {
    "SUPERIOR COURT RETENTION - JACK PANELLA":
        "Superior Court Retention - Jack Panella",
    "SUPERIOR COURT RETENTION - VICTOR P. STABILE":
        "Commonwealth Court Retention - Victor P Stabile",
}

TERM_TOKEN_RE = re.compile(r"^(\d)YR$")
SUP_RE = re.compile(r"^SUPERVISOR (.+)$")
AUD_RE = re.compile(r"^AUDITOR (.+)$")
COUNCILMAN_RE = re.compile(r"^COUNCILMAN (.+)$")
MAYOR_RE = re.compile(r"^MAYOR (.+)$")
TAX_RE = re.compile(r"^TAX COLLECTOR (.+)$")
DANVILLE_SCHOOL_RE = re.compile(
    r"^DANVILLE AREA SCHOOL DISTRICT SCHOOL DIRECTOR (\d)YR$")
WARRIOR_SCHOOL_RE = re.compile(
    r"^WARRIOR RUN SCHOOL DISTRICT SCHOOL DIRECTOR$")

ROMAN_WORDS = {"I", "II", "III", "IV", "V"}


def _expand(raw: str) -> str:
    """Expand TWP/BORO abbreviations and title-case, keeping Roman numerals
    uppercase."""
    s = re.sub(r"\bTWP\b", "Township", raw, flags=re.IGNORECASE)
    s = re.sub(r"\bBORO\b", "Borough", s, flags=re.IGNORECASE)
    out = []
    for i, w in enumerate(s.split()):
        if w.upper() in ROMAN_WORDS:
            out.append(w.upper())
        elif i > 0 and w.lower() in ("of", "the", "and"):
            out.append(w.lower())
        else:
            out.append(w.capitalize())
    return " ".join(out)


def _strip_term(rest: str):
    """Split a trailing/leading "6YR" term token; returns (rest, years)."""
    tokens = rest.split()
    if tokens:
        m = re.match(r"^(\d)YR$", tokens[0])
        if m:
            return " ".join(tokens[1:]), m.group(1)
        m = re.match(r"^(\d)YR$", tokens[-1])
        if m:
            return " ".join(tokens[:-1]), m.group(1)
    return rest, None


def montour_precinct_name(name: str) -> str:
    """Title-case an ALL-CAPS precinct/municipality, keeping Roman numerals."""
    return _expand(name)


def montour_map_header(header: str):
    h = header.strip()
    if h in EXACT_OFFICES:
        return EXACT_OFFICES[h]
    if h in RETENTION_OFFICES:
        return (RETENTION_OFFICES[h], "")
    if MAGISTERIAL_RE.match(h):
        return ("Magisterial District Judge", "")
    m = DANVILLE_SCHOOL_RE.match(h)
    if m:
        return (f"School Director ({m.group(1)} Year)", "Danville Area")
    if WARRIOR_SCHOOL_RE.match(h):
        return ("School Director", "Warrior Run")
    m = SUP_RE.match(h)
    if m:
        rest, years = _strip_term(m.group(1).strip())
        office = "Township Supervisor"
        if years:
            office += f" ({years} Year)"
        return (office, _expand(rest))
    m = AUD_RE.match(h)
    if m:
        rest, years = _strip_term(m.group(1).strip())
        district = _expand(rest)
        office = ("Borough Auditor" if "Borough" in district
                  else "Township Auditor")
        if years:
            office += f" ({years} Year)"
        return (office, district)
    m = COUNCILMAN_RE.match(h)
    if m:
        rest, years = _strip_term(m.group(1).strip())
        office = "Borough Council"
        if years:
            office += f" ({years} Year)"
        return (office, _expand(rest))
    m = MAYOR_RE.match(h)
    if m:
        return ("Mayor", _expand(m.group(1).strip()))
    m = TAX_RE.match(h)
    if m:
        return ("Tax Collector", _expand(m.group(1).strip()))
    return None


def _is_header(line: str) -> bool:
    h = line.strip()
    if h in EXACT_OFFICES or h in RETENTION_OFFICES:
        return True
    return bool(
        MAGISTERIAL_RE.match(h)
        or DANVILLE_SCHOOL_RE.match(h)
        or WARRIOR_SCHOOL_RE.match(h)
        or SUP_RE.match(h)
        or AUD_RE.match(h)
        or COUNCILMAN_RE.match(h)
        or MAYOR_RE.match(h)
        or TAX_RE.match(h)
    )


if __name__ == "__main__":
    run(COUNTY, montour_map_header,
        page_header_key=PAGE_HEADER_KEY,
        header_skip=HEADER_SKIP,
        strip_pct=True,
        is_header=_is_header,
        prettify_precinct=montour_precinct_name)