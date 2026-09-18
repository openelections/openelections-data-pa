#!/usr/bin/env python3
"""
Parse Washington County PA 2023 General (Municipal) Election precinct results.

Source: Washington County Official Results Precinct Summary 2023 General.pdf
(Electionware "Precinct Summary Results Report", vertical layout: 4 columns
TOTAL / Election Day / Mail In / Provisional; Ballots Cast values sit on the
line ABOVE their label; each precinct's first page repeats the precinct name
at the top of every continuation page). Parsed from the pdftotext -layout
extract.

Usage:
    python parsers/pa_washington_general_2023_results_parser.py \
        <input.txt|input.pdf> <output.csv>

Uses the text-based shared engine in ``electionware_txt`` with office
conventions from ``pa_washington_general_2025_results_parser.py``.

Washington-2023-specific quirks (vs the 2025 wrapper):
  - Judicial retention questions are printed as "SUPERIOR COURT - PANELLA" /
    "SUPERIOR COURT - STABILE" and local "COMMON PLEAS - LUCAS" /
    "COMMON PLEAS - COSTANZO" with plain Yes/No candidate rows; recorded as
    "<Court> Retention - <Surname>" with candidates "Yes"/"No" (surname
    convention matches the county's 2025 file, e.g. "Superior Court
    Retention - Dubow").
  - Local offices carry seat/term tokens after a dash ("SUPERVISOR - 1 FOR
    6 YEARS AMWELL", "AUDITOR - 2 YEARS BLAINE", "BOROUGH COUNCIL - 1 FOR
    2 YEARS CHARLEROI", "COUNCIL AT LARGE - 2 FOR 4 YEARS PETERS",
    "MAYOR - 2 YEARS BEALLSVILLE", "TAX COLLECTOR - 1 FOR 2 YEARS TWP
    CECIL"). Years are kept in the office name where the county's 2025
    file also distinguishes them (Township Auditor, Borough/City Council
    2-year seats) and for Township Supervisor (Union Township elected
    supervisors to 6/4/2-year terms in 2023); term-only suffixes are
    dropped where 2025 uses the plain name (Mayor, Tax Collector,
    4-year Borough Council).
  - School director headers come in three forms:
      "<DIST> SCHOOL DISTRICT SCHOOL DIRECTOR REGION I/II/III [- N YEARS]"
      "<DIST> SCHOOL DISTRICT SCHOOL DIRECTOR <geography>"  (Canon McMillan)
      "SCHOOL DIRECTOR - N FOR M YEARS <DIST> SCHOOL DISTRICT"
      "SCHOOL DIRECTOR AT LARGE - RINGGOLD SCHOOL DISTRICT RINGGOLD SCHOOL"
  - "MAGISTERIAL DISTRICT JUDGE MAGISTERIAL DISTRICT 27-1-02" (doubled
    prefix) -> Magisterial District Judge | 27-1-02.
  - "CBG" abbreviates Canonsburg in Borough Council ward headers.
  - Four candidate rows print with a stray leading "FOR " token
    ("FOR MICHAEL FISHER" for Fallowfield Township supervisor); a
    preprocessor strips it so the candidate is recorded party-less.
"""

import csv
import re
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from electionware_precinct_np import (  # noqa: E402
    ElectionwareConfig,
    normalize_office,
    title_case,
)
from pa_washington_general_2025_results_parser import (  # noqa: E402
    EXACT_OFFICES as _EXACT_2025,
    LOCAL_OFFICES as _LOCAL_2025,
    washington_city,
    washington_mdj,
)

# ---------------------------------------------------------------------------
# Exact-match offices.
# ---------------------------------------------------------------------------

RETENTIONS = {
    "SUPERIOR COURT - PANELLA": ("Superior Court Retention - Panella", ""),
    "SUPERIOR COURT - STABILE": ("Superior Court Retention - Stabile", ""),
    "COMMON PLEAS - LUCAS": ("Court of Common Pleas Retention - Lucas", ""),
    "COMMON PLEAS - COSTANZO": ("Court of Common Pleas Retention - Costanzo", ""),
}

