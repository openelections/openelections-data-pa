#!/usr/bin/env python3
"""Butler County 2023 general — county-level results from the official
"County Summary Results Report" (Electionware ESR, countywide contests only).

Usage: pa_butler_general_2023_results_parser.py <input.pdf|txt> <output.csv>
"""

import re

from pa_2023_summary_common import parse_esr, run_cli


def map_contest(header):
    h = re.sub(r"\s*COUNTYWIDE\s*$", "", header, flags=re.I).strip()
    office = {
        "County Commissioner": "County Commissioner",
        "District Attorney": "District Attorney",
        "Prothonotary": "Prothonotary",
        "Recorder of Deeds": "Recorder of Deeds",
        "Register of Wills/Clerk of Orphan's Court":
            "Register of Wills/Clerk of Orphan's Court",
        "County Treasurer": "County Treasurer",
    }.get(h, h)
    return office, ""


if __name__ == "__main__":
    run_cli("Butler", map_contest, parse_esr)