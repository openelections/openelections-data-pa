#!/usr/bin/env python3
"""Pike County 2023 general — county-level results from the official
"Official Results Summary Report" (Dominion ESR2).

The county's precinct-level PDF is a scan (no text layer) and is ignored;
this summary report is the only usable source.  Named write-in detail rows
appear in a block after "Total Votes" and are merged into a single
"Write-ins" row per contest.  The source reports "Ballots Cast: 12,835" but
no Registered Voters line.

Usage: pa_pike_general_2023_results_parser.py <input.pdf|txt> <output.csv>
"""

import re

from pa_2023_summary_common import parse_esr2, run_cli

FIXED = {
    "JUSTICE OF THE SUPREME COURT": "Justice of the Supreme Court",
    "JUDGE OF THE SUPERIOR COURT": "Judge of the Superior Court",
    "JUDGE OF THE COMMONWEALTH COURT": "Judge of the Commonwealth Court",
    "COUNTY COMMISSIONER": "County Commissioner",
    "COUNTY AUDITOR": "County Auditor",
    "COUNTY TREASURER": "County Treasurer",
    "DISTRICT ATTORNEY": "District Attorney",
    "CORONER": "Coroner",
    "PROTHONOTARY/CLERK OF THE COURTS": "Prothonotary/Clerk of the Courts",
    "RECORDER OF DEEDS/REGISTER OF WILLS":
        "Recorder of Deeds/Register of Wills",
}

TWP_OFFICE = {
    "SUPERVISOR": "Township Supervisor",
    "AUDITOR": "Township Auditor",
    "JUDGE OF ELECTIONS": "Judge of Elections",
    "INSPECTOR OF ELECTIONS": "Inspector of Elections",
    "CONSTABLE": "Constable",
}


def map_contest(header):
    h = re.sub(r"\s+", " ", header.strip())
    h = re.sub(r"\s*\(Vote for \d+\)\s*$", "", h).strip()
    hu = h.upper()

    m = re.match(r"^SUPERIOR COURT JUDGE RETAIN (PANELLA|STABILE)$", hu)
    if m:
        who = ("Jack Panella" if m.group(1) == "PANELLA"
               else "Victor P. Stabile")
        return "Judge of the Superior Court Retention - " + who, ""

    if hu in FIXED:
        return FIXED[hu], ""

    m = re.match(r"^MAGISTERIAL DISTRICT JUDGE (\d{2}-\d-\d{2})$", hu)
    if m:
        return "Magisterial District Judge", "Magisterial District " + m.group(1)

    m = re.match(r"^([A-Z ]+?) TWP #(\d) (JUDGE OF ELECTIONS|"
                 r"INSPECTOR OF ELECTIONS)$", hu)
    if m:
        return TWP_OFFICE[m.group(3)], m.group(1).title() + " Township #" + m.group(2)

    m = re.match(r"^([A-Z ]+?) TWP (\dYR )?(SUPERVISOR|AUDITOR|CONSTABLE|"
                 r"JUDGE OF ELECTIONS|INSPECTOR OF ELECTIONS|"
                 r"AMBULANCE TAX QUESTION)$", hu)
    if m:
        m = (m.group(1), m.group(2), m.group(3))
    if not m:
        m2 = re.match(r"^([A-Z ]+?) TWP (SUPERVISOR|AUDITOR|CONSTABLE|"
                      r"JUDGE OF ELECTIONS|INSPECTOR OF ELECTIONS|"
                      r"AMBULANCE TAX QUESTION) (\d)YR$", hu)
        if m2:
            m = (m2.group(1), m2.group(3) + "YR", m2.group(2))
    if m:
        name, term, base = m
        office = TWP_OFFICE.get(base, base.title())
        if term:
            office += f" ({term[0]} Year)"
        return office, name.strip().title() + " Township"

    m = re.match(r"^([A-Z ]+?) BORO (COUNCILMAN|MAYOR|CONSTABLE|"
                 r"JUDGE OF ELECTIONS|INSPECTOR OF ELECTIONS)$", hu)
    if m:
        office = {"COUNCILMAN": "Borough Council",
                  "MAYOR": "Mayor",
                  "CONSTABLE": "Constable",
                  "JUDGE OF ELECTIONS": "Judge of Elections",
                  "INSPECTOR OF ELECTIONS": "Inspector of Elections"}[
                      m.group(2)]
        boro = m.group(1).strip().title()
        m2 = re.match(r"^(.*?)(\d)$", boro)
        ward = ""
        if m2:
            boro, ward = m2.group(1).strip(), " #" + m2.group(2)
        return office, boro + " Borough" + ward

    m = re.match(r"^([A-Z ]+?) SCHOOL DISTRICT$", hu)
    if m:
        return "School Director", m.group(1).strip().title() + " School District"

    return h.title(), ""


if __name__ == "__main__":
    run_cli("Pike", map_contest, parse_esr2)