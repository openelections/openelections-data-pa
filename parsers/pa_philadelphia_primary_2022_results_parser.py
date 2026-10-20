#!/usr/bin/env python3
"""
Parse Philadelphia County PA 2022 General Primary election division results.

Source: "Philadelphia PA 2022 Primary Division Detail.xlsx" (city Results
office export). One "By_Type" sheet, one row per
(ward division, vote method E/M/P = Election Day / Mail Votes / Provisional),
one column per contest candidate; plus a "Totals" sheet (per-division sums,
used for cross-verification, not parsing). Column headers couple office,
district, party and candidate in one string, in two shapes:

    "UNITED STATES SENATOR \nDEM - ALEX KHALIL"          (office line + "PARTY - CAND")
    "REPRESENTATIVE IN CONGRESS 2ND DISTRICT \nDEM - BRENDAN F BOYLE"
    "QUESTION #1 - YES"  /  "QUESTION #1 -\n NO"          (single-line, office - cand)
    "MEMBER OF DEMOCRATIC STATE COMMITTEE 1ST DISTRICT - GIANNI HILL (M)"
                                                          (party in office line,
                                                           (M)/(F) gender tag on cand)
    "SENATOR IN THE GENERAL ASSEMBLY 5TH DISTRICT - JIMMY DILLON"  (no party tag)
    "DISTRICT COUNCIL 6TH DISTRICT - MIKE DRISCOLL"       (no party tag)

Both shapes split apart on the first " - ": everything before it is the office
line, everything after is the candidate (with the party already broken out for
the two-line shape). Party then comes either from a trailing DEM/REP token on
the office line, or from DEMOCRATIC/REPUBLICAN inside it (State Committee).

District handling
-----------------
Office lines carry their own district ("2ND DISTRICT" etc.), constant per
column; it is emitted as the CSV district and matched against the division's
Cong / Sen / Rep lookup columns (indices 6/7/8) the way the 2022 general
parser did: a districted contest row is emitted for a division when the
lookup value matches the header district, or when the division actually
recorded votes in the column (boundary-change divisions). State Committee
rows use the Sen lookup (their districts are PA Senate districts). District
Council 6 is a separate participatory body whose boundaries are NOT the
Council lookup column (col 9 "Council" is the City Council district; the two
divergent sets were verified), so its rows are emitted only for divisions
that recorded votes (157 divisions, matching the 157/157 precincts the city's
Committeepeople export reports for the race).

Party-less special primaries
----------------------------
The State Senate 5th District special primary columns carry no party tag
(Dillon / Oropeza / Write-in). Both names are the Democratic contest's
candidates per the certified returns, so those columns are emitted as DEM;
without a party the rows would also collide with the two-party write-in
convention. District Council 6 and the four ballot questions are nonpartisan
(the city's Committeepeople export tags them "Nonpartisan") and are emitted
with an empty party.

Non-divisional summary rows ("2ND/3RD/5TH CONGRESSIONAL" per-congression-
district mail aggregates and "COUNTY TOTALS") close the sheet and are NOT
attributed to any division; see the reconciliation note below.

Known reconciliation residual (do NOT correct per division)
-----------------------------------------------------------
As in the 2022 general export, three non-divisional mail rows aggregate some
mail votes per congressional district instead of per division. Verified
against the state ENR anchor (20220517__pa__primary__county.csv): the
per-division sums fall short of ENR Philadelphia by exactly those CD rows for
the affected federal contests, and every other contest (Governor, Lieutenant
Governor, State Senate, State House, State Committee) reconciles to the ENR
to the vote. No per-division correction is possible, so per-division values
are emitted exactly as printed.

Registered Voters (Registration column) and Ballots Cast (sum of the E/M/P
rows' Public Count) are emitted per division with vote-type breakdowns.

Usage:
    python parsers/pa_philadelphia_primary_2022_results_parser.py \\
        "<input.xlsx>" "<output.csv>"
"""

