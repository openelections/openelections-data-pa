#!/usr/bin/env python3
"""
Parse Mifflin County PA 2023 General (Municipal) Election precinct results.

Source: Mifflin County Precinct Official Results 2023 General.pdf
(Electionware "Summary Results Report", 4 columns: TOTAL / Election Day /
Mail Votes / Provisional Votes). Parsed from the pdftotext -layout extract.

Usage:
    python parsers/pa_mifflin_general_2023_results_parser.py \
        <input.txt|input.pdf> <output.csv>

Uses the text-based shared engine in ``electionware_txt`` with the office
tables from ``pa_mifflin_general_2025_results_parser.py`` (mixed-case
headers, lowercase Nyr term tokens).

Mifflin-2023-specific quirks:
  - Retention headers use "RETAIN" ("Superior Court - Retain Jack
    Panella"), so retention_style="retain" (2025 used "retention").
  - "School Director - Mount Union Area School District Region I" puts the
    Region at the END (2025 has "School Director - Region I <District>").
  - Some precincts list two indistinguishable "School Director Mifflin
    County School District" contests (2023 has no 2yr/4yr term tokens);
    the second occurrence within a precinct gets a "(2)" office suffix in
    ballot order (DISAMBIGUATE_ALL, mirrored by validate_helper.py).
"""

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from electionware_precinct_np import (  # noqa: E402
    ElectionwareConfig,
    expand_muni_flexible,
    normalize_office,
    prettify_huntingdon_precinct,
)
from pa_mifflin_general_2025_results_parser import (  # noqa: E402
    EXACT_OFFICES,
    LOCAL_OFFICES,
    school_director as _school_director_2025,
)

_NP_CFG = ElectionwareConfig(
    county="Mifflin",
    skip_prefixes=(),
    county_header_suffix="MIFFLIN COUNTY, PENNSYLVANIA",
    exact_offices=EXACT_OFFICES,
    local_offices=LOCAL_OFFICES,
    local_office_orientation="prefix",
    retention_style="retain",
    municipality_normalizer=expand_muni_flexible,
    school_director_handler=_school_director_2025,
)


def prettify_precinct(name: str) -> str:
    """Expand 2023's abbreviated precinct names to the 2025 conventions
    ("ARMAGH-EAST" -> "Armagh Township-East", "BOROUGH OF KISTLER" ->
    "Kistler Borough").  Brown-Reedsville_Big Valley is special-cased to
    the 2025 segment order "Brown Township-Big Valley_Reedsville"."""
    s = prettify_huntingdon_precinct(name)
    if s == "Brown-Reedsville_Big Valley":
        return "Brown Township-Big Valley_Reedsville"
    if s.startswith("Borough Of "):
        return s[len("Borough Of "):] + " Borough"
    m = re.match(r"^(Lewistown)-(.+)$", s)
    if m:
        return f"{m.group(1)} Borough-{m.group(2)}"
    m = re.match(r"^(Armagh|Brown|Decatur|Derry|Granville)-(.+)$", s)
    if m:
        return f"{m.group(1)} Township-{m.group(2)}"
    return s

_MOUNT_UNION_RE = re.compile(
    r"^School Director\s*-\s*(.*?)\s+Region\s+([IVX0-9]+)$", re.IGNORECASE
)


_BOROUGHS = {
    "Burnham", "Juniata Terrace", "Kistler", "Lewistown", "McVeytown",
    "Newton Hamilton",
}
_TOWNSHIPS = {
    "Armagh", "Bratton", "Brown", "Decatur", "Derry", "Granville", "Menno",
    "Oliver", "Union", "Wayne",
}


def _expand_tail(district: str) -> str:
    """2023 headers abbreviate the municipality ("Borough Council Burnham");
    expand to the 2025 convention ("Burnham Borough")."""
    d = district.strip()
    if d in _BOROUGHS:
        return d + " Borough"
    if d in _TOWNSHIPS:
        return d + " Township"
    return d


def normalize(line: str):
    line = re.sub(r"\s+", " ", line).strip()
    m = _MOUNT_UNION_RE.match(line)
    if m:
        return (f"School Director Region {m.group(2).upper()}", m.group(1))
    office, district = normalize_office(line, _NP_CFG)
    if office in ("Township Supervisor", "Township Auditor", "Borough Council"):
        district = _expand_tail(district)
    return (office, district)


DISAMBIGUATE_ALL = True  # two unnamed "School Director Mifflin County" seats


def disambiguate(office: str, district: str, occurrence: int):
    if occurrence > 0:
        return (f"{office} ({occurrence + 1})", district)
    return (office, district)


from electionware_txt import TxtConfig  # noqa: E402

CONFIG = TxtConfig(
    county="Mifflin",
    normalize_office=normalize,
    prettify_precinct=prettify_precinct,
    disambiguate=disambiguate,
    extra_junk=(r"^TOTAL\b",),
)

if __name__ == "__main__":
    from electionware_txt import run_cli

    run_cli(CONFIG)