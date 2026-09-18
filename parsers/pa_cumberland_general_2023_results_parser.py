#!/usr/bin/env python3
"""
Parser for Cumberland County, PA 2023 General Election precinct results.

Source: Cumberland County Official Precinct Results 2023 General.pdf
(Electionware "RESULT BOOK - Precinct Report", 417 pages, 118 precincts).
Parsed from the layout-preserving pdftotext extract via the shared text
engine ``electionware_txt``; a PDF input is converted with pdftotext
first.

Usage:
    python parsers/pa_cumberland_general_2023_results_parser.py \
        "<input.txt|input.pdf>" "<output.csv>"

Cumberland 2023 header style is muni-first with comma-separated fields:

  - "<MUNI> TOWNSHIP SUPERVISOR, 6 YEAR" -> Township Supervisor (6 Year) |
      <Muni> Township; "<MUNI> TOWNSHIP COMMISSIONER, 4 YEAR" -> Township
      Commissioner (4 Year) | <Muni> Township; "<MUNI> AUDITOR, 6 YEAR" ->
      Township Auditor / Borough Auditor (by municipality) | <Muni>
      Township/Borough; "<MUNI> BOROUGH COUNCIL, 4 YEAR" -> Borough
      Council (4 Year) | <Muni> Borough; ward-split councils ("NEWVILLE
      BOROUGH COUNCIL, NORTH WARD, 4 YEAR") keep the ward in the
      district; "<MUNI> MAYOR, 2 YEAR" -> Mayor (2 Year) | <Muni>
      Borough; "<MUNI> TAX COLLECTOR, 2 YEAR" / "SHIPPENSBURG BOROUGH TAX
      COLLECTOR, 2 YEAR" -> Tax Collector (2 Year) | <Muni>
      Township/Borough.
  - School directors: "<DIST> SCHOOL DISTRICT, SCHOOL DIRECTOR[, REGION |
      DISTRICT | area], N YEAR" -> "School Director[ Region N | District
      N | <area>] (N Year)" with the district set to the school
      district's short name ("Big Spring", "West Shore", "Carlisle
      Area", ...).
  - "MAGISTERIAL DISTRICT JUDGE 09-1-02, 6 YEAR" -> Magisterial District
      Judge | 09-1-02 (the term suffix is dropped; district kept as
      printed).
  - Retentions are already named in the source: "SUPERIOR COURT
      RETENTION - JACK PANELLA" -> "Superior Court Retention - Jack
      Panella"; "SUPERIOR COURT RETENTION - VICTOR P STABILE" ->
      "Superior Court Retention - Victor P Stabile" (as printed, no
      period).
  - County offices: COUNTY COMMISSIONER -> County Commissioner; COUNTY
      TREASURER -> Treasurer; DISTRICT ATTORNEY -> District Attorney.
  - The "NF" party label (2 rows, "Lorelei Wilson Coplen", Dickinson
      Township Supervisor) is preserved as printed.
"""

import re

from electionware_txt import TxtConfig, run_cli
from electionware_precinct_np import title_case, prettify_huntingdon_precinct


EXACT_OFFICES = {
    "JUSTICE OF THE SUPREME COURT": ("Justice of the Supreme Court", ""),
    "JUDGE OF THE SUPERIOR COURT": ("Judge of the Superior Court", ""),
    "JUDGE OF THE COMMONWEALTH COURT": ("Judge of the Commonwealth Court", ""),
    "COUNTY COMMISSIONER": ("County Commissioner", ""),
    "COUNTY TREASURER": ("Treasurer", ""),
    "DISTRICT ATTORNEY": ("District Attorney", ""),
}

# The source names the two Superior Court retention questions directly.
RETENTION = {
    "SUPERIOR COURT RETENTION - JACK PANELLA": (
        "Superior Court Retention - Jack Panella", ""),
    "SUPERIOR COURT RETENTION - VICTOR P STABILE": (
        "Superior Court Retention - Victor P Stabile", ""),
}

# "MAGISTERIAL DISTRICT JUDGE 09-1-02, 6 YEAR"
MDJ_RE = re.compile(
    r"^MAGISTERIAL DISTRICT JUDGE\s+(\d[\d-]*)\s*,\s*\d+\s+YEAR\s*$")

