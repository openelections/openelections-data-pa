#!/usr/bin/env python3
"""
Parse Philadelphia County PA 2022 General Election precinct results.

Source: "Philadelphia PA 2022 General Division Detail.xlsx" (city Results
office export). One "By_Type" sheet, one row per
(ward division, vote type E/M/P = Election Day / Mail-in / Provisional),
one column per contest candidate. Column headers embed office, district and
candidate, e.g.:

    "REPRESENTATIVE IN CONGRESS\\n2ND DISTRICT\\nBRENDAN F BOYLE DEM"
    "SENATOR IN THE GENERAL ASSEMBLY\\n4TH DISTRICT\\nART HAYWOOD DEM"
    "REPRESENTATIVE IN THE GENERAL ASSEMBLY\\n10TH DISTRICT\\nAMEN BROWN DEM"
    "COUNCIL AT-LARGE #1\\nJIM HARRITY DEM"
    "DISTRICT COUNCIL\\n7TH DISTRICT\\nQUETCY LOZADA DEM"
    "UNITED STATES SENATOR -\\nJOHN FETTERMAN DEM"
    "QUESTION #1\\nYES"

Three non-divisional summary rows ("2ND CD" / "3RD CD" / "5TH CD") close the
sheet; see the reconciliation note below.

District handling
-----------------
Each contest column carries its own district in the header's second line
("2ND DISTRICT" etc.), and that value is constant per column. It was verified
against the per-division lookup columns (Cong / St. Sen / St. House): every
nonzero vote sits in a column whose header district equals the division's
lookup value, so the two schemes agree. Districts 1, 3, 5, 7 of State Senate
appear in the lookup columns but had no 2022 contest columns (terms not up),
so only header-districted columns produce rows, and only for divisions whose
lookup value matches the header district. The statewide 2022 ballot carried
only U.S. Senate and Governor (no Attorney General / Auditor General /
State Treasurer columns in this export). Questions are keyed "Question #N"
with YES/NO candidates; local Council offices keep the printed names
("Council At-Large #N") with DISTRICT COUNCIL as "District Council" plus a
district number.

Known reconciliation residual (do NOT correct per division)
-----------------------------------------------------------
This county export is short of the state ENR (20221108__pa__general__county.csv)
Philadelphia totals for U.S. Senate AND U.S. House, and both gaps are fully
explained by three non-divisional summary rows at the bottom of the By_Type
sheet ("2ND CD" / "3RD CD" / "5TH CD", Type M: mail votes the county
aggregated per congressional district rather than assigned to a division).
Verified against ENR:
- U.S. Senate: Fetterman -595 (= 114+476+5), Oz -22 (= 9+13), Weiss -1,
  Wassmer -2, Gerhardt exact.
- U.S. House: CD-2 Boyle -99, Bashir -7 (+8 write-ins); CD-3 Evans -418,
  Hoeppner -25 (+22 write-ins); CD-5 Scanlon -2.
These CD rows are not attributed to any division, so no per-division
correction is possible: the per-division values are emitted exactly as
printed here. Every other contest (Governor exact statewide, all State
Senate / State House races exact) reconciles to ENR to the vote.

Usage:
    python parsers/pa_philadelphia_general_2022_results_parser.py \\
        "<input.xlsx>" "<output.csv>"

Emits Registered Voters (Registration column) and Ballots Cast (sum of the
E/M/P rows' Public Count) with vote-type breakdowns, plus every applicable
contest column (E+M+P summed per division).
"""

import csv
import re
import sys
from pathlib import Path

from collections import defaultdict

import openpyxl

FIELDNAMES = ["county", "precinct", "office", "district", "party",
              "candidate", "votes", "election_day", "mail", "provisional"]

TYPE_TO_GROUP = {"E": "election_day", "M": "mail", "P": "provisional"}

# Lookup-sheet column (indices into By_Type rows) used to decide whether a
# division votes in a given districted contest.
LOOKUP_FOR_OFFICE = {
    "U.S. House": 3,
    "State Senate": 4,
    "State House": 5,
    "District Council": None,  # matched on the header district of its columns
}

PARTIES = {"DEM", "REP", "LIB", "GRN", "KEY", "SWP", "IND", "OFP", "JFP",
           "LPG", "PPP", "NON", "NP", "WCP", "SP", "CST", "FWD", "ASP",
           "DAR", "TUS", "VFT", "CDB"}