_EXACT_2023 = dict(_EXACT_2025)
_EXACT_2023.update(
    {
        "JUSTICE OF THE SUPREME COURT": ("Justice of the Supreme Court", ""),
        "REGISTER OF WILLS & CLERK OF ORPHANS' COURT": (
            "Register of Wills & Clerk of Orphans' Court",
            "",
        ),
        "CLERK OF COURTS": ("Clerk of Courts", ""),
        "PROTHONOTARY": ("Prothonotary", ""),
        "COUNTY TREASURER": ("County Treasurer", ""),
        "COUNTY COMMISSIONER": ("County Commissioner", ""),
        "CORONER": ("Coroner", ""),
    }
)

_NP_CFG = ElectionwareConfig(
    county="Washington",
    skip_prefixes=(),
    county_header_suffix="Washington",
    exact_offices=_EXACT_2023,
    local_offices=_LOCAL_2025,
    local_office_orientation="prefix",
    retention_style="retention",
    municipality_normalizer=title_case,
    extra_office_handlers=[washington_city, washington_mdj],
    include_common_pleas=False,
)

# ---------------------------------------------------------------------------
# Local offices with seat/term tokens.
# ---------------------------------------------------------------------------

_TERM_RE = re.compile(
    r"^(AUDITOR|SUPERVISOR|BOROUGH COUNCIL AT LARGE|BOROUGH COUNCIL"
    r"|CITY COUNCIL|COUNCIL AT LARGE|MAYOR|TAX COLLECTOR)"
    r"\s*-\s*(?:(\d+)\s+FOR\s+)?(\d+)\s+YEARS?\s+(.+)$",
    re.IGNORECASE,
)
_CBG_RE = re.compile(r"\bCBG\b", re.IGNORECASE)


def _muni(tail: str) -> str:
    tail = re.sub(r"^TWP\s+", "", tail.strip(), flags=re.IGNORECASE)
    return title_case(tail)


def _council_tail(tail: str) -> str:
    return title_case(_CBG_RE.sub("CANONSBURG", tail.strip()))


def _term_office(line: str):
    m = _TERM_RE.match(line)
    if not m:
        # Dash without a term ("BOROUGH COUNCIL - CENTERVILLE FIRST WARD")
        m2 = re.match(r"^BOROUGH COUNCIL\s*-\s*(.+)$", line, re.IGNORECASE)
        if m2:
            return ("Borough Council", _council_tail(m2.group(1)))
        return None
    prefix, _seats, years, tail = m.groups()
    years = int(years)
    if prefix.upper() == "AUDITOR":
        return ("Township Auditor (%d Year)" % years, _muni(tail))
    if prefix.upper() == "SUPERVISOR":
        return ("Township Supervisor (%d Year)" % years, _muni(tail))
    if prefix.upper() in ("BOROUGH COUNCIL AT LARGE", "COUNCIL AT LARGE"):
        return ("Borough Council At Large", _muni(tail))
    if prefix.upper() == "BOROUGH COUNCIL":
        office = "Borough Council (2 Year)" if years == 2 else "Borough Council"
        return (office, _council_tail(tail))
    if prefix.upper() == "CITY COUNCIL":
        office = "City Council (2 Year)" if years == 2 else "City Council"
        return (office, title_case(tail))
    if prefix.upper() == "MAYOR":
        return ("Mayor", _muni(tail))
    if prefix.upper() == "TAX COLLECTOR":
        return ("Tax Collector", _muni(tail))
    return None


# ---------------------------------------------------------------------------
# School director (three 2023 forms).
# ---------------------------------------------------------------------------

