#!/usr/bin/env python3
"""
Parse Blair County PA 2023 General (Municipal) Election precinct results.

Source: Blair County Precinct Summary Results 2023 General.pdf (Electionware
"Precinct Summary Results Report", 5 columns: TOTAL / VOTE % / Election Day /
Mail Votes / Provisional Votes). Parsed from the pdftotext -layout extract;
the shared text engine strips the VOTE % column automatically.

Usage:
    python parsers/pa_blair_general_2023_results_parser.py \
        <input.txt|input.pdf> <output.csv>

Uses the text-based shared engine in ``electionware_txt`` with the office
tables from ``pa_blair_general_2025_results_parser.py``.

Blair-2023-specific quirks (vs the 2025 wrapper):
  - Retention headers use "RETAIN" and are ALL CAPS ("SUPERIOR COURT -
    RETAIN VICTOR P STABILE", "COURT OF COMMON PLEAS - RETAIN WADE A
    KAGARISE"); normalized to "<Court> Retention - <Title-cased Name>".
  - School Director headers put the district name FIRST:
      "ALTOONA SCHOOL DIRECTOR"                -> School Director | Altoona Area
      "TYRONE SCHOOL DIRECTOR 4YR"             -> School Director (4 Year) | Tyrone Area
      "CLAYSBURG KIMMEL SCHOOL DIRECTOR (GREENFIELD TWP 2&3) 4YR"
                                               -> School Director (4 Year) | Claysburg Kimmel - Greenfield 2-3
      "WILLIAMSBURG COMMUNITY SCHOOL DIRECTOR (WOODBURY TWP)"
                                               -> School Director | Williamsburg Community - Woodbury Township
    (The source misspells "HOLLIDAYSURG"; normalized to Hollidaysburg Area.)
  - "COMMISSIONER" (not "COUNTY COMMISSIONER") and "REGISTER OF WILLS &
    RECORDER OF DEEDS" header spellings.
  - "JUSTICE OF THE SUPREME COURT" (2023 partisan race; absent from the
    2025 table).
  - Precinct names are already title-cased and identical to the 2025 file
    ("Frankstown Twp, District 1"); no prettifier needed.
"""

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from electionware_precinct_np import (  # noqa: E402
    ElectionwareConfig,
    expand_muni_flexible,
    normalize_office,
    simple_capitalize,
)
from pa_blair_general_2025_results_parser import (  # noqa: E402
    EXACT_OFFICES as _EXACT_2025,
    LOCAL_OFFICES as _LOCAL_2025,
    blair_overrides,
)

_EXACT_2023 = dict(_EXACT_2025)
_EXACT_2023.update(
    {
        "JUSTICE OF THE SUPREME COURT": ("Justice of the Supreme Court", ""),
        "REGISTER OF WILLS & RECORDER OF DEEDS": ("Register and Recorder", ""),
        "COMMISSIONER": ("County Commissioner", ""),
        "CONTROLLER": ("Controller", ""),
    }
)

_NP_CFG = ElectionwareConfig(
    county="Blair",
    skip_prefixes=(),
    county_header_suffix="BLAIR COUNTY, PENNSYLVANIA OFFICIAL RESULTS",
    exact_offices=_EXACT_2023,
    local_offices=_LOCAL_2025,
    local_office_orientation="prefix",
    retention_style="retain",
    extra_office_handlers=[blair_overrides],
    municipality_normalizer=expand_muni_flexible,
)

_RETAIN_RE = re.compile(
    r"^(SUPERIOR COURT|COURT OF COMMON PLEAS)\s*-\s*RETAIN\s+(.+)$",
    re.IGNORECASE,
)

# 2023 puts the school-district name BEFORE "SCHOOL DIRECTOR".
_SCHOOL_BASES = {
    "ALTOONA": "Altoona Area",
    "HOLLIDAYSURG": "Hollidaysburg Area",
    "SPRING COVE": "Spring Cove",
    "TYRONE": "Tyrone Area",
    "BELLWOOD ANTIS": "Bellwood Antis",
    "CLAYSBURG KIMMEL": "Claysburg Kimmel",
    "PENN CAMBRIA": "Penn Cambria",
    "WILLIAMSBURG COMMUNITY": "Williamsburg Community",
}


def school_director_2023(line: str):
    m = re.match(r"^(.+?)\s+SCHOOL DIRECTOR\s*(.*)$", line)
    if not m:
        return None
    core = m.group(1).strip()
    rest = m.group(2).strip()
    years = None
    sub = None
    pm = re.match(r"\((.+?)\)\s*(.*)$", rest)
    if pm:
        sub, rest = pm.group(1).strip(), pm.group(2).strip()
    ym = re.match(r"(\d)\s*YR$", rest)
    if ym:
        years = ym.group(1)
        rest = ""
    if rest:  # unrecognized tail: not one of our headers
        return None
    base = _SCHOOL_BASES.get(core.upper(), simple_capitalize(core))
    district = base
    if sub:
        su = sub.upper()
        if su == "GREENFIELD TWP 2&3":
            subn = "Greenfield 2-3"
        elif su == "GREENFIELD TWP 1":
            subn = "Greenfield 1"
        else:
            subn = expand_muni_flexible(sub)
        district = f"{base} - {subn}"
    office = "School Director"
    if years:
        office += f" ({years} Year)"
    return (office, district)


def normalize(line: str):
    line = re.sub(r"\s+", " ", line).strip()
    sd = school_director_2023(line)
    if sd:
        return sd
    m = _RETAIN_RE.match(line)
    if m:
        court = "Superior Court" if m.group(1).upper() == "SUPERIOR COURT" \
            else "Court of Common Pleas"
        return (f"{court} Retention - {simple_capitalize(m.group(2).strip())}", "")
    return normalize_office(line, _NP_CFG)


from electionware_txt import TxtConfig  # noqa: E402

CONFIG = TxtConfig(
    county="Blair",
    normalize_office=normalize,
    prettify_precinct=lambda s: s,
)

if __name__ == "__main__":
    from electionware_txt import run_cli

    run_cli(CONFIG)