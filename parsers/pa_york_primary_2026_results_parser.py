#!/usr/bin/env python3
"""York County 2026 primary precinct parser -- OFFICIAL refresh.

Source: "York County 2026 Primary Precinct Level Detail Report OFFICIAL.xlsx"
(2026-11-07 official vintage), a per-contest-sheet workbook (349 sheets = 27
contest sheets + 322 muni Committeeperson sheets + a "Registered Voters" page
giving per-precinct RV/Ballots Cast). Each contest sheet has: row 1 = contest
title, row 2 = candidate names (one per choice block), row 3 = repeatable
column headers (Election Day Votes, Mail-in / Absentee Votes, Provisional
Votes, Total Votes), then one data row per precinct (first cell = precinct,
next = Registered Voters, choice blocks in header order) and a final "Total:"
row (used only for cross-checking, not emitted).

Write-in semantics (verified 2026-11-07 with work2026p/york/check_wi.py):
every sheet blocks its choices as
    [ballot candidate blocks] , 'Write-in (Total)' , [individually named
    write-in blocks]
and the two groups are INDEPENDENT totals -- the sheet's per-row grand
'Total' column equals ballot blocks + 'Write-in (Total)' + the sum of the
named write-in blocks on every row of every sheet (over votes are credited to
a choice only at the countywide "Total:" row, so the sheet total row can be 1
vote higher than the block sums in a contest with over votes).

Because several named write-in blocks share names with ballot candidates (e.g.
'SCOTT PERRY' as a write-in block in the Governor-REP and State House 94-REP
sheets), emitting the named write-ins as ordinary candidate rows makes their
votes merge into a ballot candidate's key and breaks the statewide anchor
totals by a few votes. 2026-11-07 fix: per contest and per precinct, the
'Write-in (Total)' block and ALL named write-in blocks are aggregated into a
single candidate row labeled "Write-In Totals" (per AGENT_BRIEF convention;
the source's lone totals row per contest is 'Write-in (Total)').  Zero-vote
write-ins therefore vanish and no nonzero write-in vote is lost: per precinct,
ballot + Write-In Totals == the sheet row's grand Total (asserted).

Contest naming (repo style, matching the Jul 2026 vintage york precinct file):
- "Special Election - Representative in the General Assembly (District 196)"
  -> office "Special Election - State House", district 196. Party printed per
  candidate (RON RUMAN DEM, GEORGE MARGETAS REP); write-in rows carry empty
  party, as the special's write-in rows did in the prior file.
- "Governor 4 Year Term-DEM" etc. -> office Governor / Lieutenant Governor /
  U.S. House / State Senate / State House (brief normalization), party from
  the title suffix, district from "(District N)", every row carrying the
  contest party.
- "Member of the Democratic/Republican State Committee" -> "Member of
  Democratic/Republican State Committee", party DEM/REP; no district in the
  source (left empty; the statewide anchor carries
  "York County (District 67)").
- "<Muni> ... Democratic/Republican Committeeperson" sheets (322) cover a
  single precinct each and are excluded from both output files, as in the
  Jul 2026 vintage files.

The "Registered Voters" page (per-precinct RV / Ballots Cast) is read for
cross-checks but not emitted, matching the prior york precinct file (as with
lancaster and erie 2026; berks is the exception in emitting RV rows).

Output columns: county, precinct, office, district, party, candidate, votes,
election_day, provisional, absentee -- the source's combined "Mail-in /
Absentee Votes" column lands in `absentee`, as in the prior file. No
vote breakdowns beyond those are in the source.

Run: uv run python parsers/pa_york_primary_2026_results_parser.py "<xlsx>" "<out_csv>" [county "<county_csv>"]
Pass the optional word "county" and an output path to also emit the
countywide file 20260519__pa__primary__york__county.csv: one row per choice
block taken from each sheet's countywide "Total:" row (columns
county,office,district,party,candidate,votes,election_day,mail,provisional;
the combined Mail-in / Absentee column lands in `mail` here), with the same
write-in aggregation into "Write-In Totals" rows. The Committeeperson sheets
are excluded from the county file like the prior file.
"""
import csv
import re
import sys
import warnings

import openpyxl

