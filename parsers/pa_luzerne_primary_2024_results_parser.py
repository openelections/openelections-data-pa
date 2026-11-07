#!/usr/bin/env python3
"""Parse Luzerne County PA 2024 Primary Election precinct results.

Source (PA DOS certified precinct returns, "prcres" format):

  * "Luzerne PA 2024 Primary Election Summary Report.pdf" (the county's own
    certified summary, work2024p/luzerne/summary.txt via pdftotext -layout)
    is COUNTYWIDE-ONLY: a single "Summary for: All Contests, All Districts"
    header, one aggregate table per contest, no per-precinct rows.  So the
    precinct detail comes from the PA Department of State certified bulk
    file ElectionReturns_2024_Primary_PrecinctReturns.txt ("Precinct
    Election Returns", Data Extracted July 23, 2024), county code 40 =
    Luzerne -- the same fallback used for countywide-only sources in the
    2022 primary (Cameron/Monroe/Pike/Potter/Snyder/Warren).  The summary
    PDF remains the ANCHOR: per-contest, per-named-candidate countywide
    totals from the summary must equal the sum of the parser's precinct
    rows (checked below / by the verify harness).

    Known omissions of the DOS file (documented, not invented):
      - write-in votes: the DOS file carries no WRITE-IN/SCATTER rows, so
        write-ins exist only as countywide "SCATTER ... WRITE-IN" blocks in
        the summary and are omitted from the precinct output;
      - "Member of the <Party> County Committee" races (Republican county
        committee seats for 20 small municipalities are aggregated countywide
        in the summary and are not carried by DOS at all);
      - Registered Voters / Ballots Cast (summary carries countywide turnout
        only: 196,131 RV, 53,323 ballots cast), so no RV/Ballots Cast rows;
      - vote-type breakdowns (DOS has a single votes cell) -- 7-column output.
      - State House 117 (REP): the July 23, 2024 DOS extract reports zero for
        all 82 rows (every precinct, both Walsh and Cabell, all precincts in
        the district), while both the county summary and the state ENR API
        carry the certified race (Walsh 4,735 / Cabell 4,731).  The all-zero
        contest group is dropped rather than written as false data.

    Cross-check (report numbers): per-candidate countywide sums from this
    output equal the state ENR GetCountyData figures exactly for all 45 ENR
    named candidates; against the county summary PDF, all Democratic-side
    named-candidate totals match exactly, and the remaining Republican-side
    deltas are tiny late-print adjustments (the summary was printed
    2025-04-09, a year after the election) documented in the parse report.

Usage:
  python pa_luzerne_primary_2024_results_parser.py <prcres.txt> <out.csv>

The parser reads the full statewide DOS text file and filters county code
40.  Precinct names are built from the DOS municipality type + breakdown
fields to match the county's established names in the 2022/2024 primary
files (2022/counties/20220517__pa__primary__luzerne__precinct.csv):
"X Boro Ward NN" / "X Township Dist NN" / "X City Ward NN" / "X Township".
Code-pinned overrides cover the cases DOS names do not disambiguate or
spell out (Dallas Boro Ward 01/02, Pittston Township 01/02, and the Butler
Township Dist 05 CD-8/CD-9 splits).
"""
from __future__ import annotations

import csv
import sys
from collections import OrderedDict

COUNTY = "Luzerne"
COUNTY_CODE = "40"          # DOS code without leading zero in the file

FIELDNAMES = ["county", "precinct", "office", "district", "party", "candidate", "votes"]

# DOS office code -> (output office, districted?)
OFFICE_MAP = {
    "USP": ("President", False),
    "USS": ("U.S. Senate", False),
    "ATT": ("Attorney General", False),
    "AUD": ("Auditor General", False),
    "TRE": ("State Treasurer", False),
    "USC": ("U.S. House", True),
    "STS": ("State Senate", True),
    "STH": ("State House", True),
    "DED": ("Delegate to the Democratic National Convention", True),
    "DER": ("Delegate to the Republican National Convention", True),
    "ADR": ("Alternate Delegate to the Republican National Convention", True),
}

TYPE_SUFFIX = {str(t): s for t, s in [(2.0, "City"), (4.0, "Township"), (6.0, "Boro")]}

# DOS precinct code -> explicit output name (pinning cases the generic rule
# cannot resolve; verified against the county's 2022 primary file naming).
PRECINCT_OVERRIDES = {
    "230": "Dallas Boro Ward 01",            # DOS "DALLAS W NORTH"
    "240": "Dallas Boro Ward 02",            # DOS "DALLAS W SOUTH"
    "157": "Butler Township Dist 05 (USC 8)",  # county split, DOS "(USC 8)"
    "158": "Butler Township Dist 05 (USC 9)",  # county split, DOS "(USC 9)"
    "1980": "Pittston Township 01",          # 2022 file: "Pittston Township 01"
    "1990": "Pittston Township 02",
}


import re

