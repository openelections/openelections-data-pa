#!/usr/bin/env python3
"""
Parse York County PA 2022 General Election precinct results.

Source: "York County Precinct Detail 2022 General.xls" (SOE Software
SpreadsheetML export: Table of Contents + Registered Voters + one contest
per numbered sheet; candidate headers like "Josh SHAPIRO // Austin DAVIS").

Usage: python pa_york_general_2022_results_parser.py <input.xls> <output.csv>

Uses the shared engine in soe_xml_precinct.py.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from soe_xml_precinct import SovcXmlConfig, run_cli  # noqa: E402

CONFIG = SovcXmlConfig(county="York")

if __name__ == "__main__":
    run_cli(CONFIG)