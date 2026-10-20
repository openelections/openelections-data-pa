#!/usr/bin/env python3
"""Elk County, PA 2022 General Primary -- per-precinct results.

Source: "Elk PA 2022pprecinct.pdf" (Electionware "Summary Results Report",
OFFICIAL RESULTS, generated 06/15/2022; 145 pages, 29 precincts, one block
per precinct). Cross-check / countywide-totals source: "Elk PA 2022pmedia.pdf"
(Election Summary, same 06/15/2022 vintage). Both are dated AFTER the
June 2022 Republican U.S. Senate recount, so the REP U.S. Senate rows carry
POST-recount values; they sum to the ENR county anchor (McCormick 1,426 /
Oz 1,422 in Elk).

Wrapper over the shared engines ``electionware_precinct_np`` (block
extraction, precinct prettify, office normalization) and
``electionware_primary_np`` (primary row parsing: party lives on the office
header, e.g. "DEM UNITED STATES SENATOR"). No shared-engine edits. Quirks
handled by this wrapper:

- Per-party statistics rows: the primary engine only emits RV - Total /
  BC - Total / BC - Blank; this wrapper additionally reads
  "Registered Voters/Ballots Cast - DEMOCRATIC|REPUBLICAN" into the
  2020-precedent party rows (mirrors the 2022 Huntingdon/Centre primary
  files). "NONPARTISAN" rows (BC all zero, and RV is already covered by the
  Total row's per-party breakdown context) are dropped per that precedent.
- "Write-In Totals" rows are renamed to "Write-ins" (2022-primary
  convention) and carry the contest's party.
- "Ballots Cast Blank" (engine name) is written as spec office
  "Ballots Cast - Blank".
- "REP REPRESENATIVE IN GENERAL ASSEMBLY - 75TH DISTRICT": the report
  misspells REPRESENATIVE (and splits the dash "- 75TH"); handled by an
  extra office handler -> "State House" district 75.
- "DEM ELK COUNTY DEMOCRATIC COMMITTEE MEMBER <MUNI>" (29 municipal
  sub-contests, one per precinct) -> office "Elk County Democratic
  Committee Member" with the municipality as district (same title/casing
  style as the precinct names); "REP ELK COUNTY REPUBLICAN CHAIRPERSON" ->
  office "Elk County Republican Chairperson" with empty district.
- U.S. House / State House districts are printed in the headers
  ("15TH DISTRICT" / "-75TH DISTRICT") and are extracted there.

Output schema (7-column + breakdowns):
  county,precinct,office,district,party,candidate,votes,election_day,mail,provisional

Usage:
    python parsers/pa_elk_primary_2022_results_parser.py \\
        "<input.pdf>" "<output.csv>"
"""

from __future__ import annotations

import csv
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import natural_pdf as npdf

from electionware_precinct_np import (
    SINGLE_TAIL_RE,
    extract_precinct_blocks,
    VOTE_TAIL_RE,
    parse_votes,
    prettify_huntingdon_precinct,
)
from electionware_primary_np import PrimaryConfig, parse_primary_precinct_rows

COUNTY = "Elk"

SKIP_PREFIXES = (
    "Summary Results Report",
    "General Primary 2022",
    "May 17, 2022",
    "Precinct Summary - ",
    "Report generated with Electionware",
    "Total Votes Cast",
    "Voter Turnout - ",
    "Vote For ",
    "Vote For",
    "Statistics",
    "STATISTICS",
)

# prettify_huntingdon_precinct gives the county's existing style for this
# source family (2026 primary Elk file): "Johnsonburg 1St", "Jay Twp",
# "Fox #1", "St Marys 9Th".


def _normalize_office(line: str):
    """Elk-specific headers the shared primary table doesn't cover."""
    line = re.sub(r"\s+", " ", line).strip()

    # REP header misspells the office (and puts a space around the dash).
    if line.upper().startswith("REPRESENATIVE IN GENERAL ASSEMBLY"):
        return ("State House", "75")

    # "ELK COUNTY DEMOCRATIC COMMITTEE MEMBER BENEZETTE"
    m = re.match(
        r"^ELK COUNTY (DEMOCRATIC) COMMITTEE MEMBER\s+(.+)$", line, re.IGNORECASE
    )
    if m:
        return (
            "Elk County Democratic Committee Member",
            _muni(m.group(2)),
        )

    if line.upper() == "ELK COUNTY REPUBLICAN CHAIRPERSON":
        return ("Elk County Republican Chairperson", "")

    return None


def _muni(raw: str) -> str:
    """Committee-municipality tail -> district, in the prettified-precinct
    style used for the county's other files ("St Marys 9Th", "Fox #1")."""
    return re.sub(r"\s{2,}", " ", prettify_huntingdon_precinct(raw.strip())).strip()