# "SCHOOL DIRECTOR [- AT LARGE] - N FOR M YEARS <DIST> SCHOOL DISTRICT"
_SCHOOL_PREFIX_RE = re.compile(
    r"^SCHOOL DIRECTOR\s+(AT\s+LARGE\s+)?-\s+(?:(\d+)\s+FOR\s+)?(\d+)\s+YEARS?\s+"
    r"(.+?)\s+SCHOOL DISTRICT$",
    re.IGNORECASE,
)
# "<DIST> SCHOOL DISTRICT SCHOOL DIRECTOR [- N YEARS] <geography>"
_SCHOOL_SUFFIX_RE = re.compile(
    r"^(.+?)\s+SCHOOL DISTRICT\s+SCHOOL DIRECTOR\s*(?:-\s*(\d+)\s*YEARS?)?\s+(.+)$",
    re.IGNORECASE,
)
_REGION_RE = re.compile(r"^REGION\s+(\S+)$", re.IGNORECASE)


def school_director_2023(line: str):
    m = _SCHOOL_PREFIX_RE.match(line)
    if m:
        at_large, _seats, years, dist = m.groups()
        office = "School Director"
        if at_large:
            office += " At Large"
        if int(years) == 2:
            office += " (2 Year)"
        return (office, title_case(dist.strip()))
    m = _SCHOOL_SUFFIX_RE.match(line)
    if m:
        dist, years, geo = m.groups()
        district = title_case(dist.strip())
        rm = _REGION_RE.match(geo.strip())
        if rm:
            office = f"School Director Region {rm.group(1).upper()}"
        else:
            office = "School Director " + title_case(geo.strip())
        if years:
            office += f" ({int(years)} Year)"
        return (office, district)
    return None


# "SCHOOL DIRECTOR AT LARGE - RINGGOLD SCHOOL DISTRICT RINGGOLD SCHOOL"
# is garbled (the district name repeats in truncated form; the county
# summary repeats it in full: "... RINGGOLD SCHOOL DISTRICT"). Map any
# "SCHOOL DIRECTOR AT LARGE - RINGGOLD ..." header to the 2025 form.
_SCHOOL_AT_LARGE_RE = re.compile(
    r"^SCHOOL DIRECTOR AT LARGE\s*-\s*RINGGOLD\b", re.IGNORECASE
)
SCHOOL_SPECIAL = {
    "SCHOOL DIRECTOR AT LARGE - RINGGOLD SCHOOL DISTRICT RINGGOLD SCHOOL": (
        "School Director At Large",
        "Ringgold",
    ),
}


def normalize(line: str):
    line = re.sub(r"\s+", " ", line).strip()
    if line.upper() in RETENTIONS:
        return RETENTIONS[line.upper()]
    if _SCHOOL_AT_LARGE_RE.match(line):
        return SCHOOL_SPECIAL[
            "SCHOOL DIRECTOR AT LARGE - RINGGOLD SCHOOL DISTRICT RINGGOLD SCHOOL"
        ]
    if line.upper() in _EXACT_2023:
        return _EXACT_2023[line.upper()]
    sd = school_director_2023(line)
    if sd:
        return sd
    t = _term_office(line)
    if t:
        return t
    m = re.match(r"^MAYOR\s*-\s*CITY\s+(.+)$", line, re.IGNORECASE)
    if m:
        return ("Mayor", title_case(m.group(1).strip()))
    m = re.match(r"^CITY TREASURER\s+(.+)$", line, re.IGNORECASE)
    if m:
        return ("City Treasurer", title_case(m.group(1).strip()))
    # First-class township commissioner (East Bethlehem is Washington
    # County's only first-class township). 2025 headers name the township
    # ("COMMISSIONER EAST BETHLEHEM SECOND WARD" -> "East Bethlehem Ward
    # 2"); 2023 prints "TOWNSHIP COMMISSIONER FIRST WARD" bare.
    m = re.match(r"^TOWNSHIP COMMISSIONER\s+AT\s+LARGE\s+(.+)$", line, re.IGNORECASE)
    if m:
        return ("Township Commissioner At Large", title_case(m.group(1).strip()))
    m = re.match(
        r"^TOWNSHIP COMMISSIONER\s+(FIRST|SECOND|THIRD|FOURTH|FIFTH|SIXTH)\s+WARD$",
        line,
        re.IGNORECASE,
    )
    if m:
        ward = {"FIRST": "1", "SECOND": "2", "THIRD": "3", "FOURTH": "4",
                "FIFTH": "5", "SIXTH": "6"}[m.group(1).upper()]
        return ("Township Commissioner", f"East Bethlehem Ward {ward}")
    return normalize_office(line, _NP_CFG)


