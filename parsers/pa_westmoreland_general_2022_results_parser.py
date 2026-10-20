#!/usr/bin/env python3
"""
Parse Westmoreland County PA 2022 General Election precinct results.

Source: "Westmoreland County Precinct Detail 2022 General.xls" (SOE Software
SpreadsheetML export; ALL-CAPS contest titles with "( N)" vote-for markers and
party-prefixed candidate headers ("REP Mehmet Oz")).

Usage: python pa_westmoreland_general_2022_results_parser.py <input.xls> <output.csv>

Uses the shared engine in soe_xml_precinct.py.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from soe_xml_precinct import SovcXmlConfig, run_cli  # noqa: E402

# Referendum titles end with the SOE Software precinct-group code in ALL-CAPS
# ("... Question NORWIN", "... SOUTHWEST GR"); keep the printed question text.
OFFICE_MAP = {
    "Norwin Public Library Referendum Question NORWIN":
        "Norwin Public Library Referendum Question",
    "Southwest Greensburg Library Tax Question SOUTHWEST GR":
        "Southwest Greensburg Library Tax Question",
}

CONFIG = SovcXmlConfig(county="Westmoreland", party_prefix=True, office_map=OFFICE_MAP)

if __name__ == "__main__":
    run_cli(CONFIG)
