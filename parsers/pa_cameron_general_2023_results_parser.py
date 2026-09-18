#!/usr/bin/env python3
"""Cameron County 2023 general — county-level results from the official
"Official Results" county summary (Electionware ESR).

NOTE: the source reports "Registered Voters - Total" as 0 (a known quirk of
this export); the source value is preserved in the output.

Usage: pa_cameron_general_2023_results_parser.py <input.pdf|txt> <output.csv>
"""

import re

from pa_2023_summary_common import parse_esr, run_cli, smart_title

# contest header (upper case) -> (office, district)
CONTESTS = {}


def _tc(s):
    return re.sub(r"\s+", " ", smart_title(s)).strip()


def map_contest(header):
    h = re.sub(r"\s+", " ", header.strip())
    hu = h.upper()
    if hu.startswith("SUPERIOR COURT - RETAIN "):
        judge = _tc(hu.split("RETAIN", 1)[1].strip())
        return ("Judge of the Superior Court Retention - " + judge, "")
    m = re.match(r"^(DRIFTWOOD|EMPORIUM) BOROUGH (.+)$", hu)
    if m:
        boro = m.group(1).title() + " Borough"
        office = {
            "TAX COLLECTOR": "Tax Collector",
            "COUNCIL PERSON": "Council",
        }.get(m.group(2), _tc(m.group(2)))
        return (office, boro)
    m = re.match(r"^(GIBSON|GROVE|LUMBER|PORTAGE|SHIPPEN) TOWNSHIP (.+)$", hu)
    if m:
        twp = m.group(1).title() + " Township"
        office = {
            "TAX COLLECTOR": "Tax Collector",
            "SUPERVISOR": "Township Supervisor",
            "AUDITOR": "Township Auditor",
        }.get(m.group(2), _tc(m.group(2)))
        return (office, twp)
    office = {
        "JUSTICE OF THE SUPREME COURT": "Justice of the Supreme Court",
        "JUDGE OF THE SUPERIOR COURT": "Judge of the Superior Court",
        "JUDGE OF THE COMMONWEALTH COURT": "Judge of the Commonwealth Court",
        "MAGISTERIAL DISTRICT JUDGE": "Magisterial District Judge",
        "DISTRICT ATTORNEY": "District Attorney",
        "COUNTY COMMISSIONER": "County Commissioner",
        "COUNTY AUDITOR": "County Auditor",
        "COUNTY TREASURER": "County Treasurer",
        "COUNTY CORONER": "Coroner",
        "COUNTY SHERIFF": "Sheriff",
        "SCHOOL BOARD DIRECTOR AT LARGE": "School Director",
    }.get(hu)
    if office is None:
        office = _tc(h)
    return office, ""


if __name__ == "__main__":
    run_cli("Cameron", map_contest, parse_esr, titlecase=True)