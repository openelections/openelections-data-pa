#!/usr/bin/env python3
"""
Parser for Adams County, PA 2022 General Election precinct results.

Source: Adams PA 2022 GeneralPrecinctSummary2022.pdf
(Electionware "Summary Results Report" format, one precinct per
Statistics-marked section). Parsed from the layout-preserving pdftotext
extract via the shared text engine in ``electionware_txt`` (the natural_pdf
engine in ``electionware_precinct_np`` times out on the 150-page PDF).

Usage:
    python parsers/pa_adams_general_2022_results_parser.py \
        "<input.txt|input.pdf>" "<output.csv>"

Adams 2022 conventions:
  - Precinct names print as "Cumberland #1"; the "#N" suffix is normalized
    to " N" to match the county's 2023 file ("Cumberland 1").
  - Offices printed:
      "United States Senator"                          -> U.S. Senate
      "Governor and Lieutenant Governor"               -> Governor
      "Representative in Congress 13th District"       -> U.S. House (13)
      "Representative in the General Assembly <N>th District"
                                                       -> State House (<N>)
      "Tyrone Township sale of Liquor Referendum"      -> kept as printed
        (candidate rows Yes / No)
  - Minor-party codes printed in 2022: GNP (Green) and KEY (Keystone).
  - Output uses the 2022 convention header with ``early_voting`` (the
    report's Mail Votes column) and write-in rows labelled "Write Ins".

Note: pages 1-6 of the source (Abbottstown remainder, Arendtsville) are
labelled UNOFFICIAL RESULTS; those are the only pages those precincts
appear on in the file, so they are parsed like any other.
"""

import csv
import re
import sys
from pathlib import Path

from electionware_txt import TxtConfig, parse_input
from electionware_precinct_np import title_case


def prettify_precinct(name: str) -> str:
    # "Cumberland #1" -> "Cumberland 1" (matches the 2023 Adams file)
    return re.sub(r"\s*#\s*(\d+)\s*$", r" \1", name.strip())


US_SENATOR_RE = re.compile(r"^United States Senator$", re.IGNORECASE)
GOVERNOR_RE = re.compile(r"^Governor and Lieutenant Governor$", re.IGNORECASE)
US_CONGRESS_RE = re.compile(r"^Representative in Congress\s+(\d+)(?:st|nd|rd|th)?\s+District$", re.IGNORECASE)
STATE_ASSEMBLY_RE = re.compile(
    r"^Representative in the General Assembly\s+(\d+)(?:st|nd|rd|th)?\s+District$",
    re.IGNORECASE,
)


def normalize_office(line: str):
    line = re.sub(r"\s+", " ", line).strip()
    if US_SENATOR_RE.match(line):
        return ("U.S. Senate", "")
    if GOVERNOR_RE.match(line):
        return ("Governor", "")
    m = US_CONGRESS_RE.match(line)
    if m:
        return ("U.S. House", m.group(1))
    m = STATE_ASSEMBLY_RE.match(line)
    if m:
        return ("State House", m.group(1))
    # Local offices / referendum: keep as printed.
    return (line, "")


CONFIG = TxtConfig(
    county="Adams",
    normalize_office=normalize_office,
    prettify_precinct=prettify_precinct,
    # Minor-party codes printed in 2022 beyond the shared PARTY_CODES:
    # GNP (Green; normalized to GRN below to match the other 2022 county
    # files and the ENR county totals) and KEY (Keystone).
    extra_parties=("GNP", "KEY"),
)

FIELDNAMES = [
    "county", "precinct", "office", "district", "party", "candidate",
    "votes", "election_day", "early_voting", "provisional",
]


def main(argv):
    if len(argv) != 3:
        sys.exit(f"Usage: {Path(argv[0]).name} <input.txt|input.pdf> <output.csv>")
    rows, precinct_count, warnings, unnamed = parse_input(argv[1], CONFIG)
    out_path = Path(argv[2])
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDNAMES)
        writer.writeheader()
        for row in rows:
            out = {
                "county": row["county"],
                "precinct": row["precinct"],
                "office": row["office"],
                "district": row["district"],
                # The report prints the Green Party as "GNP"; every other
                # 2022 county file (and the ENR county totals) use "GRN".
                "party": {"GNP": "GRN"}.get(row["party"], row["party"]),
                "candidate": "Write Ins" if row["candidate"] == "Write-ins" else row["candidate"],
                "votes": row["votes"],
                "election_day": row["election_day"],
                "early_voting": row["mail"],
                "provisional": row["provisional"],
            }
            writer.writerow(out)
    print(f"Wrote {len(rows)} rows across {precinct_count} precincts to {out_path}")
    if unnamed:
        print(f"Skipped {unnamed} unnamed (county-summary) Statistics segment(s)")
    if warnings:
        print(f"WARNING: {len(warnings)} unmatched lines:")
        for w in warnings[:40]:
            print("  " + w)
        if len(warnings) > 40:
            print("  ...")


if __name__ == "__main__":
    main(sys.argv)