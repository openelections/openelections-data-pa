#!/usr/bin/env python3
"""Carbon County 2023 general — county-level results from the official
"Official Results" county summary (Dominion ESR2, Total-only columns).

The source has NO party column (candidate rows carry name + total only);
party is left empty.  Per-contest "Times Cast" (14,956) disagrees with
Ballots Cast (15,842) in the source; see the validation log.

Usage: pa_carbon_general_2023_results_parser.py <input.pdf|txt> <output.csv>
"""

import re

from pa_2023_summary_common import parse_esr2, run_cli


def map_contest(header):
    h = re.sub(r"\s+", " ", header.strip())
    h = re.sub(r"\s*\(Vote for \d+\)\s*$", "", h).strip()
    term = None
    m = re.search(r"\((\d) Year Term\)", h, re.I)
    if m:
        term = m.group(1)
        h = (h[:m.start()] + " " + h[m.end():]).strip()
    h = re.sub(r"\s+", " ", h).strip()

    m = re.match(r"^(Jack Panella|Victor P Stabile) Superior Court Retention "
                 r"Question$", h, re.I)
    if m:
        who = ("Jack Panella" if m.group(1).lower().startswith("jack")
               else "Victor P. Stabile")
        office = "Judge of the Superior Court Retention - " + who
        h = ""
    else:
        fixed = {
            "Justice of the Supreme Court": "Justice of the Supreme Court",
            "Judge of the Superior Court": "Judge of the Superior Court",
            "Judge of the Commonwealth Court":
                "Judge of the Commonwealth Court",
            "County Commissioners": "County Commissioner",
            "District Attorney": "District Attorney",
            "Controller": "Controller",
            "Coroner": "Coroner",
            "Prothonotary": "Prothonotary",
            "Recorder of Deeds": "Recorder of Deeds",
            "Sheriff": "Sheriff",
        }.get(h)
        if fixed:
            office = fixed
            h = ""
        else:
            m = re.match(r"^(Magisterial District Judge) (\d{2}-\d-\d{2})$", h)
            if m:
                office = "Magisterial District Judge"
                h = "Magisterial District " + m.group(2)
            else:
                m = re.match(r"^(Auditor|Supervisor|Mayor|Council|"
                             r"Tax Collector|School Director)(?: for)? (.+)$", h)
                if m:
                    base, rest = m.group(1), m.group(2).strip()
                    office = {
                        "Auditor": "Township Auditor",
                        "Supervisor": "Township Supervisor",
                        "Mayor": "Mayor",
                        "Council": "Borough Council",
                        "Tax Collector": "Tax Collector",
                        "School Director": "School Director",
                    }[base]
                    h = rest
                else:
                    office = h
                    h = ""

    if term:
        office += f" ({term} Year)"
    return office, h


if __name__ == "__main__":
    run_cli("Carbon", map_contest, parse_esr2)