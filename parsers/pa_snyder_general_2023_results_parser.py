#!/usr/bin/env python3
"""
Parse Snyder County PA 2023 General (Municipal) Election precinct results.

Source: Snyder County Official Precinct Summary 2023 General.pdf
(Electionware "Summary Results Report", TOTAL-only columns — no
election_day/mail/provisional breakdowns). Parsed from the pdftotext
-layout extract in work2023/txt/.

Usage:
    python parsers/pa_snyder_general_2023_results_parser.py \
        <input.txt|input.pdf> <output.csv>

Uses the text-based shared engine in ``electionware_txt`` with the same
office tables as ``pa_snyder_general_2025_results_parser.py`` (reused via
the shared ``normalize_office``).
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from electionware_precinct_np import (  # noqa: E402
    ElectionwareConfig,
    expand_muni_abbrev,
    normalize_office,
    prettify_all_caps_precinct,
)
from electionware_txt import TxtConfig, run_cli  # noqa: E402

SKIP_PREFIXES = (
    "Summary Results Report OFFICIAL RESULTS",
    "Municipal Election",
    "November 7, 2023 SNYDER COUNTY",
    "Voter Turnout - Total",
    "Vote For ",
    "Total Votes Cast",
    "Contest Totals",
)

EXACT_OFFICES = {
    "JUSTICE OF THE SUPREME COURT": ("Justice of the Supreme Court", ""),
    "JUDGE OF THE SUPERIOR COURT": ("Judge of the Superior Court", ""),
    "JUDGE OF THE COMMONWEALTH COURT": ("Judge of the Commonwealth Court", ""),
    "COUNTY COMMISSIONER": ("County Commissioner", ""),
    "COUNTY TREASURER": ("Treasurer", ""),
    "COUNTY SHERIFF": ("Sheriff", ""),
    "COUNTY DISTRICT ATTORNEY": ("District Attorney", ""),
    "COUNTY PROTHONOTARY": ("Prothonotary", ""),
    "COUNTY CORONER": ("Coroner", ""),
    "COUNTY AUDITOR": ("County Auditor", ""),
    "JUDGE OF THE COURT OF COMMON PLEAS": (
        "Judge of the Court of Common Pleas",
        "",
    ),
}

LOCAL_OFFICES = [
    ("BOROUGH COUNCIL", "Borough Council"),
    ("BOROUGH MAYOR", "Mayor"),
    ("TOWNSHIP SUPERVISOR", "Township Supervisor"),
    ("TOWNSHIP AUDITOR", "Township Auditor"),
    ("TAX COLLECTOR", "Tax Collector"),
    ("SUPERVISOR", "Supervisor"),
    ("AUDITOR", "Auditor"),
    ("MAYOR", "Mayor"),
]

_NP_CFG = ElectionwareConfig(
    county="Snyder",
    skip_prefixes=SKIP_PREFIXES,
    county_header_suffix="SNYDER COUNTY",
    exact_offices=EXACT_OFFICES,
    local_offices=LOCAL_OFFICES,
    local_office_orientation="prefix",
    retention_style="retain",
    municipality_normalizer=expand_muni_abbrev,
)


def _np_normalize(line: str):
    return normalize_office(line, _NP_CFG)


# Snyder's duplicated school-director header: "<DISTRICT> SCHOOL DIRECTOR
# [NNYR] <DISTRICT> SCHOOL DIRECTOR" -> ("School Director [ (N Year)]",
# district).
def _school_director(line: str):
    if "SCHOOL DIRECTOR" not in line.upper():
        return None
    from electionware_precinct_np import TERM_TOKEN_RE

    head, _, tail = line.partition("SCHOOL DIRECTOR")
    district_raw = head.strip()
    if not district_raw:
        return None
    tail_tokens = tail.strip().split()
    years = None
    if tail_tokens:
        tm = TERM_TOKEN_RE.match(tail_tokens[0])
        if tm:
            years = tm.group(1)
            tail_tokens = tail_tokens[1:]
    office = f"School Director ({years} Year)" if years else "School Director"
    district = "-".join(
        " ".join(w.capitalize() for w in part.split())
        for part in district_raw.split("-")
    )
    return (office, district)


def normalize(line: str):
    """Full normalization hook used by the txt engine."""
    res = _school_director(line)
    if res is not None:
        return res
    return _np_normalize(line)


CONFIG = TxtConfig(
    county="Snyder",
    normalize_office=normalize,
    prettify_precinct=prettify_all_caps_precinct,
)

if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(f"Usage: {Path(sys.argv[0]).name} <input.txt|input.pdf> <output.csv>")
    # Snyder's report is TOTAL-only: the shared engine wants 4 tokens for
    # Ballots Cast rows, so post-parse we blank the (unreported) breakdown
    # columns and emit Ballots Cast ourselves from the single token.
    import csv as _csv

    from electionware_txt import FIELDNAMES, parse_input

    rows, n, warnings, unnamed = parse_input(sys.argv[1], CONFIG)
    out = []
    for r in rows:
        r["votes"] = str(r["votes"]).replace(",", "")
        r["election_day"] = r["mail"] = r["provisional"] = ""
        out.append(r)
    # Re-add Ballots Cast rows the engine could not parse (single token):
    import re as _re
    # walk the file, and for each Ballots Cast - Total line find the
    # precinct name above the most recent Statistics marker.
    from electionware_txt import find_precinct_name, txt_lines

    lines = txt_lines(Path(sys.argv[1]))
    precinct = None
    single = _re.compile(r"^Ballots Cast - Total\s+(\d[\d,]*)$")
    stat = _re.compile(r"^(Statistics|STATISTICS)\b")
    for i, ln in enumerate(lines):
        s = ln.strip()
        if stat.match(s):
            precinct = find_precinct_name(lines, i, CONFIG)
            continue
        m = single.match(s)
        if m and precinct:
            out.append(
                {
                    "county": "Snyder",
                    "precinct": CONFIG.prettify_precinct(precinct),
                    "office": "Ballots Cast",
                    "district": "",
                    "party": "",
                    "candidate": "",
                    "votes": m.group(1).replace(",", ""),
                    "election_day": "",
                    "mail": "",
                    "provisional": "",
                }
            )
    # Recover precinct names in file order (same walk the engine does).
    with open(sys.argv[2], "w", newline="") as fh:
        w = _csv.DictWriter(fh, fieldnames=FIELDNAMES)
        w.writeheader()
        w.writerows(out)
    print(f"Wrote {len(out)} rows across {n} precincts to {sys.argv[2]}")
    if warnings:
        print(f"WARNING: {len(warnings)} unmatched lines")
        for w in warnings[:20]:
            print("  " + w)