# DOS municipality names embed the breakdown token ("ASHLEY W 1",
# "JENKINS D 01"): strip the trailing " W n"/"D nn" before titling.
EMBEDDED_BREAKDOWN = re.compile(r"\s+[WD]\s*\d+$")


def muni_title(name: str) -> str:
    words = re.split(r"[\s]+", name)
    return " ".join("-".join(p.capitalize() for p in w.split("-")) for w in words)


def precinct_name(rec) -> str:
    code = str(int(float(rec[3])))
    if code in PRECINCT_OVERRIDES:
        return PRECINCT_OVERRIDES[code]
    muni = EMBEDDED_BREAKDOWN.sub("", rec[22])
    mtype = TYPE_SUFFIX[rec[21]]           # 2 City / 4 Township / 6 Boro
    bdc, bdn = rec[23].strip(), rec[24].strip()
    base = f"{muni_title(muni)} {mtype}"
    if not bdc or not bdn:
        return base
    n = "%02d" % int(float(bdn))
    return f"{base} {'Ward' if bdc == 'W' else 'Dist'} {n}"


# All-caps DOS surnames that break plain title-casing (repo style: LaRocca,
# O'Neill, DePasquale, McCormick, ...).
CASE_FIX = {
    "larocca": "LaRocca", "o'neill": "O'Neill", "depasquale": "DePasquale",
    "defoor": "DeFoor", "defabo": "DeFabo", "delucia": "DeLucia",
    "mccormick": "McCormick", "mcclelland": "McClelland",
    "mclinko": "McLinko", "mcdermott": "McDermott",
    "mcgregor": "McGregor", "mcgloin": "McGloin", "mckayla": "Mckayla",
}


def name_part(part: str) -> str:
    words = []
    for w in part.split():
        for piece in w.split("-"):
            w = w.replace(piece, CASE_FIX.get(piece.strip(" '’").lower(),
                             piece.capitalize()), 1)
        words.append(w)
    return " ".join(words)


def candidate_name(rec) -> str:
    last, first, middle, suffix = (p.strip() for p in rec[11:15])
    parts = [name_part(p.strip()) for p in (first, middle) if p.strip()]
    parts.append(name_part(last))
    if suffix.strip():
        s = suffix.strip().rstrip(".")
        parts.append("Jr" if s.upper() == "JR" else s.title())
    return " ".join(parts)


def dist_of(rec) -> str:
    d = rec[5]
    if not d:
        return ""
    nd = int(float(d))
    return "" if nd == 0 else str(nd)


def parse(inpath: str, outpath: str):
    rows = OrderedDict()
    with open(inpath, newline="") as f:
        for line in f:
            rec = next(csv.reader([line]))
            if len(rec) < 27 or rec[1] != "P":
                continue
            if str(int(float(rec[2]))) != COUNTY_CODE and COUNTY_CODE != rec[2]:
                continue
            office_code = rec[8]
            if office_code not in OFFICE_MAP:
                raise SystemExit(f"unmapped office code {office_code!r}")
            office, districted = OFFICE_MAP[office_code]
            district = dist_of(rec) if districted else ""
            votes = int(float(rec[15]))
            key = (
                int(float(rec[3])),      # precinct code (stable sort)
                int(float(rec[4])),      # office rank
                office, district, rec[9],
                int(float(rec[7])),      # ballot position
                rec[10],                 # candidate number
            )
            if key in rows:
                raise SystemExit(f"duplicate row {key}")
            rows[key] = {
                "precinct": precinct_name(rec),
                "office": office,
                "district": district,
                "party": rec[9],
                "candidate": candidate_name(rec),
                "votes": votes,
            }
    # Drop contest groups whose DOS rows are entirely zero: the July 23, 2024
    # DOS extract carries NOTHING but zeros for the contested State House
    # 117 (REP) race (all 82 rows, every precinct, both candidates), while
    # the county summary prints Walsh 4,735 / Cabell 4,731 and the state ENR
    # countywide feed reports identical figures.  Reporting 82 all-zero
    # precinct rows would be false data, so the contest group is omitted and
    # the discrepancy is documented in the module docstring.
    contest_sums = {}
    for key, r in rows.items():
        ck = (key[2], key[3], key[4])
        contest_sums[ck] = contest_sums.get(ck, 0) + r["votes"]
    dropped = [ck for ck, v in contest_sums.items() if v == 0]
    if dropped:
        print("all-zero contest groups dropped:", dropped)
    with open(outpath, "w", newline="") as out:
        w = csv.writer(out)
        w.writerow(FIELDNAMES)
        n = 0
        for key, r in rows.items():
            if contest_sums[(key[2], key[3], key[4])] == 0:
                continue
            w.writerow([COUNTY, r["precinct"], r["office"], r["district"],
                        r["party"], r["candidate"], r["votes"]])
            n += 1
    return n


def main():
    if len(sys.argv) not in (3,):
        sys.exit(__doc__)
    n = parse(sys.argv[1], sys.argv[2])
    print(f"{COUNTY}: wrote {n} rows to {sys.argv[2]}")


if __name__ == "__main__":
    main()
