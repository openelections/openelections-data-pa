#!/usr/bin/env python3
"""
Parse Tioga County PA 2023 General (Municipal) Election results.

Source: Tioga County Official Results 2023 General.pdf (293 pages,
Electionware).  The pdftotext -layout output (work2023/txt/) separates the
columns cleanly, so this parser works on text rather than the natural-pdf
engines.

Structure of the source:
  * A countywide "Summary Results Report" (52 pages) — used only for
    validation, not parsed.
  * A "Precinct Results Report" (241 pages) with one section per
    municipality (41 municipalities = 41 precincts; the finest granularity
    Tioga reports).  Each municipality repeats every countywide contest
    (Justice of the Supreme Court, Judge of the Superior Court, Judge of the
    Commonwealth Court, County Commissioner, County Auditor, Register and
    Recorder, District Attorney, Coroner, Magisterial District Judge 04-3-02,
    two retention questions) followed by its local contests.  Municipality
    names are used as the ``precinct`` value.

Tioga-specific header tokens (mapped to standard offices below):
  * "SUP 6YR Bloss Township"        -> Township Supervisor (6 Year)
  * "AUDITOR 6YR/4YR/2 YR <muni>"   -> Township Auditor (N Year)
  * "TAX COLLECTOR 2YR <muni>"      -> Tax Collector
  * "MOC 3/4YR Blossburg Borough"   -> Borough Council (4 Year)
    (Member of Council; "MOC n/mYR" = n seats, m-year term).  Two
    Wellsboro variants lack normal spacing: "MOC 2/4YR" appears inside the
    Wellsboro Borough Ward One section (its 4-year council contest) and
    "MOC1/4YRWellsboro Boro2" is Ward Two's 4-year contest.
  * School codes: STSD (Southern Tioga), NTSD (Northern Tioga), WASD
    (Wellsboro Area), GASD (Galeton Area), CASD (Canton Area), with
    "REG r/..." region tokens and optional "2YR TERM" suffixes.
  * "RETENTION QUESTION #1/#2" are the two 2023 statewide judicial
    retentions, printed in ballot order: #1 = Superior Court - Jack Panella,
    #2 = Commonwealth Court - Victor P. Stabile (verified against other
    counties' named retention reports, e.g. Blair and Montour).
  * "MAGISTERIAL DISTRICT JUDGE 04-3-02" is the only MDJ district.

Usage:
    python parsers/pa_tioga_general_2023_results_parser.py \\
        <input.txt-or-.pdf> <output.csv> [--county Tioga]
"""

import re

try:
    from electionware_txt_2023 import run
except ImportError:  # allow running from repo root
    import sys, os
    sys.path.insert(0, os.path.join(os.path.dirname(__file__)))
    from electionware_txt_2023 import run


COUNTY = "Tioga"

# "November 7, 2023   Electionware County" precedes each municipality name.
PAGE_HEADER_KEY = "Electionware County"
PAGE_FILTER = lambda chunk: "Precinct Results Report" in chunk  # noqa: E731

TERM_YEARS = {"1": 1, "2": 2, "4": 4, "6": 6}


def _muni(raw: str) -> str:
    """Normalize a Tioga municipality name from a contest header."""
    s = raw.strip()
    s = re.sub(r"\btownship\b", "Township", s)
    s = re.sub(r"\bBoro(?![a-z])", "Borough", s)
    # Duplicated trailing municipality ("Jackson Township Jackson Township").
    if " " in s:
        parts = s.split()
        n = len(parts) // 2
        if parts[:n] == parts[n:]:
            s = " ".join(parts[:n])
    return s


EXACT_OFFICES = {
    "JUSTICE OF THE SUPREME COURT": ("Justice of the Supreme Court", ""),
    "JUDGE OF THE SUPERIOR COURT": ("Judge of the Superior Court", ""),
    "JUDGE OF THE COMMONWEALTH COURT": ("Judge of the Commonwealth Court", ""),
    "COUNTY COMMISSIONER": ("County Commissioner", ""),
    "COUNTY AUDITOR": ("County Auditor", ""),
    "REGISTER AND RECORDER": ("Register and Recorder", ""),
    "DISTRICT ATTORNEY": ("District Attorney", ""),
    "CORONER": ("Coroner", ""),
}

MDJ_RE = re.compile(r"^MAGISTERIAL DISTRICT JUDGE (\d{2}-\d-\d{2})$")
SUP_RE = re.compile(r"^SUP (\d)YR (.+)$")
AUD_RE = re.compile(r"^AUDITOR (\d)\s*YR (.+)$")
TAX_RE = re.compile(r"^TAX COLLECTOR (\d)YR (.+)$")
MOC_RE = re.compile(r"^MOC (\d+)/(\d)YR (.+)$")
MOC_BARE_RE = re.compile(r"^MOC (\d)/(\d)YR$")          # no muni: Wellsboro W1
MOC_GLUED_RE = re.compile(r"^MOC(\d)/(\d)YR\s*Wellsboro Boro(\d)$")
RETENTION_RE = re.compile(r"^RETENTION QUESTION #(\d)$")

