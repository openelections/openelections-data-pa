#!/usr/bin/env python3
"""Parse McKean County PA 2024 Primary precinct results.

Source: "McKean PA 2024 Primary Precinct Report.pdf" (Electionware
"Summary Results Report", 42 precinct blocks over 336 pages, generated
04/26/2024 — unofficial results).

Uses the shared ``electionware_primary_np`` engine (block extraction +
primary row parsing) with a county-specific config. The 4-integer vote tail
is standard: TOTAL, Election Day, Mail-In, Provisional.

Quirks handled here:
- Office headers carry the party in title case ("Dem PRESIDENT OF THE
  UNITED STATES", "Rep ATTORNEY GENERAL") — the engine's party-prefix split
  is case-insensitive, so no special handling is needed.
- Headers omit district ordinals; McKean sits in U.S. House 15, State
  Senate 25 and State House 67 (matches McKean's 2024 general CSV).
- Delegates/alternates are elected countywide ("Vote For 3/4") with no
  district printed, so ``district`` is left empty for them.
- The Statistics block reports Registered Voters and Ballots Cast per
  party; per-party rows are emitted in addition to the totals (2022-primary
  precedent for this county), using DEM / REP / NONPARTISAN party labels.
- Write-in detail rows ("Write-In: ...") follow the engine's convention of
  being skipped; only the source-printed "Write-In Totals" aggregate row is
  kept (it carries the contest party).

Output header follows the 2024-primary convention (Adams/Venango):
county,precinct,office,district,party,candidate,votes,election_day,provisional,absentee
with the Mail-In column mapped to ``absentee``.

Usage:
    uv run python parsers/pa_mckean_primary_2024_results_parser.py <input.pdf> <output.csv>
"""

import csv
import re
import sys
from pathlib import Path

import natural_pdf as npdf

from electionware_primary_np import (
    PrimaryConfig,
    parse_primary_precinct_rows,
)
from electionware_precinct_np import extract_precinct_blocks

DISTRICT_FIXES = {
    "U.S. House": "15",
    "State Senate": "25",
    "State House": "67",
}

# The engine's generic title-caser drops capitals after "." and inside
# "De"/"Mc"-style surnames; restore the source's printed forms.
CANDIDATE_FIXES = {
    "Eugene Depasquale": "Eugene DePasquale",
    "Keir Bradford-grey": "Keir Bradford-Grey",
    "Tim Defoor": "Tim DeFoor",
}

SKIP_PREFIXES = (
    "Summary Results Report",
    "UNOFFICIAL RESULTS",
    "2024 Presidential Primary",
    "April 23, 2024",
    "Report generated with Electionware",
    "Page ",
    "Voter Turnout",
)


CONFIG = PrimaryConfig(
    county="McKean",
    skip_prefixes=SKIP_PREFIXES,
    county_header_suffix="McKean County",
)

# Column-header lines printed under every office header ("TOTAL Election
# Day Mail-In Provisional" splits into fragments). None of them end in a
# 4-integer vote tail, but drop them explicitly so nothing can misattribute.
COLUMN_HEADER_EXACT = {"Day", "Election", ""}
COLUMN_HEADER_PREFIXES = (
    "TOTAL ",
    "Statistics",
    "STATISTICS",
)

# Statistics block party rows.
RV_PARTY_RE = re.compile(
    r"^Registered Voters - (Democratic|Republican|Nonpartisan)\s+([\d,]+)\s*$"
)
BC_PARTY_RE = re.compile(
    r"^Ballots Cast - (Democratic|Republican|Nonpartisan)\s+([\d,]+)\s+"
    r"(\d[\d,]*)\s+(\d[\d,]*)\s+(\d[\d,]*)\s*$"
)
PARTY_MAP = {"Democratic": "DEM", "Republican": "REP", "Nonpartisan": "NONPARTISAN"}

