#!/usr/bin/env python3
"""Somerset County 2023 general — county-level results from the official
"Official Summary Results Report" (Electionware ESR).

Headers are ALL-CAPS, e.g. "TOWNSHIP SUPERVISOR 6 YR Addison Township",
"BOROUGH COUNCIL 4YR Garrett Borough",
"BERLIN-BROTHERSVALLEY SCH DIR REG 1 4YR".

Usage: pa_somerset_general_2023_results_parser.py <input.pdf|txt> <output.csv>
"""

import re

from pa_2023_summary_common import parse_esr, run_cli, smart_title


def map_contest(header):
    h = re.sub(r"\s+", " ", header.strip())
    hu = h.upper()

    m = re.match(r"^SUPERIOR COURT RETENTION ELECTION QUESTION (.+)$", hu)
    if m:
        judge = m.group(1).strip()
        who = ("Jack Panella" if "PANELLA" in judge
               else "Victor P. Stabile" if "STABILE" in judge
               else smart_title(judge))
        return "Judge of the Superior Court Retention - " + who, ""

    fixed = {
        "JUSTICE OF THE SUPREME COURT": "Justice of the Supreme Court",
        "JUDGE OF THE SUPERIOR COURT": "Judge of the Superior Court",
        "JUDGE OF THE COMMONWEALTH COURT": "Judge of the Commonwealth Court",
        "COUNTY COMMISSIONER": "County Commissioner",
        "COUNTY AUDITOR": "County Auditor",
        "DISTRICT ATTORNEY": "District Attorney",
        "HIGH SHERIFF": "Sheriff",
        "CLERK OF COURTS": "Clerk of Courts",
        "RECORDER OF DEEDS": "Recorder of Deeds",
    }
    if hu in fixed:
        return fixed[hu], ""
    if hu == "REGISTER OF WILLS AND CLERK OF THE ORPHANS' COURT DIVISION":
        return "Register of Wills and Clerk of the Orphans' Court Division", ""

    m = re.match(r"^MAGISTERIAL DISTRICT JUDGE (\d{2}-\d-\d{2})$", hu)
    if m:
        return "Magisterial District Judge", "Magisterial District " + m.group(1)

    m = re.match(r"^(TAX COLLECTOR) (\d)\s*YR (.+)$", hu)
    if m:
        return f"Tax Collector ({m.group(2)} Year)", smart_title(m.group(3))

    m = re.match(r"^(BOROUGH AUDITOR|BOROUGH COUNCIL|MAYOR|"
                 r"TOWNSHIP AUDITOR|TOWNSHIP SUPERVISOR) (\d) ?YR (.+)$", hu)
    if m:
        office = {
            "BOROUGH AUDITOR": "Borough Auditor",
            "BOROUGH COUNCIL": "Borough Council",
            "MAYOR": "Mayor",
            "TOWNSHIP AUDITOR": "Township Auditor",
            "TOWNSHIP SUPERVISOR": "Township Supervisor",
        }[m.group(1)]
        return f"{office} ({m.group(2)} Year)", smart_title(m.group(3))

    # "<SCHOOL DISTRICT> SCH DIR REG <n> <t>YR" / "... DIRECTORS AT LARGE <t> YR"
    m = re.match(r"^(.+?) SCH DIR REG (\d) (\d)\s*YR$", hu)
    if m:
        return (f"School Director ({m.group(3)} Year)",
                smart_title(m.group(1)) + " School District Region "
                + m.group(2))
    m = re.match(r"^(.+?) SCH DIR DIRECTORS AT LARGE (\d) ?YR$", hu)
    if m:
        return (f"School Director ({m.group(2)} Year)",
                smart_title(m.group(1)) + " School District At Large")

    return smart_title(hu), ""


if __name__ == "__main__":
    run_cli("Somerset", map_contest, parse_esr, titlecase=True)