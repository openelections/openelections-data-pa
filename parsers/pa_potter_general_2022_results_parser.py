#!/usr/bin/env python3
"""Parse Potter County PA 2022 General Election precinct results.

Source: "Potter County PA 2022 General Precinct Results.pdf" -- an
Electionware "Precinct Summary" report (one 6-page block per precinct:
STATISTICS + one contest per page, with a VOTE % column in the middle of
each candidate row and long write-in lists that spill onto extra pages).

Uses the shared natural-pdf Electionware engine in
``electionware_precinct_np`` with Potter-2022-specific config, then
post-processes rows to the 2022 repo convention:

  - ``United States Senator``              -> U.S. Senate
  - ``Governor/ Lieutenant Governor``      -> Governor (governor's name
    only; the running mate after " and " is dropped)
  - ``Representative in Congress 15th ...``-> U.S. House, district 15
  - ``Representative in the General Assembly 67th ...`` -> State House, 67
  - Write-in rows renamed ``Write Ins``; Overvotes / Undervotes /
    Ballots Cast Blank rows dropped (2022 convention).

Usage:
    python parsers/pa_potter_general_2022_results_parser.py \\
        "<input.pdf>" "<output.csv>"
"""
from __future__ import annotations

import csv
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from electionware_precinct_np import (
    ElectionwareConfig,
    expand_muni_flexible,
    parse_pdf,
    prettify_huntingdon_precinct,
)

# --------------------------------------------------------------------------
# Line preprocessor: strip the VOTE % column ("  27        19.57%  17 ...").
# --------------------------------------------------------------------------
PCT_RE = re.compile(r"\s+\d+\.\d+%")


def strip_pct(line: str) -> str:
    # "KEY" (Keystone Party) is not in the shared engine's PARTY_CODES;
    # rewrite it to the accepted "NA" token and restore party="KEY" in
    # transform() below.  Potter 2022 has no genuine IND/NA candidates.
    if line.startswith("KEY "):
        line = "NA " + line[4:]
    return PCT_RE.sub("", line)


SKIP_PREFIXES = (
    "Summary Results Report",
    "General Election",
    "November 8, 2022",
    "Precinct Summary - ",
    "Report generated with Electionware",
    "TOTAL Election",
    "Voter Turnout - Total",
    "Vote For ",
    "Total Votes Cast",
    "Contest Totals",
)


def _us_senate(line: str):
    if re.match(r"^United States Senator$", line, re.I):
        return ("U.S. Senate", "")
    return None


def _governor(line: str):
    if re.match(r"^Governor/?\s*Lieutenant Governor$", line, re.I):
        return ("Governor", "")
    return None


def _congress(line: str):
    m = re.match(
        r"^Representative in Congress\s+(\d+)\s*(?:st|nd|rd|th)?\s+"
        r"Congressional District$",
        line,
        re.I,
    )
    if m:
        return ("U.S. House", m.group(1))
    return None


def _state_house(line: str):
    m = re.match(
        r"^Representative in the General Assembly\s+(\d+)\s*"
        r"(?:st|nd|rd|th)?\s+Legislative District$",
        line,
        re.I,
    )
    if m:
        return ("State House", m.group(1))
    return None


EXTRA_OFFICE_HANDLERS = [_us_senate, _governor, _congress, _state_house]

# --------------------------------------------------------------------------
# Post-processing to the 2022 repo convention.
# --------------------------------------------------------------------------

DROP_CANDIDATES = {"Overvotes", "Undervotes"}
WRITE_IN_RENAMES = {"Write-ins": "Write Ins", "Write-Ins": "Write Ins"}
GOV_PAIR_RE = re.compile(r"^(.*?)\s+and\s+", re.I)

FIELDNAMES_2022 = [
    "county", "precinct", "office", "district", "party", "candidate",
    "votes", "election_day", "early_voting", "provisional",
]


def transform(rows: list[dict]) -> list[dict]:
    out = []
    for r in rows:
        cand = r["candidate"]
        if r["office"] == "Ballots Cast Blank":
            continue
        if cand in DROP_CANDIDATES:
            continue
        cand = WRITE_IN_RENAMES.get(cand, cand)
        party = r["party"]
        if party == "NA":  # preprocessor token for KEY (Keystone Party)
            party = "KEY"
        if r["office"] == "Governor" and cand not in ("", "Write Ins",
                                                     "Ballots Cast",
                                                     "Registered Voters"):
            m = GOV_PAIR_RE.match(cand)
            if m:
                cand = m.group(1).strip()
        out.append({
            "county": r["county"],
            "precinct": r["precinct"],
            "office": r["office"],
            "district": r["district"],
            "party": party,
            "candidate": cand,
            "votes": r["votes"],
            "election_day": r["election_day"],
            "early_voting": r["mail"],
            "provisional": r["provisional"],
        })
    return out


def main(argv):
    if len(argv) != 3:
        sys.exit(f"Usage: {Path(argv[0]).name} <input.pdf> <output.csv>")

    # parse_pdf needs the config; build it here so the handlers above are
    # attached (mirrors the 2025 Potter config, adjusted for 2022).
    config = ElectionwareConfig(
        county="Potter",
        skip_prefixes=SKIP_PREFIXES,
        county_header_suffix="POTTER COUNTY, PENNSYLVANIA",
        extra_office_handlers=EXTRA_OFFICE_HANDLERS,
        retention_style="retention",
        municipality_normalizer=expand_muni_flexible,
        prettify_precinct=prettify_huntingdon_precinct,
        line_preprocessor=strip_pct,
    )

    from electionware_precinct_np import parse_pdf as _parse_pdf

    pdf_path = Path(argv[1])
    out_path = Path(argv[2])
    if not pdf_path.exists():
        sys.exit(f"Missing PDF: {pdf_path}")

    raw_rows, precinct_count = _parse_pdf(pdf_path, config)
    rows = transform(raw_rows)

    with out_path.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDNAMES_2022)
        writer.writeheader()
        writer.writerows(rows)
    print(
        f"Wrote {len(rows)} rows across {precinct_count} precincts to {out_path}"
    )


if __name__ == "__main__":
    main(sys.argv)