#!/usr/bin/env python3
"""Parse McKean County PA 2022 Primary precinct results.

Source: "McKean PA 2022 Primary Precinct Report Official Results.pdf"
(Electionware precinct summary, "Recount MASTER" build generated 06/06/2022 —
post-recount official results, so GOP U.S. Senate values match the certified
recount anchor).

Uses the shared ``electionware_primary_np`` engine with a county-specific
``PrimaryConfig``. McKean's 2022 report uses the standard 4-integer vote tail
(TOTAL, Election Day, Mail, Provisional).

Quirks handled here:
- State Committee headers carry no party prefix ("MEMBER OF THE REPUBLICAN
  STATE COMMITTEE"); a line preprocessor injects DEM/REP so candidate rows
  inherit the correct contest party.
- The Statistics section reports Registered Voters and Ballots Cast per party;
  per-party rows are emitted in addition to the totals (2020 primary
  precedent), using the contest-free party labels DEM / REP / NONPARTISAN.
- Office headers omit district ordinals; McKean sits in U.S. House 15 and
  State House 67 (matches McKean's 2022 general CSV).

Output header matches the 2022-primary convention:
county,precinct,office,district,party,candidate,votes,election_day,mail,provisional

Usage:
    uv run python parsers/pa_mckean_primary_2022_results_parser.py <input.pdf> <output.csv>
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


# Office headers omit the district ordinal (e.g. "DEM REPRESENTATIVE IN
# CONGRESS"). Districts per McKean's 2022 general CSV; State Committee uses
# the district value carried by the 2022 primary county-level anchor (and
# Allegheny's precinct file) for State Committee contests.
DISTRICT_FIXES = {
    "U.S. House": "15",
    "State House": "67",
    "Member of Democratic State Committee": "42",
    "Member of Republican State Committee": "42",
}

SKIP_PREFIXES = (
    "Precinct Results",
    "2022 General Primary",
    "May 17, 2022",
    "Precinct Summary - ",
    "Report generated with Electionware",
    "Vote For ",
    "Voter Turnout",
    "TOTAL",
    "Election",
    "Day",
    "Registered Voters - DEM",
    "Registered Voters - REP",
    "Registered Voters - NON",
    "Ballots Cast - DEM",
    "Ballots Cast - REP",
    "Ballots Cast - NON",
    "Ballots Cast - Blank",
)


def _preprocess_line(line: str) -> str:
    """Give the unprefixed State Committee headers a party so candidate rows
    inherit the right party (engine derives party from the office header)."""
    if line.startswith("MEMBER OF THE DEMOCRATIC STATE COMMITTEE"):
        return "DEM MEMBER OF THE DEMOCRATIC STATE COMMITTEE"
    if line.startswith("MEMBER OF THE REPUBLICAN STATE COMMITTEE"):
        return "REP MEMBER OF THE REPUBLICAN STATE COMMITTEE"
    return line


CONFIG = PrimaryConfig(
    county="McKean",
    skip_prefixes=SKIP_PREFIXES,
    county_header_suffix="McKean County",
    line_preprocessor=_preprocess_line,
)

# Party labels as printed in the Statistics section.
RV_PARTY_RE = re.compile(
    r"^Registered Voters - (DEMOCRATIC|REPUBLICAN|NONPARTISAN)\s+([\d,]+)\s*$"
)
BC_PARTY_RE = re.compile(
    r"^Ballots Cast - (DEMOCRATIC|REPUBLICAN|NONPARTISAN)\s+([\d,]+)\s+"
    r"(\d[\d,]*)\s+(\d[\d,]*)\s+(\d[\d,]*)\s*$"
)
RV_PARTY_MAP = {"DEMOCRATIC": "DEM", "REPUBLICAN": "REP", "NONPARTISAN": "NONPARTISAN"}

FIELDNAMES = [
    "county",
    "precinct",
    "office",
    "district",
    "party",
    "candidate",
    "votes",
    "election_day",
    "mail",
    "provisional",
]


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
                    "party": RV_PARTY_MAP[m.group(1)],
                    "candidate": "",
                    "votes": int(m.group(2).replace(",", "")),
                    "election_day": "",
                    "mail": "",
                    "provisional": "",
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
                    "party": RV_PARTY_MAP[m.group(1)],
                    "candidate": "",
                    "votes": total,
                    "election_day": ed,
                    "mail": mail,
                    "provisional": prov,
                }
            )
    return rows


def _to_output_row(row: dict) -> dict:
    out = {
        "county": row["county"],
        "precinct": row["precinct"],
        "office": row["office"],
        "district": DISTRICT_FIXES.get(row["office"], row.get("district", "")),
        "party": row.get("party", ""),
        "candidate": row.get("candidate", ""),
        "votes": row["votes"],
        "election_day": row.get("election_day", ""),
        "mail": row.get("mail", ""),
        "provisional": row.get("provisional", ""),
    }
    return out


def parse_pdf(pdf_path: Path) -> tuple[list[dict], int]:
    pdf = npdf.PDF(str(pdf_path))
    rows: list[dict] = []
    precinct_count = 0
    for precinct_name, text in extract_precinct_blocks(pdf, CONFIG):
        precinct_count += 1
        pretty = re.sub(r"\s{2,}", " ", precinct_name).strip()
        for row in parse_primary_precinct_rows(pretty, text, CONFIG):
            # 2022-primary cycle convention (Allegheny/McKean-general):
            # aggregate write-in row is named "Write Ins" rather than the
            # engine's "Write-In Totals"; it still carries the contest party.
            if row.get("candidate") == "Write-In Totals":
                row["candidate"] = "Write Ins"
            rows.append(
                _to_output_row(
                    {
                        "county": "McKean",
                        "precinct": pretty,
                        "office": row["office"],
                        "district": row.get("district", ""),
                        "party": row.get("party", ""),
                        "candidate": row.get("candidate", ""),
                        "votes": row["votes"],
                        "election_day": row.get("election_day", ""),
                        "mail": row.get("absentee", ""),
                        "provisional": row.get("provisional", ""),
                    }
                )
            )
        rows.extend(_stats_rows(pretty, text))
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