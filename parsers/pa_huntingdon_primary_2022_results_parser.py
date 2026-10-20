#!/usr/bin/env python3
"""Huntingdon County, PA 2022 General Primary -- per-precinct results.

Source: "Huntingdon PA 2022 Primary Precinct Summary.pdf" (Electionware
"Summary Results Report", per-precinct, all-caps "STATISTICS" marker
variant; 58 precincts over 522 pages). Cross-check source:
"Huntingdon PA 2022 Primary Election Summary.pdf" (countywide summary,
identical 05/24/2022 vintage).

Both reports are pre-recount (05/24/2022): Huntingdon has no amended /
recount per-precinct source, so the REP U.S. Senate rows carry the
pre-recount (canvassed) values.

Wrapper over the shared engines ``electionware_precinct_np`` (block
extraction, precinct prettify) and ``electionware_primary_np`` (primary
row parsing: party lives on the office header, e.g. "DEM UNITED STATES
SENATOR"). No shared-engine edits. Quirks handled by this wrapper:

- Per-party statistics rows: the primary engine only emits RV - Total /
  BC - Total / BC - Blank; this wrapper additionally reads
  "Registered Voters/Ballots Cast - DEMOCRATIC|REPUBLICAN" (plus the
  engine's Total/Blank rows) into the 2020-precedent party rows, mirring
  the 2022 Centre primary file. "Ballots Cast - NONPARTISAN" (all zero,
  no nonpartisan contest) is dropped per the Centre precedent.
- "Write-In Totals" rows are renamed to "Write-ins" (Centre/2022-primary
  convention) and carry the contest's party; the "Write-In: ..." detail
  rows and the "Not Assigned" rows (a write-in-detail residual, already
  included in the Write-In Totals figure) are dropped.
- "Ballots Cast Blank" (engine's name) is written as spec office
  "Ballots Cast - Blank".
- "REP REPRESENTATIVE IN CONGRESS" / "SENATOR IN THE GENERAL ASSEMBLY"
  carry no district number in this report; the district is stamped from
  the DOS-certified county-level anchor (Huntingdon is entirely CD-13 /
  SD-30 in 2022; the anchor file lists only district 13 / 30).
- "MEMBER OF <PARTY> COUNTY COMMITTEE <MUNI>" local contests: office
  "Member of Democratic/Republican County Committee" with the
  municipality as district, normalized like the county's 2023 primary
  parser (Township/Borough suffix stripped; Huntingdon/Mount Union ward
  tails "HUNTINGDON BOROUGH 1ST" -> "Huntingdon 1"; slash forms mirror
  the 2023 general file).

Output schema (7-column + breakdowns):
  county,precinct,office,district,party,candidate,votes,election_day,mail,provisional

Usage:
    python parsers/pa_huntingdon_primary_2022_results_parser.py \\
        "<input.pdf>" "<output.csv>"
"""

from __future__ import annotations

import csv
import re
from pathlib import Path

import natural_pdf as npdf

from electionware_precinct_np import (
    ElectionwareConfig,
    extract_precinct_blocks,
    parse_votes,
    prettify_huntingdon_precinct,
    SINGLE_TAIL_RE,
    VOTE_TAIL_RE,
)
from electionware_primary_np import parse_primary_precinct_rows

COUNTY = "Huntingdon"

SKIP_PREFIXES = (
    "Summary Results Report",
    "2022 General Primary",
    "May 17, 2022",
    "Precinct Summary - ",
    "Report generated with Electionware",
    "Total Votes Cast",
    "Not Assigned",
    "Voter Turnout - ",
    "TOTAL ",
    "Election Day",
    "Provisional",
    "Vote For ",
)

# Trailing suffix stripped from county-committee municipality tails.
_MUNI_SUFFIX_RE = re.compile(r"\s+(TOWNSHIP|TWP|BOROUGH|BORO)$", re.I)

# Ward tails rendered like the county's 2023 general/primary files.
_WARD_TAILS = {
    "HUNTINGDON BOROUGH 1ST": "Huntingdon 1",
    "HUNTINGDON BOROUGH 2ND": "Huntingdon 2",
    "HUNTINGDON BOROUGH 3RD": "Huntingdon 3",
    "HUNTINGDON BOROUGH 4TH": "Huntingdon 4",
    "HUNTINGDON BOROUGH 5TH": "Huntingdon 5",
    "HUNTINGDON BOROUGH 6TH": "Huntingdon 6",
    "MOUNT UNION BOROUGH 1ST": "Mount Union 1",
    "MOUNT UNION BOROUGH 2ND": "Mount Union 2",
    "MOUNT UNION BOROUGH 3RD": "Mount Union 3",
    "HOPEWELL TWP/HOPEWELL": "Hopewell/hopewell",
    "HOPEWELL TWP/PUTTSTOWN": "Hopewell/puttstown",
    "SHIRLEY TWP/MOUNT UNION": "Shirley/mount Union",
    "SHIRLEY TWP/SHIRLEY": "Shirley/shirley",
    "SHIRLEY TWP/VALLEY POINT": "Shirley/valley Point",
}