def disambiguate(office: str, district: str, occurrence: int):
    if occurrence > 0:
        return (f"{office} ({occurrence + 1})", district)
    return (office, district)


# ---------------------------------------------------------------------------
# Precinct names: 2023 prints "CANONSBURG 1 W 3"; the county's 2025 file
# uses "Canonsburg 1w 3" (ward "W" attached to the precinct number, and
# Peters' ward letter A lower-cased).
# ---------------------------------------------------------------------------


def prettify_precinct(name: str) -> str:
    out = []
    for t in name.split():
        up = t.upper()
        if up == "MT":
            out.append("Mt")
        elif t.isdigit():
            out.append(t)
        elif up == "W" and out and out[-1].isdigit():
            out[-1] += "w"
        elif len(t) == 1 and t.isalpha():
            out.append(t.lower())
        else:
            out.append(t.capitalize())
    return " ".join(out)


_NUM_RE = re.compile(r"^\d[\d,]*$")
_FOR_ROW_RE = re.compile(r"^\s*FOR\s+\S.*?\s+\d[\d,]*(\s+\d[\d,]*){3}\s*$")


def preprocess_lines(lines: list[str]) -> list[str]:
    """Strip the stray leading "FOR " token from candidate rows
    ("FOR MICHAEL FISHER  128 105 23 0" -> "MICHAEL FISHER  ...")."""
    out = []
    for ln in lines:
        if _FOR_ROW_RE.match(ln):
            ln = re.sub(r"^(\s*)FOR\s+", r"\1", ln, count=1)
        out.append(ln)
    return out


from electionware_txt import (  # noqa: E402
    FIELDNAMES,
    TxtConfig,
    parse_input,
)

CONFIG = TxtConfig(
    county="Washington",
    normalize_office=normalize,
    prettify_precinct=prettify_precinct,
    disambiguate=disambiguate,
    # "MICHAEL FISHER" (Fallowfield supervisor, after the "FOR " strip)
    # prints without a party code.
    party_optional=True,
)


def main(argv):
    if len(argv) != 3:
        sys.exit(f"Usage: {Path(argv[0]).name} <input.txt|input.pdf> <output.csv>")
    inp = Path(argv[1])
    if inp.suffix.lower() == ".pdf":
        rows, n, warnings, unnamed = parse_input(str(inp), CONFIG)
    else:
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as tf:
            tmp = Path(tf.name)
            tmp.write_text(
                "\n".join(preprocess_lines(inp.read_text(errors="replace").split("\n"))),
                encoding="utf-8",
            )
        try:
            rows, n, warnings, unnamed = parse_input(str(tmp), CONFIG)
        finally:
            tmp.unlink(missing_ok=True)
    out_path = Path(argv[2])
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDNAMES)
        w.writeheader()
        w.writerows(rows)
    print(f"Wrote {len(rows)} rows across {n} precincts to {out_path}")
    if unnamed:
        print(f"Skipped {unnamed} unnamed (county-summary) Statistics segment(s)")
    if warnings:
        print(f"WARNING: {len(warnings)} unmatched lines:")
        for wmsg in warnings[:40]:
            print("  " + wmsg)
        if len(warnings) > 40:
            print("  ...")


if __name__ == "__main__":
    main(sys.argv)