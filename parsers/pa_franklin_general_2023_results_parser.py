#!/usr/bin/env python3
"""Franklin County 2023 general — county-level results from the official
"Official Summary Results" (Electionware ESR).

Headers use ALL-CAPS "<OFFICE> [N]YR <municipality>" conventions.

Usage: pa_franklin_general_2023_results_parser.py <input.pdf|txt> <output.csv>
"""

import re

from pa_2023_summary_common import parse_esr, run_cli, smart_title

TOWNSHIPS = {"Antrim", "Fannett", "Green", "Guilford", "Hamilton",
             "Letterkenny", "Lurgan", "Metal", "Montgomery", "Peters",
             "Quincy", "Southampton", "St. Thomas", "Warren", "Washington"}
BOROUGHS = {"Chambersburg", "Greencastle", "Mercerburg", "Mont Alto",
            "Orrstown", "Waynesboro", "West End Shippenburg"}

ORDINAL = re.compile(r"^(.*) (\d(?:st|nd|rd|th))$", re.I)


def _muni(name, suffix):
    m = ORDINAL.match(name)
    if m:
        return f"{m.group(1)} {m.group(2)} Ward"
    return name + " " + suffix


def map_contest(header):
    h = re.sub(r"\s+", " ", header.strip())
    hu = h.upper()

    if hu.startswith("SUPERIOR COURT - RETAIN "):
        return ("Judge of the Superior Court Retention - "
                + smart_title(hu.split("RETAIN", 1)[1].strip()), "")
    if hu.startswith("COURT OF COMMON PLEAS - RETAIN "):
        return ("Judge of the Court of Common Pleas Retention - "
                + smart_title(hu.split("RETAIN", 1)[1].strip()), "")

    fixed = {
        "JUSTICE OF THE SUPREME COURT": "Justice of the Supreme Court",
        "JUDGE OF THE SUPERIOR COURT": "Judge of the Superior Court",
        "JUDGE OF THE COMMONWEALTH COURT": "Judge of the Commonwealth Court",
        "COUNTY COMMISSIONER": "County Commissioner",
        "DISTRICT ATTORNEY": "District Attorney",
        "CLERK OF COURTS": "Clerk of Courts",
        "CONTROLLER": "Controller",
        "CORONER": "Coroner",
        "PROTHONOTARY": "Prothonotary",
        "REGISTER AND RECORDER": "Register and Recorder",
        "SHERIFF": "Sheriff",
    }
    if hu in fixed:
        return fixed[hu], ""

    m = re.match(r"^MAGISTERIAL DISTRICT JUDGE #(.+)$", hu)
    if m:
        return "Magisterial District Judge", "Magisterial District " + m.group(1)

    m = re.match(r"^SCHOOL DIRECTOR (.+)$", hu)
    if m:
        return "School Director", smart_title(m.group(1))

    m = re.match(r"^(AUDITOR|TAX COLLECTOR|TOWNSHIP SUPERVISOR)"
                 r"(?: (\d)YR)? (.+)$", hu)
    if m:
        base, term, rest = m.group(1), m.group(2), smart_title(m.group(3))
        suffix = "Borough" if rest in BOROUGHS else "Township"
        office = {"AUDITOR": "Township Auditor",
                  "TAX COLLECTOR": "Tax Collector",
                  "TOWNSHIP SUPERVISOR": "Township Supervisor"}[m.group(1)]
        if term:
            office += f" ({term} Year)"
        return office, _muni(rest, suffix)

    m = re.match(r"^(COUNCILPERSON)(?: (\d)YR)? (.+)$", hu)
    if m:
        office = "Councilperson"
        if m.group(2):
            office += f" ({m.group(2)} Year)"
        rest = smart_title(m.group(3))
        return office, _muni(rest, "Borough")

    m = re.match(r"^MAYOR (.+)$", hu)
    if m:
        return "Mayor", _muni(smart_title(m.group(1)), "Borough")

    return smart_title(hu), ""


if __name__ == "__main__":
    run_cli("Franklin", map_contest, parse_esr, titlecase=True)