#!/usr/bin/env python3
"""Clarion County 2023 general — county-level results from the official
"Official County Wide Combined Report" (Dominion ESR2, Total-only columns).

Per-contest "Times Cast" lines are turnout metadata, not results; each
contest's candidate block carries an aggregate "Write-in" row followed by
named write-in detail rows (the details are skipped — the aggregate is kept).

Usage: pa_clarion_general_2023_results_parser.py <input.pdf|txt> <output.csv>
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

    if h == "Jack Panella Retention Question":
        office, dist = "Judge of the Superior Court Retention - Jack Panella", ""
    elif h == "Victor P Stabile Retention Question":
        office = "Judge of the Superior Court Retention - Victor P. Stabile"
        dist = ""
    else:
        fixed = {
            "Justice of the Supreme Court": "Justice of the Supreme Court",
            "Judge of the Superior Court": "Judge of the Superior Court",
            "Judge of the Commonwealth Court":
                "Judge of the Commonwealth Court",
            "County Commissioners": "County Commissioner",
            "County Auditors": "County Auditor",
            "County Treasurer": "County Treasurer",
            "District Attorney": "District Attorney",
            "Prothonotary": "Prothonotary",
            "Register/Recorder": "Register/Recorder",
        }.get(h)
        if fixed:
            office, dist = fixed, ""
        else:
            m = re.match(r"^(Township Supervisor|Township Auditor|"
                         r"Township Tax Collector|Borough Council|"
                         r"Borough Auditor|Borough Mayor|Borough Tax Collector|"
                         r"School Director)\s+(.+)$", h)
            if m:
                office, dist = m.group(1), m.group(2).strip()
            else:
                office, dist = h, ""

    if term:
        office += f" ({term} Year)"
    return office, dist


if __name__ == "__main__":
    run_cli("Clarion", map_contest, parse_esr2)