warnings.filterwarnings("ignore")

COUNTY = "York"

SPECIAL_CAND_PARTY = {"RON RUMAN": "DEM", "GEORGE MARGETAS": "REP"}
WI_LABEL = "Write-In Totals"
ROMAN = {"I", "II", "III", "IV", "VI", "VII", "VIII", "IX", "XI", "XII",
         "XIII", "XIV", "XV", "XVI", "XVII"}


def titlecase(name):
    """Title-case an ALL-CAPS name the way the prior york file did:
    Roman numerals kept capital, Mc-prefix handled, single-letter initials
    spaced ('M.SHORB' -> 'M. Shorb'), Jr/Sr given a trailing period."""
    n = re.sub(r"(?i)\b([A-Z])\.(?=[A-Z])", r"\1. ", name)
    n = re.sub(r"\s+", " ", n).strip()
    out = []
    for w in n.split():
        if w.upper() in ROMAN:
            out.append(w.upper())
        elif len(w) > 2 and w.upper().startswith("MC"):
            out.append("Mc" + w[2:].capitalize())
        else:
            out.append(w.capitalize())
    t = " ".join(out)
    t = re.sub(r"(, )?(Jr|Sr)$", r"\1\2.", t)
    return t


def parse_contest(title):
    """Map a sheet title to (office, district, contest_party)."""
    t = title.strip()
    # ballot party from the title suffix ("... 2 Year Term-REP"), if any
    m = re.search(r"-(DEM|REP)(\s*\(|\s*$)", t)
    party = m.group(1) if m else ""
    t = re.sub(r"-(DEM|REP)(\s*\(|\s*$)", r"\2", t).strip()
    t = re.sub(r"\s*\(\s*\d+\s*\)\s*$", "", t).strip()
    m = re.search(r"\(District (\d+)\)", t)
    district = m.group(1) if m else ""
    t = re.sub(r"\s*\(District \d+\)", "", t).strip()
    if t.startswith("Special Election"):
        return ("Special Election - State House", district, "")
    if t.startswith("Governor"):
        return ("Governor", district, party or "DEM")
    if t.startswith("Lieutenant Governor"):
        return ("Lieutenant Governor", district, party or "DEM")
    if t.startswith("Representative in Congress"):
        return ("U.S. House", district, party or "DEM")
    if t.startswith("Senator in the General Assembly"):
        return ("State Senate", district, party or "DEM")
    if t.startswith("Representative in the General Assembly"):
        return ("State House", district, party or "DEM")
    if t.startswith("Member of the Democratic State Committee"):
        return ("Member of Democratic State Committee", district, "DEM")
    if t.startswith("Member of the Republican State Committee"):
        return ("Member of Republican State Committee", district, "REP")
    raise ValueError(f"Unmapped contest title: {title}")