def parse_header(h):
    """Header cell -> (head, district, candidate, party) or None.

    ``head`` is the office line; ``district`` is any district encoded on a
    later header line ("4TH DISTRICT" -> "4"); ``candidate`` is the trailing
    candidate/party text with the party split off when present.
    """
    parts = [p.strip() for p in str(h).split("\n") if p.strip()]
    if len(parts) < 2:
        return None
    if parts[0].upper() in ("DIVISION", "TYPE", "WARD", "CONG", "ST. SEN",
                            "ST. HOUSE", "PUBLIC COUNT", "REGISTRATION",
                            "TURNOUT"):
        return None
    if re.match(r"^\d{4}", parts[0]):  # "2023 Council"/"2019 Council" seat-history columns
        return None
    m = re.match(r"^(.*?)\s+-\s*(.+)$", parts[0].upper(), re.S)
    # district line, e.g. "2ND DISTRICT" / "170TH DISTRICT", kept out of the
    # candidate text (every nonzero vote sits in the matching lookup column)
    district, dist_line = "", None
    for p in parts[1:]:
        dm = re.match(r"^(\d+)\w*\s+DISTRICT$", p, re.I)
        if dm:
            district = str(int(dm.group(1)))
            dist_line = p
            break
    if m:
        # statewide suffix style: "UNITED STATES SENATOR -\nCAND PARTY"
        head, joined = m.group(1).strip(), " ".join(parts[1:])
    elif district:
        # districted office lines: head, then "Nth District", then candidate
        head = parts[0]
        joined = " ".join(p for p in parts[1:] if p != dist_line)
    else:
        head, joined = parts[0], " ".join(parts[1:])
    # joined = "CAND NAME PARTY" or "YES" or "CAND ... Write-in"
    cand, party = joined, ""
    pm = re.match(r"^(.*\S)\s+(" + "|".join(PARTIES) + r")$", joined, re.I)
    if pm:
        cand, party = pm.group(1).strip(), pm.group(2).upper()
    return head, district, cand, party


def office_info(head):
    """Office head -> (office, districted:bool)."""
    u = head.upper()
    if u.startswith("UNITED STATES SENATOR"):
        return "U.S. Senate", False
    if u.startswith("GOVERNOR"):
        return "Governor", False
    if u.startswith("ATTORNEY GENERAL"):
        return "Attorney General", False
    if u.startswith("AUDITOR GENERAL"):
        return "Auditor General", False
    if u.startswith("STATE TREASURER"):
        return "State Treasurer", False
    if u.startswith("REPRESENTATIVE IN CONGRESS"):
        return "U.S. House", True
    if u.startswith("SENATOR IN THE GENERAL ASSEMBLY"):
        return "State Senate", True
    if u.startswith("REPRESENTATIVE IN THE GENERAL ASSEMBLY"):
        return "State House", True
    if u.startswith("DISTRICT COUNCIL"):
        return "District Council", True
    return re.sub(r"\s+", " ", head.title()), False


def clean_candidate(cand):
    cand = cand.strip()
    if cand.lower() in ("write-in", "write in"):
        return "Write Ins"
    if cand and cand.upper() == cand and len(cand.split()) > 1:
        cand = cand.title()
    cand = re.sub(r"\s+And\s+", " / ", cand)
    return cand


