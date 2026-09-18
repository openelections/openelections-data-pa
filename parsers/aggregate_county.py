#!/usr/bin/env python3
"""Aggregate an OpenElections precinct-level CSV into a county-level CSV.

County-level rows are the sum of precinct rows over the same
(county, office, district, party, candidate) key.  This mirrors how the
2025 county-level files were produced (verified: 854/854 keys identical
for Berks 2025).

Usage: python agg_county.py <precinct.csv> <county.csv>
"""
from __future__ import annotations
import csv, sys
from collections import OrderedDict

FIELDNAMES = ["county", "office", "district", "party", "candidate",
              "votes", "election_day", "mail", "provisional"]
BREAKDOWNS = ["votes", "election_day", "mail", "provisional", "absentee", "military"]


def aggregate(precinct_path: str, county_path: str) -> int:
    agg: "OrderedDict[tuple, dict]" = OrderedDict()
    with open(precinct_path, newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            key = (r["county"], r["office"], r.get("district") or "",
                   r.get("party") or "", r["candidate"])
            row = agg.get(key)
            if row is None:
                row = agg[key] = dict.fromkeys(BREAKDOWNS, 0)
            for col in BREAKDOWNS:
                v = r.get(col)
                if v not in (None, ""):
                    row[col] += int(v)
    with open(county_path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(FIELDNAMES)
        for (county, office, district, party, candidate), row in agg.items():
            w.writerow([county, office, district, party, candidate,
                        row["votes"], row["election_day"], row["mail"], row["provisional"]])
    return len(agg)


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(f"Usage: {sys.argv[0]} <precinct.csv> <county.csv>")
    n = aggregate(sys.argv[1], sys.argv[2])
    print(f"aggregated {n} county-level rows -> {sys.argv[2]}")