def main(xlsx_path, out_path, county_flag=None, county_out=None):
    wb = openpyxl.load_workbook(xlsx_path, read_only=True, data_only=True)
    rows_out = []
    county_rows = []
    problems = []

    for name in wb.sheetnames:
        if not name.isdigit():
            continue
        ws = wb[name]
        ws.reset_dimensions()
        srows = [r for r in ws.iter_rows(values_only=True)]
        title = str(srows[0][0]).strip()
        if "Committeeperson" in title:
            continue  # single-precinct muni committee contests: excluded
        office, district, contest_party = parse_contest(title)

        cand = list(srows[1])
        hdr = list(srows[2] or [])
        starts = [i for i, v in enumerate(hdr) if v == "Election Day Votes"]
        blocks = [(i, cand[i]) for i in starts if cand[i] is not None]
        wi_k = [k for k, (i, c) in enumerate(blocks)
                if str(c).strip().lower() == "write-in (total)"]
        if not wi_k:
            problems.append((title, "no Write-in (Total) block"))
            continue
        k = wi_k[0]
        ballot_blocks = blocks[:k]
        wi_blocks = blocks[k:]

        # precinct data rows (skip the countywide "Total:" row)
        pr_rows = [r for r in srows[3:]
                   if r and r[0] is not None and str(r[0]).strip() != "Total:"]

        def block_cell(r, i):
            row_vals = list(r) + [None] * (len(hdr) + 1 - len(r))
            return (row_vals[i] or 0, row_vals[i + 1] or 0,
                    row_vals[i + 2] or 0, row_vals[i + 3] or 0)

        grand_idx = hdr.index("Total")

        # ballot candidate rows: one block after another, precincts within
        for i, cname in ballot_blocks:
            for r in pr_rows:
                ed, ma, prov, tot = block_cell(r, i)
                if sum((ed, ma, prov)) != tot:
                    problems.append((title, cname, str(r[0]).strip(),
                                     "block sum", (ed, ma, prov), tot))
                if office == "Special Election - State House":
                    party = SPECIAL_CAND_PARTY.get(str(cname).upper(), "")
                else:
                    party = contest_party
                rows_out.append([COUNTY, str(r[0]).strip(), office, district,
                                 party, titlecase(cname), tot, ed, prov, ma])

        # write-ins: one aggregated "Write-In Totals" row per precinct
        if office == "Special Election - State House":
            wi_party = ""
        else:
            wi_party = contest_party
        for r in pr_rows:
            bal_total = sum(block_cell(r, i)[3] for i in
                            [j for j, _c in ballot_blocks])
            wi_total = wi_ed = wi_ma = wi_prov = 0
            for i, _cname in wi_blocks:
                ed, ma, prov, tot = block_cell(r, i)
                if sum((ed, ma, prov)) != tot:
                    problems.append((title, _cname, str(r[0]).strip(),
                                     "block sum", (ed, ma, prov), tot))
                wi_total += tot
                wi_ed += ed
                wi_prov += prov
                wi_ma += ma
            grand = r[grand_idx] if len(r) > grand_idx else None
            if grand is not None and bal_total + wi_total != grand:
                problems.append((title, str(r[0]).strip(), "grand mismatch",
                                 bal_total, wi_total, grand))
            rows_out.append([COUNTY, str(r[0]).strip(), office, district,
                             wi_party, WI_LABEL, wi_total, wi_ed, wi_prov,
                             wi_ma])

        # countywide file: one row per block from the sheet's "Total:" row
        if county_out:
            tot_row = next((r for r in srows[3:]
                            if r and r[0] is not None and
                            str(r[0]).strip() == "Total:"), None)
            assert tot_row is not None, f"no Total: row on sheet {title}"
            for i, cname in ballot_blocks:
                ed, ma, prov, tot = block_cell(tot_row, i)
                if office == "Special Election - State House":
                    party = SPECIAL_CAND_PARTY.get(str(cname).upper(), "")
                else:
                    party = contest_party
                county_rows.append([COUNTY, office, district, party,
                                    titlecase(cname), tot, ed, ma, prov])
            cwi_ed = cwi_ma = cwi_prov = cwi_total = 0
            for i, _cname in wi_blocks:
                ed, ma, prov, tot = block_cell(tot_row, i)
                cwi_total += tot
                cwi_ed += ed
                cwi_ma += ma
                cwi_prov += prov
            county_rows.append([COUNTY, office, district, wi_party, WI_LABEL,
                                cwi_total, cwi_ed, cwi_ma, cwi_prov])

    with open(out_path, "w", newline="") as fh:
        w = csv.writer(fh, quoting=csv.QUOTE_MINIMAL)
        w.writerow(["county", "precinct", "office", "district", "party",
                    "candidate", "votes", "election_day", "provisional",
                    "absentee"])
        w.writerows(rows_out)
    if problems:
        print(f"PROBLEMS ({len(problems)}):")
        for p in problems[:40]:
            print(" ", p)
        sys.exit(1)
    print(f"wrote {len(rows_out)} rows -> {out_path}")
    if county_out:
        with open(county_out, "w", newline="") as fh:
            w = csv.writer(fh, quoting=csv.QUOTE_MINIMAL)
            w.writerow(["county", "office", "district", "party", "candidate",
                        "votes", "election_day", "mail", "provisional"])
            w.writerows(county_rows)
        print(f"wrote {len(county_rows)} rows -> {county_out}")


if __name__ == "__main__":
    args = sys.argv[1:] + [None] * (4 - len(sys.argv[1:]))
    main(args[0], args[1], args[2], args[3])
