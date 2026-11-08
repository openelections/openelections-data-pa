#!/usr/bin/env python3
"""Parse Wayne County PA 2026 Primary precinct results.

Source: Wayne County Statement_of_Votes_Cast_by_Precincts_for_MAY_19_2026_GENERAL_PRIMARY*.pdf
(SOVC-by-geography; party is in parentheses on the contest line, e.g.
``GOVERNOR-D (DEMOCR) (Vote for 1)``). Candidate rows are 5-column:
total, vote%, ED, MI, PR.

Usage:
    uv run python parsers/pa_wayne_primary_2026_results_parser.py <input.pdf> <output.csv>
"""

import sys
from pathlib import Path

from sovc_geo_primary_np import (
    PrimarySovcConfig,
    parse_primary_sovc_pdf,
    write_csv,
)


# Wayne 2026's PDF omits the congressional district on "REPRESENTATIVE IN
# CONGRESS" headers. Wayne is entirely within PA-8.
DISTRICT_FIXES = {"U.S. House": "8"}


# Repo/anchor naming for the PA-8 incumbent is "Rob Bresnahan Jr" (the raw
# source line ends with "Jr."); keep the trailing period off.
CANDIDATE_FIXES = {"Rob Bresnahan Jr.": "Rob Bresnahan Jr"}


# Write-in rows from multi-slot contests (Vote for N prints N boxes) carry
# per-count ED/MI/PR tallies; sum them into one Write-In Totals row so
# (precinct, office, district, party, candidate) stays unique. Wayne's
# Member of Republican State Committee is Vote for 2 and prints two
# (mostly zero) Write-in lines per precinct.
def aggregate_writeins(rows: list[dict]) -> list[dict]:
    agg: dict[tuple, dict] = {}
    drop: set[int] = set()
    for i, r in enumerate(rows):
        if r.get("candidate") != "Write-In Totals":
            continue
        k = (r["precinct"], r["office"], r["district"], r["party"])
        if k in agg:
            base = agg[k]
            drop.add(i)
            for f in ("votes", "election_day", "provisional", "absentee"):
                base[f] = int(base[f] or 0) + int(r[f] or 0)
        else:
            agg[k] = r
    if not drop:
        return rows
    return [r for i, r in enumerate(rows) if i not in drop]


SKIP_PREFIXES = (
    "Statement of Votes Cast by Geography",
    "WAYNE COUNTY, MAY 19, 2026 GENERAL PRIMARY",
    "All Precincts, All Districts",
    "Total Ballots Cast:",
    "35 precincts reported",
    "Choice Votes Vote %",
)


CONFIG = PrimarySovcConfig(
    county="Wayne",
    skip_prefixes=SKIP_PREFIXES,
    countywide_marker="All Precincts",
    emit_registered_voters=True,
    contest_skip_prefixes=(
        "MEMBER OF REPUBLICAN COUNTY COMMITTE",
        "MEMEBER OF REPUBLICAN COUNTY COMMITTE",
        "MEMBER OF DEMOCRATIC COUNTY COMMITTE",
        "SPECIAL ELECTION",
    ),
)


if __name__ == "__main__":
    argv = sys.argv
    if len(argv) != 3:
        sys.exit(f"Usage: {Path(argv[0]).name} <input.pdf> <output.csv>")
    pdf_path = Path(argv[1])
    out_path = Path(argv[2])
    if not pdf_path.exists():
        sys.exit(f"Missing PDF: {pdf_path}")
    rows = aggregate_writeins(parse_primary_sovc_pdf(pdf_path, CONFIG))
    for r in rows:
        if not r.get("district") and r.get("office") in DISTRICT_FIXES:
            r["district"] = DISTRICT_FIXES[r["office"]]
        if r.get("candidate") in CANDIDATE_FIXES:
            r["candidate"] = CANDIDATE_FIXES[r["candidate"]]
    write_csv(rows, out_path)
    print(f"Wrote {len(rows)} rows to {out_path}")