import csv
import re
import sys
from pathlib import Path

import openpyxl

FIELDNAMES = ["county", "precinct", "office", "district", "party",
              "candidate", "votes", "election_day", "mail", "provisional"]

TYPE_TO_GROUP = {"Election Day": "election_day",
                 "Mail Votes": "mail",
                 "Provisional": "provisional"}

# By_Type sheet column layout (0-indexed)
COL_PUBLIC_COUNT = 2
COL_REGISTRATION = 3
COL_WARD = 5
COL_CONG = 6
COL_SEN = 7
COL_REP = 8

# lookup column for each lookup-driven office (indices into lk = [cong, sen, rep])
LOOKUP = {"U.S. House": 0, "State Senate": 1, "State House": 2}

# State Committee districts are PA Senate districts.
LOOKUP["Member of Democratic State Committee"] = 1
LOOKUP["Member of Republican State Committee"] = 1


def parse_header(h):
    """Header cell -> (head, district, candidate, party) or None.

    Splits on the first " - ": head is the office line (possibly carrying a
    trailing DEM/REP token, or DEMOCRATIC/REPUBLICAN for State Committee),
    the remainder is the candidate. ``district`` is any "NTH DISTRICT" text
    embedded in the office line.
    """
    txt = " ".join(str(h).split())  # collapse newline/space runs
    if not txt:
        return None
    fixed = {"PRECINCT", "VOTE METHOD", "PUBLIC COUNT", "REGISTRATION",
             "TURNOUT", "WARD", "CONG", "SEN", "REP", "COUNCIL"}
    if txt.upper() in fixed:
        return None
    if re.match(r"^\d{4}", txt):  # seat-history columns, if any
        return None

    head, sep, cand = txt.partition(" - ")
    if not sep:
        head, cand = txt, ""
    head = head.strip()
    district = ""
    dm = re.search(r"(\d+)\w*\s+DISTRICT", head, re.I)
    if dm:
        district = str(int(dm.group(1)))
        head = re.sub(r"\s*" + re.escape(dm.group(0)), "", head).strip()

    party = ""
    pm = re.search(r"\s+(DEM|REP)$", head)
    if pm:
        party = pm.group(1)
        head = head[:pm.start()].strip()
    if not party and re.search(r"STATE COMMITTEE", head, re.I):
        party = "DEM" if re.search(r"DEMOCRATIC", head, re.I) else \
            ("REP" if re.search(r"REPUBLICAN", head, re.I) else "")
    return head, district, cand, party


def office_info(head):
    """Office head -> standardized (office, party) applying to head itself."""
    u = head.upper()
    if "LIEUTENANT GOVERNOR" in u:
        return "Lieutenant Governor", ""
    if u == "GOVERNOR":
        return "Governor", ""
    if "STATE COMMITTEE" in u:
        if "DEMOCRATIC" in u:
            return "Member of Democratic State Committee", ""
        if "REPUBLICAN" in u:
            return "Member of Republican State Committee", ""
    if u.startswith("UNITED STATES SENATOR"):
        return "U.S. Senate", ""
    if u.startswith("REPRESENTATIVE IN CONGRESS"):
        return "U.S. House", ""
    if u.startswith("SENATOR IN THE GENERAL ASSEMBLY"):
        return "State Senate", ""
    if u.startswith("REPRESENTATIVE IN THE GENERAL ASSEMBLY"):
        return "State House", ""
    if u.startswith("DISTRICT COUNCIL"):
        return "District Council", ""
    m = re.match(r"^QUESTION #(\d+)", u)
    if m:
        return f"Question #{m.group(1)}", ""
    return re.sub(r"\s+", " ", head.title()), ""


