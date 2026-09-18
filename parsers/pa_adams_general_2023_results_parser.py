#!/usr/bin/env python3
"""
Parser for Adams County, PA 2023 General Election precinct results.

Source: Adams County Precinct Summary Results 2023 General.pdf
(Electionware "Summary Results Report" format, one precinct per
Statistics-marked section; candidate rows carry a VOTE % column).

Parsed from the layout-preserving pdftotext extract via the shared text
engine in ``electionware_txt`` (the natural_pdf engine in
``electionware_precinct_np`` times out on the 429-page PDF). The txt
extract may be passed directly, or the PDF (converted with pdftotext).

Usage:
    python parsers/pa_adams_general_2023_results_parser.py \
        "<input.txt|input.pdf>" "<output.csv>"

Adams conventions (reproduced from the county's 2025 file
``2025/counties/20251104__pa__general__adams__precinct.csv``):
  - Local office headers are "<Office> [Nyr] <Muni>" (e.g. "Auditor 2yr
    Berwick", "Supervisor 6yr Mt Pleasant"); the office keeps the leading
    office words and the whole remainder ("2yr Berwick") is the district.
  - "Magisterial District Judge District 51-3-03" keeps "District" in the
    district value, as in the 2025 file.
  - Superior Court retention headers read "Superior Court - Retain Jack
    Panella"; normalized to "Superior Court Retention - Jack Panella".
"""

import re

from electionware_txt import TxtConfig, run_cli
from electionware_precinct_np import title_case


EXACT_OFFICES = {
    "Justice of the Supreme Court": ("Justice of the Supreme Court", ""),
    "Judge of the Superior Court": ("Judge of the Superior Court", ""),
    "Judge of the Commonwealth Court": ("Judge of the Commonwealth Court", ""),
    "Judge of the Court of Common Pleas": ("Judge of the Court of Common Pleas", ""),
    "County Commissioner": ("County Commissioner", ""),
    "District Attorney": ("District Attorney", ""),
    "Controller": ("Controller", ""),
    "Coroner": ("Coroner", ""),
    "Prothonotary": ("Prothonotary", ""),
    "Register and Recorder": ("Register and Recorder", ""),
    "Sheriff": ("Sheriff", ""),
    "County Treasurer": ("County Treasurer", ""),
}

RETENTION_RE = re.compile(
    r"^(Supreme|Superior|Commonwealth) Court\s*-\s*Retain\s+(.+)$", re.IGNORECASE
)

# Longest-first local office prefixes (Adams headers are prefix-style).
LOCAL_OFFICE_PREFIXES = [
    "Council Member",
    "School Director",
    "Tax Collector",
    "Supervisor",
    "Auditor",
    "Constable",
    "Mayor",
]

MDJ_RE = re.compile(r"^Magisterial District Judge\s+District\s+(\S+)\s*$", re.IGNORECASE)


def normalize_office(line: str):
    line = re.sub(r"\s+", " ", line).strip()
    if line in EXACT_OFFICES:
        return EXACT_OFFICES[line]

    m = RETENTION_RE.match(line)
    if m:
        court = m.group(1).capitalize()
        tail = m.group(2).strip()
        return (f"{court} Court Retention - {tail}", "")

    m = MDJ_RE.match(line)
    if m:
        return ("Magisterial District Judge", f"District {m.group(1)}")

    for prefix in LOCAL_OFFICE_PREFIXES:
        if line == prefix:
            return (prefix, "")
        if line.startswith(prefix + " "):
            return (prefix, line[len(prefix):].strip())

    return (line, "")


CONFIG = TxtConfig(
    county="Adams",
    normalize_office=normalize_office,
    prettify_precinct=lambda s: s,
    # The report prints "PFF" (as printed, both in the precinct and the
    # county summary) as the party for independent candidate Arend Visher.
    extra_parties=("PFF",),
)


if __name__ == "__main__":
    run_cli(CONFIG)