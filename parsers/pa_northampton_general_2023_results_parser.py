#!/usr/bin/env python3
"""Northampton County 2023 general — county-level results from the official
"Summary Results" report (Electionware ESR).

Headers look like "Council 4yr Chapman", "Auditor 6yr Allen Twsp",
"School Director Region I - Northampton Area School District", etc.

Usage: pa_northampton_general_2023_results_parser.py <input.pdf|txt> <output.csv>
"""

import re

from pa_2023_summary_common import parse_esr, run_cli

# municipalities that are townships vs boroughs/cities in Northampton County
TOWNSHIPS = {"Allen", "Bethlehem", "Bushkill", "East Allen", "Forks",
             "Hanover", "Lehigh", "Lower Mt Bethel", "Lower Nazareth",
             "Lower Saucon", "Moore", "Palmer", "Plainfield",
             "Upper Mt Bethel", "Upper Nazareth", "Washington", "Williams"}
PLACES = {"Bangor", "Bath", "Bethlehem", "Chapman", "East Bangor", "Easton",
          "Freemansburg", "Glendon", "Hellertown", "Nazareth",
          "North Catasauqua", "Northampton", "Pen Argyl", "Portland",
          "Roseto", "Stockertown", "Tatamy", "Walnutport", "West Easton",
          "Wilson", "Wind Gap"}

CITY = {"Bethlehem", "Easton"}
FIXED = {
    "justice of the supreme court": "Justice of the Supreme Court",
    "judge of the superior court": "Judge of the Superior Court",
    "judge of the commonwealth court": "Judge of the Commonwealth Court",
    "judge of the court of common pleas": "Judge of the Court of Common Pleas",
    "district attorney": "District Attorney",
    "county controller": "County Controller",
}


def _place(name):
    """Municipality name -> "<Name> Township"/"<Name> Borough"/"<Name>"."""
    name = re.sub(r"\s+", " ", name).strip()
    name = re.sub(r"\s+Twsp$", "", name, flags=re.I)
    name = re.sub(r"\s+Boro$", "", name, flags=re.I)
    if name in TOWNSHIPS:
        return name + " Township"
    if name in CITY:
        return name
    if name in PLACES:
        return name + " Borough"
    return name + " Township"


def _wards(name):
    """"Nazareth 1st Ward" style: keep the ward, add the place kind."""
    name = name.strip()
    m = re.match(r"^(.*?)(\d(?:st|nd|rd|th) Ward)$", name, re.I)
    body = m.group(1).strip() if m else name
    return _place(body) + (" " + m.group(2) if m else "")


def map_contest(header):
    h = re.sub(r"\s+", " ", header.strip())

    m = re.match(r"^Superior Court\s*-?\s*Retain (.+)$", h, re.I)
    if m:
        return "Judge of the Superior Court Retention - " + m.group(1).strip(), ""
    m = re.match(r"^Court of Common Pleas\s*-?\s*Retain (.+)$", h, re.I)
    if m:
        return ("Judge of the Court of Common Pleas Retention - "
                + m.group(1).strip(), "")

    low = h.lower()
    if low in FIXED:
        return FIXED[low], ""

    m = re.match(r"^Magisterial District Judge District (.+)$", h, re.I)
    if m:
        return "Magisterial District Judge", "Magisterial District " + m.group(1)

    if h.startswith("Northampton County Home Rule Charter Amendment"):
        return h, ""
    if h.startswith("Lower Saucon Council Term Limit"):
        return "Lower Saucon Council Term Limit", "Lower Saucon Township"

    m = re.match(r"^County Council District (.+)$", h)
    if m:
        return "County Council", "District " + m.group(1)

    m = re.match(r"^City Council At-Large (.+)$", h)
    if m:
        return "City Council", m.group(1).strip() + " At Large"
    m = re.match(r"^City Controller (.+)$", h)
    if m:
        return "City Controller", m.group(1).strip()
    m = re.match(r"^Commissioner At-Large (.+)$", h)
    if m:
        return "Township Commissioner", _place(m.group(1)) + " At Large"
    m = re.match(r"^Commissioner (.+?) (\d(?:st|nd|rd|th) Ward)$", h)
    if m:
        return "Township Commissioner", _place(m.group(1)) + " " + m.group(2)
    m = re.match(r"^Controller (.+)$", h)
    if m:
        return "Controller", _place(m.group(1))
    m = re.match(r"^Treasurer (.+)$", h)
    if m:
        return "Treasurer", m.group(1).strip()  # City of Bethlehem
    m = re.match(r"^Tax Collector (.+)$", h)
    if m:
        return "Tax Collector", _place(m.group(1))

    m = re.match(r"^Council At-Large (.+)$", h)
    if m:
        return "Council", _place(m.group(1)) + " At Large"
    m = re.match(r"^Council(?: (\d)yr)? (.+)$", h, re.I)
    if m:
        office = "Council"
        if m.group(1):
            office += f" ({m.group(1)} Year)"
        return office, _wards(m.group(2))
    m = re.match(r"^Mayor(?: (\d)yr)? (.+)$", h, re.I)
    if m:
        office = "Mayor"
        if m.group(1):
            office += f" ({m.group(1)} Year)"
        return office, _place(m.group(2))

    m = re.match(r"^(Auditor|Supervisor) (\d)yr (.+)$", h, re.I)
    if m:
        office = ("Township Auditor" if m.group(1).lower() == "auditor"
                  else "Township Supervisor")
        office += f" ({m.group(2)} Year)"
        return office, _place(m.group(3))

    m = re.match(r"^School Director(?: (\d)yr)? At-Large (.+)$", h, re.I)
    if m:
        office = "School Director"
        if m.group(1):
            office += f" ({m.group(1)} Year)"
        return office, m.group(2).strip() + " At Large"
    m = re.match(r"^School Director(?: (\d)yr)? (.+)$", h, re.I)
    if m:
        office = "School Director"
        if m.group(1):
            office += f" ({m.group(1)} Year)"
        rest = re.sub(r"\s+", " ", m.group(2).strip())
        # "Region I - Northampton Area School District" ->
        #   "<district> Region I" ; keep source order otherwise
        m2 = re.match(r"^Region ([IVX]+)\s*-\s*(.+)$", rest)
        if m2:
            district = m2.group(2).strip()
            if re.search(r"school district$", district, re.I):
                return office, district + " Region " + m2.group(1)
            return office, district + " Region " + m2.group(1)
        if re.search(r"school director$", rest, re.I):
            rest = re.sub(r"\s*School Director$", " School District", rest,
                          flags=re.I)
        return office, rest

    return h, ""


if __name__ == "__main__":
    run_cli("Northampton", map_contest, parse_esr)