CONFIG = PrimaryConfig(
    county=COUNTY,
    skip_prefixes=SKIP_PREFIXES,
    county_header_suffix="Elk",
    extra_office_handlers=[_normalize_office],
    prettify_precinct=prettify_huntingdon_precinct,
)

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


def stats_rows(precinct: str, text: str) -> list[dict]:
    """Party-level Registered Voters / Ballots Cast rows (2020 precedent).

    Reads "Registered Voters - DEMOCRATIC|REPUBLICAN" (single int) and
    "Ballots Cast - DEMOCRATIC|REPUBLICAN" (4 ints) plus the Total/Blank
    rows; NONPARTISAN rows dropped per the 2022 Huntingdon/Centre primary
    precedent (BC - Nonpartisan is all zero everywhere; no nonpartisan
    contest).
    """
    def row(office, party, votes, ed="", mail="", prov=""):
        return {
            "county": COUNTY,
            "precinct": precinct,
            "office": office,
            "district": "",
            "party": party,
            "candidate": "",
            "votes": votes,
            "election_day": ed,
            "mail": mail,
            "provisional": prov,
        }

    rows: list[dict] = []
    party_by_word = {"Total": "", "DEMOCRATIC": "DEM", "REPUBLICAN": "REP", "Blank": ""}
    blank_office = {"Total": "Ballots Cast", "DEMOCRATIC": "Ballots Cast",
                    "REPUBLICAN": "Ballots Cast", "Blank": "Ballots Cast - Blank"}
    for raw in text.split("\n"):
        line = re.sub(r"\s+", " ", raw.strip()).strip()
        if not line:
            continue
        m = re.match(r"^Registered Voters - (\w+)\s+([\d,]+)$", line)
        if m and m.group(1) in party_by_word:
            rows.append(
                row(
                    "Registered Voters",
                    party_by_word[m.group(1)],
                    int(m.group(2).replace(",", "")),
                )
            )
            continue
        m = re.match(r"^Ballots Cast - (\w+)\s+(-?[\d,]+)(.*)$", line)
        if m and m.group(1) in party_by_word:
            tail = VOTE_TAIL_RE.match(line)
            if tail:
                vals = parse_votes([tail.group(i) for i in (2, 3, 4, 5)])
            else:
                vals = (int(m.group(2).replace(",", "")), "", "", "")
            rows.append(
                row(
                    "Ballots Cast" if m.group(1) != "Blank" else "Ballots Cast - Blank",
                    party_by_word[m.group(1)],
                    vals[0],
                    vals[1],
                    vals[2],
                    vals[3],
                )
            )
            continue
    return rows


def parse_pdf(pdf_path: Path) -> list[dict]:
    pdf = npdf.PDF(str(pdf_path))
    out: list[dict] = []
    for name, text in extract_precinct_blocks(pdf, CONFIG):
        pretty = re.sub(r"\s{2,}", " ", prettify_huntingdon_precinct(name)).strip()
        contest_rows = []
        for r in parse_primary_precinct_rows(pretty, text, CONFIG):
            if r["office"] in ("Registered Voters", "Ballots Cast", "Ballots Cast Blank"):
                continue  # re-read below into the ordered party layout
            if r["candidate"] == "Write-In Totals":
                r["candidate"] = "Write-ins"
            if r["candidate"] == "Glenn Gt Thompson":
                # Match the printed initials / the majority of the county's
                # sibling 2022 primary files (the generic title-caser
                # produces "Gt").
                r["candidate"] = "Glenn GT Thompson"
            # The primary engine carries the 3rd int under "absentee"
            # (its 2024-primary key); rename it to the 2022 "mail" column.
            r["mail"] = r.get("absentee", "")
            contest_rows.append(r)
        rows = stats_rows(pretty, text) + contest_rows
        out.extend(rows)
    return out


def write_csv(rows: list[dict], out_path: Path) -> None:
    with out_path.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDNAMES, extrasaction="ignore")
        writer.writeheader()
        for r in rows:
            writer.writerow(
                {k: (r.get(k, "") if r.get(k, "") is not None else "") for k in FIELDNAMES}
            )


def main() -> None:
    if len(sys.argv) != 3:
        sys.exit(
            "Usage: pa_elk_primary_2022_results_parser.py <input.pdf> <output.csv>"
        )
    pdf_path = Path(sys.argv[1])
    out_path = Path(sys.argv[2])
    if not pdf_path.exists():
        sys.exit(f"Missing PDF: {pdf_path}")
    rows = parse_pdf(pdf_path)
    write_csv(rows, out_path)
    precincts = len({r["precinct"] for r in rows})
    print(f"Wrote {len(rows)} rows across {precincts} precincts to {out_path}")


if __name__ == "__main__":
    main()