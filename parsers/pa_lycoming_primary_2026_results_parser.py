#!/usr/bin/env python3
"""Parse Lycoming County PA 2026 Primary precinct results.

Source: Lycoming County 2026GPPrecinct.pdf (SOVC-by-geography; party in
parens on the contest line, e.g. ``Governor (Dem) (Vote for 1)``).
Candidate rows are 5-column: total, vote%, ED, MI, PR. Per-precinct
committee races (``Anthony Dem Committee (M)`` etc.) are skipped.

The engine emits one row per source ``Write-in`` line; multi-slot contests
print one Write-in line per write-in box (e.g. Member of Republican State
Committee, Vote for 3 -> three Write-in lines with distinct ED/MI/PR counts).
Those boxes are one contest's write-in pool, so they are summed into a single
``Write-In Totals`` row per (precinct, office, district, party).

Usage:
    uv run python parsers/pa_lycoming_primary_2026_results_parser.py <input.pdf> <output.csv>
"""

from sovc_geo_primary_np import PrimarySovcConfig, parse_primary_sovc_pdf, write_csv


SKIP_PREFIXES = (
    "Official Results by Precinct",
    "May 19, 2026 General Primary",
    "Lycoming County 14:29:38",
    "All Precincts, All Districts",
    "Total Ballots Cast:",
    "80 precincts reported",
    "Choice Votes Vote %",
)


import sys
from pathlib import Path

from sovc_geo_primary_np import (
    PrimarySovcConfig,
    parse_primary_sovc_pdf,
    write_csv,
)


# Write-in rows from multi-slot contests (Vote for N prints N boxes) carry
# distinct per-count ED/MI/PR tallies; sum them into one Write-In Totals row
# so (precinct, office, district, party, candidate) stays unique.
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


CONFIG = PrimarySovcConfig(
    county="Lycoming",
    skip_prefixes=SKIP_PREFIXES,
    emit_registered_voters=False,
    contest_skip_contains=(
        # Per-precinct committee races: "<Precinct> Dem Committee (M)/(F)"
        "DEM COMMITTEE",
        "REP COMMITTEE",
        "REPUBLICAN COMMITTEE",
        "DEMOCRATIC COMMITTEE",
    ),
)


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(f"Usage: {Path(sys.argv[0]).name} <input.pdf> <output.csv>")
    pdf_path = Path(sys.argv[1])
    out_path = Path(sys.argv[2])
    if not pdf_path.exists():
        sys.exit(f"Missing PDF: {pdf_path}")
    rows = aggregate_writeins(parse_primary_sovc_pdf(pdf_path, CONFIG))
    write_csv(rows, out_path)
    print(f"Wrote {len(rows)} rows to {out_path}")