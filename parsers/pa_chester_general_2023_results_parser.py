#!/usr/bin/env python3
"""
Parser for Chester County, PA 2023 General Election precinct results.

Source: Chester County Precinct Results 2023 General.pdf (Electionware
"Summary Precinct Results Report", 230 precincts). Parsed from the
layout-preserving pdftotext extract via the shared text engine
``electionware_txt``; a PDF input is converted with pdftotext first.

Usage:
    python parsers/pa_chester_general_2023_results_parser.py \
        "<input.txt|input.pdf>" "<output.csv>"

Conventions follow the county's 2025 parser/file
(parsers/pa_chester_general_2025_results_parser.py):
  - Precinct labels "005 Atglen" -> leading 3-digit code stripped.
  - "Member of Council" -> "Borough Council"; "Auditor <muni>" ->
      "Township Auditor".
  - "Unexpired N Year Term" infix (e.g. "Auditor Unexpired 4 Year Term
      W Whiteland Township") -> office " (Unexpired N Year)" suffix,
      same as 2025.
  - "Township District Supervisor Tredyffrin 1st Dist" -> Township
      Supervisor | Tredyffrin 1st District (as the 2025 file prints
      "Tredyffrin 2nd District").
  - School director: "School Director [At Large ]<district>[ Region X]"
      (X digit or letter A/B/C); muni-first form like "West Chester
      Region 2" -> ("School Director Region 2", "West Chester").
  - Retentions: "Superior Court Judicial Retention Question - Jack
      Panella" -> "Superior Court Retention - Jack Panella" (periods
      dropped from names, as in the 2025 map); the two local Court of
      Common Pleas retentions (John L. Hall, Patrick Carmody) likewise.
  - Referenda "<jurisdiction>: <question>" split office/district:
      "Borough of Kennett Square: Library Tax Referendum" ->
      ("Library Tax Referendum", "Kennett Square Borough"), matching
      the 2025 "Police Service Tax Referendum | West Pikeland Township"
      treatment.
  - "Magisterial District Judge District 15-3-01" ->
      ("Magisterial District Judge", "15-3-01").
"""

import re
from typing import Optional

from electionware_txt import TxtConfig, run_cli


EXACT_OFFICES = {
    "Justice of the Supreme Court": ("Justice of the Supreme Court", ""),
    "Judge of the Superior Court": ("Judge of the Superior Court", ""),
    "Judge of the Commonwealth Court": ("Judge of the Commonwealth Court", ""),
    "Judge of the Court of Common Pleas": ("Judge of the Court of Common Pleas", ""),
    "County Commissioner": ("County Commissioner", ""),
    "District Attorney": ("District Attorney", ""),
    "Sheriff": ("Sheriff", ""),
    "Coroner": ("Coroner", ""),
    "Prothonotary": ("Prothonotary", ""),
    "Clerk of Courts": ("Clerk of Courts", ""),
    "Register of Wills": ("Register of Wills", ""),
    "Recorder of Deeds": ("Recorder of Deeds", ""),
}

RETENTION = {
    "Superior Court Judicial Retention Question - Jack Panella": (
        "Superior Court Retention - Jack Panella", ""),
    "Superior Court Judicial Retention Question - Victor P. Stabile": (
        "Superior Court Retention - Victor P Stabile", ""),
    "Court of Common Pleas Judicial Retention Question - John L. Hall": (
        "Court of Common Pleas Retention - John L Hall", ""),
    "Court of Common Pleas Judicial Retention Question - Patrick Carmody": (
        "Court of Common Pleas Retention - Patrick Carmody", ""),
}

REFERENDA = {
    "Borough of Kennett Square: Library Tax Referendum": (
        "Library Tax Referendum", "Kennett Square Borough"),
    "Honey Brook Township: Referendum for Additional Township Supervisors": (
        "Referendum for Additional Township Supervisors", "Honey Brook Township"),
    "Phoenixville Area School District: Occupational Tax Referendum": (
        "Occupational Tax Referendum", "Phoenixville Area School District"),
}

MDJ_RE = re.compile(r"^Magisterial District Judge\s+District\s+(\d[\d-]*)\s*$")

# "Township District Supervisor Tredyffrin 1st Dist"
DISTRICT_SUP_RE = re.compile(
    r"^Township District Supervisor\s+(.+?)\s+(\d+(?:st|nd|rd|th))\s+Dist\s*$")

SCHOOL_RE = re.compile(
    r"^School Director (At Large )?(.+?)(?: Region (\S+))?$")

UNEXPIRED_RE = re.compile(r"\s+Unexpired\s+(\d+)\s+Year\s+Term\b")

LOCAL_OFFICES = [
    ("Township Supervisor", "Township Supervisor"),
    ("Township Commissioner", "Township Commissioner"),
    ("Member of Council", "Borough Council"),
    ("Tax Collector", "Tax Collector"),
    ("Auditor", "Township Auditor"),
    ("Mayor", "Mayor"),
]


def _normalize_cleaned(cleaned: str) -> Optional[tuple]:
    """Normalize an "Unexpired N Year Term"-stripped line."""
    if cleaned in EXACT_OFFICES:
        return EXACT_OFFICES[cleaned]
    m = SCHOOL_RE.match(cleaned)
    if m:
        at_large, district, region = m.groups()
        office = "School Director"
        if at_large:
            office += " At Large"
        if region:
            office += f" Region {region}"
        return (office, district.strip())
    for prefix, norm in LOCAL_OFFICES:
        if cleaned == prefix:
            return (norm, "")
        if cleaned.startswith(prefix + " "):
            return (norm, cleaned[len(prefix) + 1:].strip())
    return None


def normalize_office(line: str):
    line = re.sub(r"\s+", " ", line).strip()

    if line in EXACT_OFFICES:
        return EXACT_OFFICES[line]
    if line in RETENTION:
        return RETENTION[line]
    if line in REFERENDA:
        return REFERENDA[line]

    m = MDJ_RE.match(line)
    if m:
        return ("Magisterial District Judge", m.group(1))

    m = DISTRICT_SUP_RE.match(line)
    if m:
        return ("Township Supervisor", f"{m.group(1)} {m.group(2)} District")

    if line.startswith("Township Supervisor At Large "):
        return ("Township Supervisor At Large",
                line[len("Township Supervisor At Large "):].strip())

    m = UNEXPIRED_RE.search(line)
    if m:
        years = m.group(1)
        cleaned = (line[: m.start()] + line[m.end():]).strip()
        cleaned = re.sub(r"\s{2,}", " ", cleaned)
        base = _normalize_cleaned(cleaned)
        if base is not None:
            return (f"{base[0]} (Unexpired {years} Year)", base[1])

    m = SCHOOL_RE.match(line)
    if m:
        at_large, district, region = m.groups()
        office = "School Director"
        if at_large:
            office += " At Large"
        if region:
            office += f" Region {region}"
        return (office, district.strip())

    for prefix, norm in LOCAL_OFFICES:
        if line == prefix:
            return (norm, "")
        if line.startswith(prefix + " "):
            return (norm, line[len(prefix) + 1:].strip())

    return (line, "")


PRECINCT_CODE_RE = re.compile(r"^\d{3}\s+")


def prettify_precinct(name: str) -> str:
    return PRECINCT_CODE_RE.sub("", name, count=1)


CONFIG = TxtConfig(
    county="Chester",
    normalize_office=normalize_office,
    prettify_precinct=prettify_precinct,
)


if __name__ == "__main__":
    run_cli(CONFIG)