RETENTION_OFFICES = {
    1: "Superior Court Retention - Jack Panella",
    2: "Commonwealth Court Retention - Victor P Stabile",
}

SCHOOL_RE = re.compile(r"^(STSD|NTSD|WASD|GASD|CASD)\s+(.+)$")
SCHOOLS = {
    "STSD": "Southern Tioga",
    "NTSD": "Northern Tioga",
    "WASD": "Wellsboro Area",
    "GASD": "Galeton Area",
    "CASD": "Canton Area",
}
REGION_TERM_RE = re.compile(r"REG (\d)(?:/(\d+)YR\s+TERM)?")
LEAD_TERM_RE = re.compile(r"^(\d)YR TERM\s+")
REGION_TAIL_RE = re.compile(r"\bRegion (\d+)$")


def _school(header: str):
    m = SCHOOL_RE.match(header)
    if not m:
        return None
    district = SCHOOLS[m.group(1)]
    rest = m.group(2).strip()
    region = None
    years = None

    rt = REGION_TERM_RE.search(rest)
    if rt:
        region = rt.group(1)
        if rt.group(2):
            years = rt.group(2)
        rest = rest[: rt.start()] + rest[rt.end():]
    else:
        lt = LEAD_TERM_RE.match(rest)
        if lt:
            years = lt.group(1)
            rest = rest[lt.end():]
    tm = REGION_TAIL_RE.search(rest)
    if tm and region is None:
        region = tm.group(1)

    office = "School Director"
    if region:
        office += f" Region {region}"
    if years:
        office += f" ({years} Year)"
    return office, district


def tioga_map_header(header: str):
    h = header.strip()
    if h in EXACT_OFFICES:
        return EXACT_OFFICES[h]
    m = MDJ_RE.match(h)
    if m:
        return ("Magisterial District Judge", m.group(1))
    m = RETENTION_RE.match(h)
    if m and int(m.group(1)) in RETENTION_OFFICES:
        return (RETENTION_OFFICES[int(m.group(1))], "")
    m = SUP_RE.match(h)
    if m:
        return (f"Township Supervisor ({TERM_YEARS[m.group(1)]} Year)",
                _muni(m.group(2)))
    m = AUD_RE.match(h)
    if m:
        return (f"Township Auditor ({TERM_YEARS[m.group(1)]} Year)",
                _muni(m.group(2)))
    m = TAX_RE.match(h)
    if m:
        return ("Tax Collector", _muni(m.group(2)))
    m = MOC_RE.match(h)
    if m:
        return (f"Borough Council ({TERM_YEARS[m.group(2)]} Year)",
                _muni(m.group(3)))
    m = MOC_BARE_RE.match(h)
    if m:
        # Appears only inside the Wellsboro Borough Ward One section.
        return (f"Borough Council ({TERM_YEARS[m.group(2)]} Year)", "")
    m = MOC_GLUED_RE.match(h)
    if m:
        return (f"Borough Council ({TERM_YEARS[m.group(2)]} Year)",
                "Wellsboro Borough Ward Two")
    s = _school(h)
    if s:
        return s
    return None


def tioga_fill_district(office: str, precinct: str):
    """The bare "MOC 2/4YR" header (Wellsboro Borough Ward One's 4-year
    council contest; the source header carries no municipality) takes the
    section's municipality as district, as the 2025 file does."""
    if office.startswith("Borough Council") and precinct.startswith("Wellsboro"):
        return (office, precinct)
    return None


def _is_header(line: str) -> bool:
    """Side-effect-free header test for the page-break walk-back."""
    h = line.strip()
    return bool(
        h in EXACT_OFFICES
        or MDJ_RE.match(h)
        or RETENTION_RE.match(h)
        or SUP_RE.match(h)
        or AUD_RE.match(h)
        or TAX_RE.match(h)
        or MOC_RE.match(h)
        or MOC_BARE_RE.match(h)
        or MOC_GLUED_RE.match(h)
        or SCHOOL_RE.match(h)
    )


if __name__ == "__main__":
    run(COUNTY, tioga_map_header,
        page_header_key=PAGE_HEADER_KEY,
        header_skip=("STATISTICS",),
        page_filter=PAGE_FILTER,
        is_header=_is_header,
        fill_district=tioga_fill_district)