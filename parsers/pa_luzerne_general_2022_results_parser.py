#!/usr/bin/env python3
"""
Parse Luzerne County PA 2022 General Election precinct results.

Source: "Luzerne County Precinct Detail 2022 General.xls" (SOE Software
SpreadsheetML export; vote-type column order Election Day / Provisional /
Mail-in-Absentee / Total, read from the label row by the shared engine).

Usage: python pa_luzerne_general_2022_results_parser.py <input.xls> <output.csv>

Uses the shared engine in soe_xml_precinct.py.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from soe_xml_precinct import SovcXmlConfig, run_cli  # noqa: E402

CONFIG = SovcXmlConfig(county="Luzerne")

if __name__ == "__main__":
    run_cli(CONFIG)
