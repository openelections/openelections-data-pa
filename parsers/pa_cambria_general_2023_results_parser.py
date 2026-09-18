#!/usr/bin/env python3
"""Cambria County 2023 general — county-level output from the official
"Official Results Municipal Election" certified winners table.

IMPORTANT: this source is a certified WINNERS list only (JURISDICTION | TERM |
OFFICE | NAME).  It contains NO vote counts anywhere, so the output carries
the winning candidate rows with EMPTY votes/party columns.  No values are
fabricated; see work2023/validate/cambria.md.

Usage: pa_cambria_general_2023_results_parser.py <input.pdf|txt> <output.csv>
"""

import re
import sys

from pa_2023_summary_common import (Rows, write_csv, smart_title,
                                   clean_name)

ROW_RE = re.compile(r"^(\S.*?)\s\s+(\d)\s*YEAR TERM\s\s+(.+)$")

MUNI_OFFICES = {
    "COUNCIL": "Borough Council",
    "COUNCIL - FIRST WARD": "Borough Council Ward 1",
    "COUNCIL - SECOND WARD": "Borough Council Ward 2",
    "SUPERVISOR": "Township Supervisor",
    "AUDITOR": "Municipal Auditor",
    "COMMISSIONER AT LARGE": "Township Commissioner At Large",
    "TAX COLLECTOR": "Tax Collector",
    "MAYOR": "Mayor",
    "CONSTABLE": "Constable",
}


def _muni_office(office, jur_kind):
    """Map a raw table office to its township/borough/city form."""
    office = MUNI_OFFICES.get(office, office.title())
    if office == "Borough Council":
        if jur_kind == "city":
            return "City Council"
        if jur_kind == "township":
            return "Township Council"
        return "Borough Council"
    if office == "Municipal Auditor":
        return {"township": "Township Auditor",
                "borough": "Borough Auditor"}.get(jur_kind, office)
    if office == "Township Commissioner At Large" and jur_kind != "township":
        return "Commissioner At Large"
    if office in ("Township Supervisor", "Township Auditor",
                  "Township Council", "Township Auditor") \
            and jur_kind != "township":
        office = office.replace("Township", "Borough")
    return office


def parse_row(line):
    line = line.lstrip("\x0c")
    m = ROW_RE.match(line)
    if not m:
        return None
    jur = re.sub(r"\s+", " ", m.group(1)).strip()
    term = m.group(2)
    rest = m.group(3)
    office = name = None
    for cand in ("MAGISTERIAL DISTRICT JUDGE", "SCHOOL DIRECTOR",
                 "COMMISSIONER AT LARGE", "TAX COLLECTOR",
                 "DISTRICT ATTORNEY", "COMMISSIONER",
                 "PROTHONOTARY", "CLERK OF COURTS", "REGISTER OF WILLS",
                 "RECORDER OF DEEDS", "TREASURER", "CORONER", "CONTROLLER",
                 "SUPERVISOR", "COUNCIL - SECOND WARD", "COUNCIL - FIRST WARD",
                 "COUNCIL", "AUDITOR", "MAYOR", "CONSTABLE"):
        m2 = re.match(r"^" + re.escape(cand) + r"\s\s+(.+)$", rest)
        if m2:
            office, name = cand, re.sub(r"\s+", " ", m2.group(1)).strip()
            break
    if office is None:
        return None

    if jur.upper() == "CAMBRIA COUNTY":
        county_offices = {
            "COMMISSIONER": "County Commissioner",
            "DISTRICT ATTORNEY": "District Attorney",
            "PROTHONOTARY": "Prothonotary",
            "CONTROLLER": "Controller",
            "CLERK OF COURTS": "Clerk of Courts",
            "REGISTER OF WILLS": "Register of Wills",
            "RECORDER OF DEEDS": "Recorder of Deeds",
            "TREASURER": "Treasurer",
            "CORONER": "Coroner",
        }
        office = county_offices.get(office, office.title())
        district = ""
    elif jur.upper().startswith("MAGISTERIAL DISTRICT "):
        district = jur.title()
        office = "Magisterial District Judge"
    elif jur.upper().endswith("SCHOOL DISTRICT"):
        district = jur.title()
        office = "School Director"
    elif jur.upper().startswith("CITY OF "):
        district = jur[len("CITY OF "):].strip().title() + " City"
        office = _muni_office(office, "city")
    elif jur.upper().endswith(" BOROUGH"):
        district = jur.title()
        office = _muni_office(office, "borough")
    elif jur.upper().endswith(" TOWNSHIP") or jur.upper() == "WEST TAYLOR":
        district = ("West Taylor Township" if jur.upper() == "WEST TAYLOR"
                    else jur.title())
        if office == "COMMISSIONER":
            office = "Township Commissioner"
        else:
            office = _muni_office(office, "township")
    else:
        district = jur.title()
        office = _muni_office(office, "borough")

    return office, district, term, name


def parse(text_path, county):
    with open(text_path, encoding="utf-8") as fh:
        lines = [l.rstrip() for l in fh]

    R = Rows(county)
    for line in lines:
        if not line.strip():
            continue
        if line.strip().upper().startswith("JURISDICTION"):
            continue
        if "OFFICIAL RESULTS" in line.upper() or \
                "November" in line:
            continue
        p = parse_row(line)
        if p is None:
            continue
        office, district, term, name = p
        office += f" ({term} Year)"
        R.add(office, district, "", clean_name(name, True), "")
        R.contests.append((office, district))
    return R


if __name__ == "__main__":
    args = sys.argv[1:]
    if len(args) < 2:
        raise SystemExit("Usage: pa_cambria_general_2023_results_parser.py "
                         "<input> <output>")
    R = parse(args[0], "Cambria")
    n = write_csv(args[1], R)
    print(f"wrote {n} rows -> {args[1]}")
    print(f"contests parsed: {len(set(R.contests))}")
    print("NOTE: source is a certified winners table with no vote counts;"
          " votes are left empty")