def parse(path, county="Philadelphia"):
    wb = openpyxl.load_workbook(path, read_only=True)
    ws = wb["By_Type"]
    rows_iter = ws.iter_rows(values_only=True)
    headers = list(next(rows_iter))

    # one record per contest column, carrying standardized office, district,
    # candidate and party
    mapped = []  # (col, office, district, candidate, party)
    for i, h in enumerate(headers):
        if h is None:
            continue
        m = parse_header(h)
        if m is None:
            continue
        head, raw_district, cand, party = m
        office, districted = office_info(head)
        district = raw_district if districted else ""
        cand = clean_candidate(cand)
        mapped.append((i, office, district, cand, party))

    divisions = {}          # division -> {(office, district, cand, party): {group: votes}}
    groups_of = {}          # division -> {"election_day","mail","provisional"} ballots cast
    lookup = {}             # division -> [cong, st_sen, st_house]
    ward_by_div = {}

    for row in rows_iter:
        if row is None or row[0] is None:
            continue
        division = str(row[0]).strip()
        if not re.match(r"^\d+\-\d+$", division):
            continue  # summary rows like '2ND CD'
        ward = str(row[2]).strip() if row[2] is not None else ""
        ttype = str(row[1]).strip().upper()
        group = TYPE_TO_GROUP.get(ttype)
        if group is None:
            continue
        ward_by_div.setdefault(division, ward)
        # Cong / St. Sen / St. House / 2023 Council lookups (the 2023 Council
        # column is the current district-council district; a handful of
        # boundary-change divisions vote in a different district-council race)
        lk = [str(row[c]).strip() if row[c] is not None else ""
              for c in (3, 4, 5, 6)]
        if division in lookup:
            if lookup[division] != lk:
                print(f"WARN: inconsistent district lookup for {division}: "
                      f"{lookup[division]} vs {lk}")
        else:
            lookup[division] = lk
        groups_of.setdefault(division, {"election_day": 0, "mail": 0,
                                        "provisional": 0})
        public_count = int(float(row[8])) if row[8] is not None else 0
        groups_of[division][group] += public_count
        d = divisions.setdefault(division, {})
        for i, office, district, cand, party in mapped:
            v = int(float(row[i])) if i < len(row) and row[i] is not None else 0
            key = (office, district, cand, party)
            g = d.setdefault(key, {"election_day": 0, "mail": 0,
                                   "provisional": 0})
            g[group] += v

    # registration values (per division, first type row carries it)
    regs = {}
    it = wb["By_Type"].iter_rows(values_only=True)
    next(it)  # header
    for r in it:
        if r is None or r[0] is None:
            continue
        div = str(r[0]).strip()
        if not re.match(r"^\d+\-\d+$", div):
            continue
        regs.setdefault(div, int(float(r[9])) if r[9] is not None else 0)

    recs = []
    for division in sorted(divisions, key=lambda s: [int(x) for x in s.split("-")]):
        d = divisions[division]
        lk = lookup[division]  # [cong, st_sen, st_house, 2023 council]
        bc = groups_of[division]
        prec = f"Ward {int(ward_by_div[division])}-{division.split('-')[-1].lstrip('0')}"
        recs.append({
            "county": county, "precinct": prec,
            "office": "Registered Voters", "district": "", "party": "",
            "candidate": "", "votes": regs.get(division, 0),
            "election_day": "", "mail": "", "provisional": "",
        })
        recs.append({
            "county": county, "precinct": prec,
            "office": "Ballots Cast", "district": "", "party": "",
            "candidate": "", "votes": (bc["election_day"] + bc["mail"] +
                                       bc["provisional"]),
            "election_day": bc["election_day"], "mail": bc["mail"],
            "provisional": bc["provisional"],
        })
        # districted contests resolve against the division's Cong / St. Sen /
        # St. House lookup value; District Council against the 2023 Council
        # lookup (post-redistricting). A contest column applies when the
        # lookup matches its header district, or when the division actually
        # recorded votes in it (boundary-change divisions).
        for key, v in sorted(
            ((k, val) for k, val in d.items() if k != "groups"),
            key=lambda kv: (kv[0][0], int(kv[0][1]) if kv[0][1] else 0,
                            str(kv[0][2])),
        ):
            office, district, cand, party = key
            need_lk = {"U.S. House": 0, "State Senate": 1, "State House": 2,
                       "District Council": 3}.get(office)
            ed, mi, pv = v["election_day"], v["mail"], v["provisional"]
            if need_lk is not None:
                want = str(int(lk[need_lk])) if lk[need_lk] else ""
                voted = bool(ed + mi + pv)
                if want != district and not voted:
                    continue
            recs.append({
                "county": county, "precinct": prec,
                "office": office, "district": district,
                "party": party, "candidate": cand,
                "votes": ed + mi + pv, "election_day": ed,
                "mail": mi, "provisional": pv,
            })
    return recs


def main():
    if len(sys.argv) != 3:
        sys.exit(f"Usage: {Path(sys.argv[0]).name} <input.xlsx> <output.csv>")
    recs = parse(sys.argv[1])
    with open(sys.argv[2], "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDNAMES)
        w.writeheader()
        w.writerows(recs)
    print(f"Wrote {len(recs)} rows to {sys.argv[2]}")


if __name__ == "__main__":
    main()