FIELDNAMES = [
    "county",
    "precinct",
    "office",
    "district",
    "party",
    "candidate",
    "votes",
    "election_day",
    "provisional",
    "absentee",
]


def _clean_block_text(text: str) -> str:
    """Drop page-header noise; keep candidate/office/stat lines."""
    out = []
    for raw in text.split("\n"):
        line = raw.strip()
        if not line:
            out.append("")
            continue
        if line.startswith(SKIP_PREFIXES):
            continue
        if line in COLUMN_HEADER_EXACT:
            continue
        if line.startswith(COLUMN_HEADER_PREFIXES):
            continue
        out.append(line)
    return "\n".join(out)


def _stats_rows(precinct: str, text: str) -> list[dict]:
    """Per-party Registered Voters / Ballots Cast rows from the Statistics
    block (the engine emits only the totals)."""
    rows = []
    for raw in text.split("\n"):
        line = raw.strip()
        m = RV_PARTY_RE.match(line)
        if m:
            rows.append(
                {
                    "county": "McKean",
                    "precinct": precinct,
                    "office": "Registered Voters",
                    "district": "",
                    "party": PARTY_MAP[m.group(1)],
                    "candidate": "",
                    "votes": int(m.group(2).replace(",", "")),
                    "election_day": "",
                    "provisional": "",
                    "absentee": "",
                }
            )
            continue
        m = BC_PARTY_RE.match(line)
        if m:
            total, ed, mail, prov = (int(g.replace(",", "")) for g in m.groups()[1:])
            rows.append(
                {
                    "county": "McKean",
                    "precinct": precinct,
                    "office": "Ballots Cast",
                    "district": "",
                    "party": PARTY_MAP[m.group(1)],
                    "candidate": "",
                    "votes": total,
                    "election_day": ed,
                    "provisional": prov,
                    "absentee": mail,
                }
            )
    return rows


def parse_pdf(pdf_path: Path) -> tuple[list[dict], int]:
    pdf = npdf.PDF(str(pdf_path))
    rows: list[dict] = []
    precinct_count = 0
    for precinct_name, text in extract_precinct_blocks(pdf, CONFIG):
        precinct_count += 1
        pretty = re.sub(r"\s{2,}", " ", precinct_name).strip()
        # Engine's PrimaryConfig already sets county; rows carry
        # county/office/district/party/candidate/votes/election_day/
        # provisional/absentee with Mail-In -> absentee.
        for row in parse_primary_precinct_rows(pretty, _clean_block_text(text), CONFIG):
            out = {
                "county": "McKean",
                "precinct": pretty,
                "office": row["office"],
                "district": DISTRICT_FIXES.get(row["office"], row.get("district", "")),
                "party": row.get("party", ""),
                "candidate": CANDIDATE_FIXES.get(
                    row.get("candidate", ""), row.get("candidate", "")
                ),
                "votes": row["votes"],
                "election_day": row.get("election_day", ""),
                "provisional": row.get("provisional", ""),
                "absentee": row.get("absentee", ""),
            }
            rows.append(out)
        # Cleaned text still carries the per-party statistics lines; reuse it
        # for the per-party RV / Ballots Cast rows.
        rows.extend(_stats_rows(pretty, _clean_block_text(text)))
    return rows, precinct_count


def write_csv(rows: list[dict], out_path: Path) -> None:
    with out_path.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)


if __name__ == "__main__":
    argv = sys.argv
    if len(argv) != 3:
        sys.exit(f"Usage: {Path(argv[0]).name} <input.pdf> <output.csv>")
    pdf_path = Path(argv[1])
    out_path = Path(argv[2])
    if not pdf_path.exists():
        sys.exit(f"Missing PDF: {pdf_path}")
    rows, precinct_count = parse_pdf(pdf_path)
    write_csv(rows, out_path)
    print(f"Wrote {len(rows)} rows across {precinct_count} precincts to {out_path}")
