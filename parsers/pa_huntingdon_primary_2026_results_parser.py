#!/usr/bin/env python3
"""Huntingdon County, PA 2026 General Primary -- per-precinct results.

Source: "Huntingdon County 2026 Primary Precinct Summary.pdf" (Electionware
"Summary Results Report", per-precinct, 58 precincts over 174 pages,
report vintage 05/22/2026 10:28 AM, UNOFFICIAL). There is no countywide
summary source in the 2026 bundle, and the per-precinct PDF prints no
countywide totals section, so no ``__county.csv`` is produced.

No DOS fallbacks are used: every contest on Huntingdon's 2026 primary
ballot (Governor, Lieutenant Governor, U.S. House CD-13, State Senate
SD-30, State House HD-80/HD-81, Member of the Democratic/Republican State
Committee, per-municipality County Committee) is printed in the county PDF,
so the statewide DOS precinct-returns file
(``work2026p/dos/huntingdon__dos_precinctreturns.txt``) is not consumed.

Wrapper over the shared engines ``electionware_precinct_np`` (block
extraction, precinct prettify, statistics rows) and
``electionware_primary_np`` (primary row parsing: party lives on the
office header, e.g. "DEM GOVERNOR"). No shared-engine edits. Quirks
handled by this wrapper (pattern follows the county's 2022 primary
wrapper ``pa_huntingdon_primary_2022_results_parser.py``):

- "Not Assigned" rows are dropped: they are the write-in detail residual
  already included in each contest's "Write-In Totals" figure (verified
  in the source: e.g. WOOD / REP GOVERNOR prints GARRITY 40, Write-In
  Totals 6, Not Assigned 6).
- "Write-In Totals" rows keep that name (2026-primary convention: the
  source prints a single totals row) and carry the contest's party (the
  engine stamps the office-header party), so the duplicate_entries test
  does not collide the DEM and REP halves of the same office.
- "MEMBER OF <PARTY> COUNTY COMMITTEE <MUNI>" local contests: office
  "Member of Democratic/Republican County Committee", municipality as
  district, normalized like the county's 2022 primary file (Township/
  Borough suffix stripped, Broad Top City Borough -> "Broad Top",
  Huntingdon/Mount Union wards -> "Huntingdon 1" etc., split precincts
  "HOPEWELL_HOPEWELL" -> "Hopewell/hopewell" mirroring the 2022 slash
  forms; the 2026 report uses "_" as its split separator).
- Ballot districts are extracted from the office headers by the shared
  engine ("13th Congressional District" -> U.S. House | 13,
  "30th Senatorial District" -> State Senate | 30,
  "80th/81st DISTRICT" -> State House | 80/81).

Per-party statistics lines ("Registered Voters - DEMOCRATIC/REPUBLICAN",
"Ballots Cast - DEMOCRATIC/REPUBLICAN") are not emitted; the party-total
rows (Registered Voters - Total, Ballots Cast - Total/Blank) follow the
repo convention, as in the 2026 Snyder/Centre/Clinton primary files.

Output schema (10-column, mail votes reported in the ``absentee`` column
as in every other 2026-primary county file):
  county,precinct,office,district,party,candidate,votes,election_day,provisional,absentee

Usage:
    python parsers/pa_huntingdon_primary_2026_results_parser.py \\
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
    prettify_huntingdon_precinct,
)
from electionware_primary_np import parse_primary_precinct_rows

COUNTY = "Huntingdon"

SKIP_PREFIXES = (
    "Precinct Summary Results Report",
    "Precinct Summary - ",
    "Municipal Primary Election",
    "May 19, 2026",
    "Pennsylvania",
    "Huntingdon County",
    "Report generated with Electionware",
    "Total Votes Cast",
    "Contest Totals",
    "Voter Turnout - ",
    "TOTAL ",
    "Election",
    "Provision",
    "Mail",
    "Vote For ",
)

# Trailing suffix stripped from county-committee municipality tails.
_MUNI_SUFFIX_RE = re.compile(r"\s+(TOWNSHIP|TWP|BOROUGH|BORO)$", re.I)

# Split-precinct committee tails: the 2026 report uses "_" as the
# municipality/village separator; map them to the district forms the
# county's 2025 general file used for the same precincts.
_SPLIT_TAILS = {
    "HOPEWELL_HOPEWELL": "Hopewell/Hopewell",
    "HOPEWELL_PUTTSTOWN": "Hopewell/Puttstown",
    "SHIRLEY_MOUNT UNION": "Shirley Mount Union",
    "SHIRLEY_SHIRLEY": "Shirley/Shirley",
    "SHIRLEY_VALLEY POINT": "Shirley/Valley Point",
}

# Precinct-name finalizer: same "_" separator in the precinct banner
# names, rewritten to the 2025-general spelling.
_PRECINCT_NAME_MAP = {
    "Hopewell_Hopewell": "Hopewell/Hopewell",
    "Hopewell_Puttstown": "Hopewell/Puttstown",
    "Shirley_Mount Union": "Shirley Mount Union",
    "Shirley_Shirley": "Shirley/Shirley",
    "Shirley_Valley Point": "Shirley/Valley Point",
}

# Huntingdon's legislative districts are not pulled by the shared
# engine's ordinal-district regex when the header carries a genre word
# ("13th CONGRESSIONAL DISTRICT", "30th SENATORIAL DISTRICT"); Huntingdon
# lies entirely in CD-13 / SD-30 (per the DOS-certified countywide
# anchor, whose only district values for the county are these).
CONGRESS_DISTRICT = "13"
SENATE_DISTRICT = "30"

# Ward tails kept in the 2022 primary file's short form.
_WARD_TAILS = {
    "HUNTINGDON 1": "Huntingdon 1",
    "HUNTINGDON 2": "Huntingdon 2",
    "HUNTINGDON 3": "Huntingdon 3",
    "HUNTINGDON 4": "Huntingdon 4",
    "HUNTINGDON 5": "Huntingdon 5",
    "HUNTINGDON 6": "Huntingdon 6",
    "MOUNT UNION 1": "Mount Union 1",
    "MOUNT UNION 2": "Mount Union 2",
    "MOUNT UNION 3": "Mount Union 3",
}

COUNTY_COMMITTEE_RE = re.compile(
    r"^MEMBER OF (DEMOCRATIC|REPUBLICAN) COUNTY COMMITTEE\s+(.+)$"
)


def normalize_muni(raw: str) -> str:
    """Committee-municipality tail -> district (2022-parser conventions)."""
    t = re.sub(r"\s+", " ", raw.strip()).upper()
    if t in _SPLIT_TAILS:
        return _SPLIT_TAILS[t]
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


# prettify_huntingdon_precinct capitalizes every letter run; then the
# "_" separator spellings are mapped to the 2025-general file's forms.
def prettify_precinct(name: str) -> str:
    pretty = prettify_huntingdon_precinct(name)
    return _PRECINCT_NAME_MAP.get(pretty, pretty)


CONFIG = ElectionwareConfig(
    county=COUNTY,
    skip_prefixes=SKIP_PREFIXES,
    county_header_suffix="Huntingdon County",
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
    "provisional",
    "absentee",
]


def parse_pdf(pdf_path: Path) -> list[dict]:
    pdf = npdf.PDF(str(pdf_path))
    out: list[dict] = []
    for name, text in extract_precinct_blocks(pdf, CONFIG):
        pretty = re.sub(r"\s{2,}", " ", prettify_precinct(name)).strip()
        for r in parse_primary_precinct_rows(pretty, text, CONFIG):
            if r["candidate"] == "Not Assigned":
                # Write-in detail residual already included in the
                # contest's Write-In Totals figure.
                continue
            if r["office"] == "U.S. House":
                # District not extracted by the engine's ordinal regex in
                # this report; Huntingdon is entirely CD-13.
                r["district"] = CONGRESS_DISTRICT
            elif r["office"] == "State Senate":
                # Same: Huntingdon is entirely SD-30.
                r["district"] = SENATE_DISTRICT
            out.append(r)
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
            "Usage: pa_huntingdon_primary_2026_results_parser.py <input.pdf> <output.csv>"
        )
    pdf_path = Path(sys.argv[1])
    out_path = Path(sys.argv[2])
    rows = parse_pdf(pdf_path)
    write_csv(rows, out_path)
    precincts = len({r["precinct"] for r in rows})
    print(f"Wrote {len(rows)} rows across {precincts} precincts to {out_path}")
