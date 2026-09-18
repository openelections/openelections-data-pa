#!/usr/bin/env python3
"""Luzerne County 2023 general — county-level results from the official
"Election Summary Report" (Dominion ESR2).

Headers look like "Borough Council Ashley Boro (4 Year Term) (Vote for 3)",
"Auditor Bear Creek Twp (Vote for 1)",
"Jack Panella Superior Court Retention (Vote for 1)".

Usage: pa_luzerne_general_2023_results_parser.py <input.pdf|txt> <output.csv>
"""

import re

from pa_2023_summary_common import parse_esr2, run_cli


def _muni(name):
    name = re.sub(r"\s+", " ", name).strip()
    ward = ""
    m = re.match(r"^(.*?)\s+Ward\s+(\d+)$", name, re.I)
    if m:
        name = m.group(1).strip()
        ward = f" Ward {m.group(2)}"
    name = re.sub(r"\s+Boro(r)?$", "", name, flags=re.I)
    if ward:
        return name + ward
    if name.endswith(" Twp"):
        return name[: -len(" Twp")] + " Township"
    if name.endswith(" City"):
        return name[: -len(" City")] + " City"
    return name


def map_contest(header):
    h = re.sub(r"\s+", " ", header.strip())
    h = re.sub(r"\s*\(Vote for \d+\)\s*$", "", h).strip()
    term = None
    m = re.search(r"\((\d) Year Term\)", h, re.I)
    if m:
        term = m.group(1)
        h = (h[:m.start()] + " " + h[m.end():]).strip()
    h = re.sub(r"\s+", " ", h).strip()

    m = re.match(r"^(Jack Panella|Victor P Stabile) Superior Court Retention$",
                 h, re.I)
    if m:
        who = ("Jack Panella" if m.group(1).lower().startswith("jack")
               else "Victor P. Stabile")
        return "Judge of the Superior Court Retention - " + who, ""

    m = re.match(r"^Magisterial District Judge (\d{2}-\d-\d{2})$", h, re.I)
    if m:
        return "Magisterial District Judge", "Magisterial District " + m.group(1)

    office = None
    dist = ""
    m = re.match(r"^Township Supervisor (.+)$", h, re.I)
    if m:
        office, dist = "Township Supervisor", _muni(m.group(1))
    m = re.match(r"^Auditor (.+)$", h, re.I)
    if m and office is None:
        office, dist = "Township Auditor", _muni(m.group(1))
    m = re.match(r"^Tax Collector (.+)$", h, re.I)
    if m and office is None:
        office, dist = "Tax Collector", _muni(m.group(1))
    m = re.match(r"^Mayor (.+)$", h, re.I)
    if m and office is None:
        office, dist = "Mayor", _muni(m.group(1))
    m = re.match(r"^Borough Council (.+)$", h, re.I)
    if m and office is None:
        office, dist = "Borough Council", _muni(m.group(1))
    m = re.match(r"^Township Council (.+)$", h, re.I)
    if m and office is None:
        office, dist = "Township Council", _muni(m.group(1))
    m = re.match(r"^Township Commissioner (.+)$", h, re.I)
    if m and office is None:
        office, dist = "Township Commissioner", _muni(m.group(1))
    m = re.match(r"^City Council (.+)$", h, re.I)
    if m and office is None:
        office, dist = "City Council", _muni(m.group(1))
    m = re.match(r"^City Controller (.+)$", h, re.I)
    if m and office is None:
        office, dist = "City Controller", _muni(m.group(1))
    m = re.match(r"^City Treasurer (.+)$", h, re.I)
    if m and office is None:
        office, dist = "City Treasurer", _muni(m.group(1))
    m = re.match(r"^(.+?) School Director(?: Region (\d+))?$", h, re.I)
    if m and office is None:
        name = m.group(1).strip()
        if not re.search(r"(Area|Valley|Region)$", name):
            name += " School District"
        dist = name + (f" Region {m.group(2)}" if m.group(2) else "")
        office = "School Director"

    if office is None:
        fixed = {
            "County Council": ("County Council", ""),
            "District Attorney": ("District Attorney", ""),
            "Justice of the Supreme Court": ("Justice of the Supreme Court", ""),
            "Judge of the Superior Court": ("Judge of the Superior Court", ""),
            "Judge of the Commonwealth Court":
                ("Judge of the Commonwealth Court", ""),
            "Jenkins Township Proposition":
                ("Jenkins Township Proposition", "Jenkins Township"),
        }.get(h)
        if fixed:
            office, dist = fixed
        else:
            m = re.match(r"^(Nanticoke Home Rule Charter Amendment \d+)",
                         h, re.I)
            if m:
                office, dist = m.group(1), ""
            else:
                office, dist = h, ""

    if term:
        office += f" ({term} Year)"
    return office, dist


if __name__ == "__main__":
    run_cli("Luzerne", map_contest, parse_esr2)