def clean_candidate(cand, office):
    cand = re.sub(r"\s*\((M|F)\)$", "", cand.strip())  # committee gender tag
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

    mapped = []  # (col, office, district, candidate, party)
    for i, h in enumerate(headers):
        if h is None:
            continue
        m = parse_header(h)
        if m is None:
            continue
        head, district, cand, party = m
        office, _ = office_info(head)
        # State Senate 5 special primary columns carry no party tag; both
        # candidates plus the write-in column are the Democratic contest.
        if not party and office == "State Senate" and district == "5":
            party = "DEM"
        cand = clean_candidate(cand, office)
        district = district if office in ("U.S. House", "State Senate",
                                          "State House",
                                          "Member of Democratic State Committee",
                                          "Member of Republican State Committee",
                                          "District Council") else ""
        mapped.append((i, office, district, cand, party))

    divisions = {}          # division -> {(office, district, cand, party): {group: votes}}
    groups_of = {}          # division -> {"election_day","mail","provisional"} ballots cast
    lookup = {}             # division -> [cong, sen, rep]
    ward_by_div = {}

    for row in rows_iter:
        if row is None or row[0] is None:
            continue
        division = str(row[0]).strip()
        if not re.match(r"^\d+\-\d+$", division):
            continue  # summary rows: '2ND/3RD/5TH CONGRESSIONAL', 'COUNTY TOTALS'
        ward = str(row[COL_WARD]).strip() if row[COL_WARD] is not None else ""
        ttype = str(row[1]).strip()
        group = TYPE_TO_GROUP.get(ttype)
        if group is None:
            continue
        ward_by_div.setdefault(division, ward)
        lk = [str(row[c]).strip() if row[c] is not None else ""
              for c in (COL_CONG, COL_SEN, COL_REP)]
        if division in lookup:
            if lookup[division] != lk:
                print(f"WARN: inconsistent district lookup for {division}: "
                      f"{lookup[division]} vs {lk}")
        else:
            lookup[division] = lk
        groups_of.setdefault(division, {"election_day": 0, "mail": 0,
                                        "provisional": 0})
        public_count = int(float(row[COL_PUBLIC_COUNT])) \
            if row[COL_PUBLIC_COUNT] is not None else 0
        groups_of[division][group] += public_count
        d = divisions.setdefault(division, {})
        for i, office, district, cand, party in mapped:
            v = int(float(row[i])) if i < len(row) and row[i] is not None else 0
            key = (office, district, cand, party)
            g = d.setdefault(key, {"election_day": 0, "mail": 0,
                                   "provisional": 0})
            g[group] += v

    # registration values (per division, constant across type rows)
    regs = {}
    it = wb["By_Type"].iter_rows(values_only=True)
    next(it)  # header
    for r in it:
        if r is None or r[0] is None:
            continue
        div = str(r[0]).strip()
        if not re.match(r"^\d+\-\d+$", div):
            continue
        regs.setdefault(div, int(float(r[COL_REGISTRATION]))
                        if r[COL_REGISTRATION] is not None else 0)

    recs = []
    for division in sorted(divisions, key=lambda s: [int(x) for x in s.split("-")]):
        d = divisions[division]
        lk = lookup[division]  # [cong, sen, rep]
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
        # districted contests resolve against the division's Cong / Sen / Rep
        # lookup; District Council (a separate participatory body, boundaries
        # independent of the Cong/Sen/Rep and City-Council lookups) emits only
        # for divisions that recorded votes in its columns.
        for key, v in sorted(
            ((k, val) for k, val in d.items()),
            key=lambda kv: (kv[0][0], int(kv[0][1]) if kv[0][1] else 0,
                            str(kv[0][2])),
        ):
            office, district, cand, party = key
            need_lk = LOOKUP.get(office)
            ed, mi, pv = v["election_day"], v["mail"], v["provisional"]
            if need_lk is not None:
                want = str(int(lk[need_lk])) if lk[need_lk] else ""
                voted = bool(ed + mi + pv)
                if want != district and not voted:
                    continue
            elif office == "District Council" and not (ed + mi + pv):
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