COUNTY_COMMITTEE_RE = re.compile(
    r"^MEMBER OF (DEMOCRATIC|REPUBLICAN) COUNTY COMMITTEE\s+(.+)$"
)

# prettify_huntingdon_precinct capitalizes every letter run, so an
# ordinal tail becomes "1St"; lowercase the ordinal suffix to match the
# county's 2023 general file ("Huntingdon Borough 1st").
_ORDINAL_SUFFIX_RE = re.compile(r"\b(\d+)(St|Nd|Rd|Th)\b", re.IGNORECASE)


def prettify_precinct(name: str) -> str:
    return _ORDINAL_SUFFIX_RE.sub(
        lambda m: m.group(1) + m.group(2).lower(),
        prettify_huntingdon_precinct(name),
    )


def normalize_muni(raw: str) -> str:
    """Committee-municipality tail -> district (2023-parser conventions)."""
    t = re.sub(r"\s+", " ", raw.strip()).upper()
    if t in _WARD_TAILS:
        return _WARD_TAILS[t]
    if t == "BROAD TOP CITY BOROUGH":
        return "Broad Top"
    t = _MUNI_SUFFIX_RE.sub("", t)
    return " ".join(w.capitalize() for w in t.split())


def county_committee_handler(line: str):
    m = COUNTY_COMMITTEE_RE.match(line.upper())
    if m:
        party = m.group(1).capitalize()
        return (f"Member of {party} County Committee", normalize_muni(m.group(2)))
    return None


CONFIG = ElectionwareConfig(
    county=COUNTY,
    skip_prefixes=SKIP_PREFIXES,
    county_header_suffix="Huntingdon",
    extra_office_handlers=[county_committee_handler],
    prettify_precinct=prettify_precinct,
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

# The engine maps the source's 3rd/4th breakdown ints (mail, provisional)
# onto its 2024-primary keys; we rename them into the 2022-primary schema.
_STAT_KEYS = (
    ("votes", "votes"),
    ("election_day", "election_day"),
    ("absentee", "mail"),
    ("provisional", "provisional"),
)


def stats_rows(precinct: str, text: str) -> list[dict]:
    """Party-level Registered Voters / Ballots Cast rows (2020 precedent).

    Reads "Registered Voters - DEMOCRATIC|REPUBLICAN" (single int) and
    "Ballots Cast - DEMOCRATIC|REPUBLICAN" (4 ints) plus Total/Blank
    rows; nonpartisan rows (all zero, no contest) dropped per the 2022
    Centre primary precedent.
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
                    blank_office[m.group(1)],
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
        pretty = re.sub(r"\s{2,}", " ", prettify_precinct(name)).strip()
        contest_rows = []
        for r in parse_primary_precinct_rows(pretty, text, CONFIG):
            if r["candidate"] == "Not Assigned":
                # Write-in detail residual already included in the
                # contest's Write-In Totals figure.
                continue
            if r["office"] in ("Registered Voters", "Ballots Cast", "Ballots Cast Blank"):
                continue  # re-read below into the ordered party layout
            if r["candidate"] == "Write-In Totals":
                r["candidate"] = "Write-ins"
            if r["office"] == "U.S. House":
                # District not printed in this report; Huntingdon was
                # entirely in CD-13 for 2022 (per the DOS-certified
                # county-level anchor, which lists only district 13).
                r["district"] = "13"
            elif r["office"] == "State Senate":
                # Same: not printed; Huntingdon is entirely SD-30.
                r["district"] = "30"
            # The primary engine carries the 3rd int under "absentee"
            # (its 2024-primary key); rename it to the 2022 "mail" column.
            r["mail"] = r.get("absentee", "")
            contest_rows.append(r)
        rows: list[dict] = (
            stats_rows(pretty, text) + contest_rows
        )
        out.extend(rows)
    return out


def write_csv(rows: list[dict], out_path: Path) -> None:
    with out_path.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDNAMES, extrasaction="ignore")
        writer.writeheader()
        for r in rows:
            writer.writerow({k: r.get(k, "") if r.get(k, "") is not None else "" for k in FIELDNAMES})


if __name__ == "__main__":
    import sys

    if len(sys.argv) != 3:
        sys.exit(
            "Usage: pa_huntingdon_primary_2022_results_parser.py <input.pdf> <output.csv>"
        )
    pdf_path = Path(sys.argv[1])
    out_path = Path(sys.argv[2])
    rows = parse_pdf(pdf_path)
    write_csv(rows, out_path)
    precincts = len({r["precinct"] for r in rows})
    print(f"Wrote {len(rows)} rows across {precincts} precincts to {out_path}")