# "<DIST> SCHOOL DISTRICT, SCHOOL DIRECTOR[, REGION N | DISTRICT X | area], N YEAR"
SCHOOL_RE = re.compile(
    r"^([A-Z ./'&-]+?)\s+SCHOOL DISTRICT\s*,\s*SCHOOL DIRECTOR"
    r"(?:\s*,\s*([^,]+?))?\s*,\s*(\d+)\s+YEAR\s*$")
REGION_AREA_RE = re.compile(r"^(REGION|DISTRICT)\s+(\S+)$")

# Comma-style local offices: "<MUNI> <OFFICE TOKEN>[, <ward/area>], N YEAR"
OFFICE_TOKENS = [
    # (token, office name, district kind)
    ("TOWNSHIP SUPERVISOR", "Township Supervisor", "township"),
    ("TOWNSHIP COMMISSIONER", "Township Commissioner", "township"),
    ("BOROUGH TAX COLLECTOR", "Tax Collector", "borough"),
    ("BOROUGH COUNCIL", "Borough Council", "borough"),
    ("TAX COLLECTOR", "Tax Collector", "auto"),
    ("AUDITOR", "Auditor", "auto"),
    ("MAYOR", "Mayor", "auto"),
]

# Municipalities appearing in Cumberland's unmarked (plain "AUDITOR" /
# "TAX COLLECTOR" / "MAYOR") headers.
TOWNSHIP_MUNIS = {
    "COOKE", "DICKINSON", "HOPEWELL", "LOWER FRANKFORD", "LOWER MIFFLIN",
    "MIDDLESEX", "MONROE", "NORTH MIDDLETON", "NORTH NEWTON", "PENN",
    "SILVER SPRING", "SOUTH MIDDLETON", "SOUTH NEWTON", "SOUTHAMPTON",
    "UPPER FRANKFORD", "UPPER MIFFLIN", "WEST PENNSBORO",
}
BOROUGH_MUNIS = {
    "MT. HOLLY SPRINGS", "NEW CUMBERLAND", "NEWBURG", "SHIPPENSBURG",
    "SHIREMANSTOWN",
}


def _slash_title(s: str) -> str:
    """title_case each slash-separated segment ("NORTH/SOUTH" ->
    "North/South")."""
    return "/".join(title_case(seg) for seg in s.split("/"))


def _with_term(office: str, years) -> str:
    return f"{office} ({years} Year)" if years else office


def _district(muni: str, kind: str) -> str:
    muni = muni.strip()
    if kind == "borough":
        return title_case(muni) + " Borough"
    if kind == "township":
        return title_case(muni) + " Township"
    # auto: classify by muni name
    if muni.upper() in TOWNSHIP_MUNIS:
        return title_case(muni) + " Township"
    if muni.upper() in BOROUGH_MUNIS:
        return title_case(muni) + " Borough"
    return title_case(muni)


def normalize_office(line: str):
    line = re.sub(r"\s+", " ", line).strip()

    if line in EXACT_OFFICES:
        return EXACT_OFFICES[line]
    if line in RETENTION:
        return RETENTION[line]

    m = MDJ_RE.match(line)
    if m:
        return ("Magisterial District Judge", m.group(1))

    m = SCHOOL_RE.match(line)
    if m:
        sd_name, area, years = m.groups()
        district = title_case(sd_name.strip())
        office = "School Director"
        if area:
            area = area.strip()
            rm = REGION_AREA_RE.match(area)
            if rm:
                office += f" {rm.group(1).title()} {rm.group(2).upper()}"
            else:
                office += f" {_slash_title(area)}"
        return (f"{office} ({years} Year)", district)

    if line.upper().endswith("YEAR") or re.search(r",\s*\d+\s+YEAR\s*$", line):
        parts = [p.strip() for p in line.split(",")]
        term_m = re.match(r"^(\d+)\s+YEAR$", parts[-1], re.IGNORECASE)
        if term_m:
            years = term_m.group(1)
            head = parts[0]
            # optional middle field ("NORTH WARD") appends to the district
            area = parts[1] if len(parts) == 3 else None
            for token, office, kind in OFFICE_TOKENS:
                if head.upper().endswith(" " + token):
                    muni = head[: -len(token) - 1]
                    district = _district(muni, kind)
                    if area:
                        district += f" {title_case(area.strip())}"
                    return (_with_term(office, years), district)

    return (line.title(), "")


CONFIG = TxtConfig(
    county="Cumberland",
    normalize_office=normalize_office,
    prettify_precinct=prettify_huntingdon_precinct,
    extra_parties=("NF",),
)


if __name__ == "__main__":
    run_cli(CONFIG)