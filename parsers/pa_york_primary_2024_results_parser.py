#!/usr/bin/env python
"""Parser for York County 2024 PA primary precinct detail results.

Source: 'York PA 2024 Primary Precinct Detail.xlsx' (county precinct-detail
SOVC export; one sheet per contest). Cross-checked against the paired
countywide summary CSV.

Contest sheet layout (sheets named '2'..'37'):
  row 1: contest title in col A, e.g.
         'Representative In The General Assembly (District 47) 2 Year Term-DEM ( 1)'
  row 2: candidate names, one above each 4-column vote block (blocks hold
         Election Day / Mail-in + Absentee / Provisional / Total votes);
         column order is ballot candidates first, then exactly one
         'Write-In (Total)' block, then one block per individual write-in
         candidate name (the source's name-level breakdown of that same
         total)
  row 3: headers repeating per block
  rows 4..: one row per precinct: Precinct (col A), Registered Voters
         (col B), then the vote blocks; the column after the last block is a
         per-precinct contest Total (candidates + over-votes)
  last row: 'Total:' countywide totals

The 'Registered Voters' sheet carries per-precinct Registered Voters and
Ballots Cast.

Output: county,precinct,office,district,party,candidate,votes,election_day,
mail,provisional. Ballot candidates are emitted as printed; the
'Write-In (Total)' block becomes one 'Write-In Totals' row per precinct
(repo-wide 2024 primary convention); individual write-in name blocks are not
emitted (the source's Write-In (Total) block is a larger audited total that
includes unnamed write-in votes; the parser only checks it is >= their sum).

Usage:
    uv run python parsers/pa_york_primary_2024_results_parser.py \
        "<Precinct Detail.xlsx>" "<Countywide Summary.csv>" <output.csv>
"""

import csv
import re
import sys

import openpyxl

COUNTY = "York"
WRITE_IN_NAME = "Write-In Totals"
FIRST_SHEET, LAST_SHEET = 2, 37

OFFICE_PATS = [
    (re.compile(r"^President of the United States"), "President"),
    (re.compile(r"^United States Senator"), "U.S. Senate"),
    (re.compile(r"^Attorney General"), "Attorney General"),
    (re.compile(r"^Auditor General"), "Auditor General"),
    (re.compile(r"^State Treasurer"), "State Treasurer"),
    (re.compile(r"^Representative In Congress \((\d+)(?:st|nd|rd|th) Congressional District\)"),
     "U.S. House"),
    (re.compile(r"^Senator In The General Assembly \(District (\d+)\)"), "State Senate"),
    (re.compile(r"^Representative In The General Assembly \(District (\d+)\)"), "State House"),
    (re.compile(r"^Alternate Delegate To The (Democratic) National Convention "
                r"\((\d+)(?:st|nd|rd|th) Congressional District\)"),
     "Alternate Delegate to the Democratic National Convention"),
    (re.compile(r"^Alternate Delegate To The (Republican) National Convention "
                r"\((\d+)(?:st|nd|rd|th) Congressional District\)"),
     "Alternate Delegate to the Republican National Convention"),
    (re.compile(r"^Delegate To The (Democratic) National Convention "
                r"\((\d+)(?:st|nd|rd|th) Congressional District\)"),
     "Delegate to the Democratic National Convention"),
    (re.compile(r"^Delegate To The (Republican) National Convention "
                r"\((\d+)(?:st|nd|rd|th) Congressional District\)"),
     "Delegate to the Republican National Convention"),
]


def parse_title(title):
    """Return (office, district, party) from a contest title."""
    m = re.search(r"-(DEM|REP)\s*\(\s*(?:Vote For\s*)?\d+\s*\)\s*$", title)
    party = m.group(1) if m else ""
    base = title[: m.start()] if m else title
    for pat, office in OFFICE_PATS:
        dm = pat.match(base)
        if dm:
            groups = dm.groups()
            if len(groups) == 2:      # delegate contests: (party word, district)
                district = groups[1]
                party = party or ("DEM" if "Democratic" in base else "REP")
            elif len(groups) == 1:    # districted offices: (district,)
                district = groups[0]
            else:
                district = ""
            return office, district, party
    raise ValueError("Unrecognized contest title: %r" % title)


def read_registered_voters(wb):
    """Return list of (precinct, registered_voters, ballots_cast)."""
    ws = wb["Registered Voters"]
    out = []
    for i, row in enumerate(ws.iter_rows(values_only=True)):
        if i == 0 or row[0] is None:
            continue
        name = str(row[0]).strip()
        if name.startswith("Total"):
            break
        out.append((name, int(row[1]), int(row[2])))
    return out


def clean_name(name):
    return re.sub(r"\s+", " ", name).strip()


def parse_contest_sheet(ws):
    """Parse one contest sheet.

    Returns (title, office, district, party, cand_cols, precincts, total_row)
    where cand_cols is an ordered list of (col_index, candidate_name) and
    precincts are (precinct_name, registered_voters,
    [ (election_day, mail, provisional, total) per cand_cols entry ],
    contest_block_total).
    """
    rows = list(ws.iter_rows(values_only=True))
    title = str(rows[0][0]).strip()
    office, district, party = parse_title(title)

    # candidate names line up with the header row by column index
    cands_by_col = {}
    for i, v in enumerate(rows[1]):
        if v is not None:
            cands_by_col[i] = clean_name(str(v))
    header_by_col = {i: str(v).strip() for i, v in enumerate(rows[2])
                     if v is not None}
    cand_cols = []
    for col in sorted(cands_by_col):
        if header_by_col.get(col, "").startswith("Election Day"):
            cand_cols.append((col, cands_by_col[col]))
        else:
            raise ValueError("%s: candidate %r at column %d has no vote block"
                             % (title, cands_by_col[col], col))
    if not cand_cols:
        raise ValueError("%s: no candidate columns found" % title)

    wi_cols = [col for _c, name in cand_cols if name == "Write-In (Total)"]
    if len(wi_cols) != 1:
        raise ValueError("%s: expected exactly one 'Write-In (Total)' column, "
                         "found %d" % (title, len(wi_cols)))

    precincts = []
    total_row = None
    for row in rows[3:]:
        if row[0] is None:
            continue
        name = str(row[0]).strip()
        if name.startswith("Total"):
            total_row = row
            break
        rv = int(row[1])
        blocks = []
        for col, _cand in cand_cols:
            vals = row[col:col + 4]
            if any(v is None for v in vals):
                raise ValueError("%s / %s: blank vote cell in column %d"
                                 % (title, name, col))
            blocks.append(tuple(int(v) for v in vals))
        contest_total_col = cand_cols[-1][0] + 4
        block_total = int(row[contest_total_col])
        if (contest_total_col + 1) < len(row) and \
                row[contest_total_col + 1] is not None:
            raise ValueError("%s / %s: unexpected data after contest Total "
                             "column" % (title, name))
        precincts.append((name, rv, blocks, block_total))
    if total_row is None:
        raise ValueError("%s: no 'Total:' row found" % title)
    return title, office, district, party, cand_cols, precincts, total_row


def main():
    if len(sys.argv) != 4:
        sys.exit(__doc__)
    xlsx_path, summary_path, out_path = sys.argv[1:4]

    wb = openpyxl.load_workbook(xlsx_path, data_only=True)

    # Countywide summary: contest key -> {choice name: (party, total votes)}
    summary = {}
    with open(summary_path, newline="", encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            key = parse_title(r["contest name"])
            summary.setdefault(key, {})[clean_name(r["choice name"])] = \
                (r["party name"].strip(), int(r["total votes"]))

    rows_out = []
    problems = []

    for sheet_name in [str(i) for i in range(FIRST_SHEET, LAST_SHEET + 1)]:
        (title, office, district, party, cand_cols, precincts,
         total_row) = parse_contest_sheet(wb[sheet_name])

        # -- internal check: precinct sums vs the sheet's own 'Total:' row --
        col_sums = {}
        for j, (col, cand) in enumerate(cand_cols):
            for k, fld in enumerate(("election_day", "mail", "provisional",
                                     "total")):
                col_sums[(cand, fld)] = sum(p[2][j][k] for p in precincts)
                sheet_tot = int(total_row[col + k])
                if col_sums[(cand, fld)] != sheet_tot:
                    problems.append(
                        "%s / %s / %s: precinct sum %d != sheet 'Total:' %d"
                        % (title, cand, fld, col_sums[(cand, fld)],
                           sheet_tot))
        over = [p[3] - sum(b[3] for b in p[2]) for p in precincts]
        for precinct, over_amt in zip(precincts, over):
            if over_amt != 0:
                problems.append(
                    "%s / %s: contest Total column != sum of candidate "
                    "blocks (diff %d)" % (title, precinct[0], over_amt))

        # -- cross-check vs the countywide summary CSV ----------------------
        skey = (office, district, party)
        if skey not in summary:
            problems.append("%s: no matching contest in summary" % (skey,))
        else:
            s = summary[skey]
            wi_idx = next(i for i, (_c, name) in enumerate(cand_cols)
                          if name == "Write-In (Total)")
            named = [clean_name(name) for _c, name in cand_cols[:wi_idx]]
            s_named = [c for c, (p, _v) in s.items()
                       if p == party and c != "Write-In (Total)"]
            if s_named != named:
                problems.append(
                    "%s: ballot candidates xlsx=%r vs summary=%r"
                    % (skey, named, s_named))
            for cand in named:
                summed = col_sums[(cand, "total")]
                s_tot = s.get(cand, (None, None))[1]
                if s_tot != summed:
                    problems.append(
                        "%s / %s: precinct sum %d != summary total %s"
                        % (skey, cand, summed, s_tot))
            wi_cand = cand_cols[wi_idx][1]
            summed_wi = col_sums[(wi_cand, "total")]
            s_wi = s.get(wi_cand, (None, None))[1]
            if summed_wi != s_wi:
                problems.append(
                    "%s: write-in precinct sum %d != summary total %s"
                    % (skey, summed_wi, s_wi))

        # -- emit rows -------------------------------------------------------
        wi_idx = next(i for i, (_c, name) in enumerate(cand_cols)
                      if name == "Write-In (Total)")
        for name, _rv, blocks, _block_total in precincts:
            for j, (_col, cand) in enumerate(cand_cols[:wi_idx]):
                ed, mail, prov, tot = blocks[j]
                rows_out.append([COUNTY, name, office, district, party, cand,
                                 tot, ed, mail, prov])
            wi_ed = wi_mail = wi_prov = wi_tot = 0
            for i in range(wi_idx + 1, len(cand_cols)):
                ed, mail, prov, tot = blocks[i]
                wi_ed += ed
                wi_mail += mail
                wi_prov += prov
                wi_tot += tot
            ed, mail, prov, tot = blocks[wi_idx]
            if tot < wi_tot:
                problems.append(
                    "%s / %s: Write-In (Total) block (%d) < summed "
                    "individual write-in blocks (%d)"
                    % (title, name, tot, wi_tot))
            rows_out.append([COUNTY, name, office, district, party,
                             WRITE_IN_NAME, tot, ed, mail, prov])

    # Registered Voters / Ballots Cast rows
    rv_rows = []
    for precinct, rv, bc in read_registered_voters(wb):
        rv_rows.append([COUNTY, precinct, "Registered Voters", "", "", "", rv,
                        "", "", ""])
        rv_rows.append([COUNTY, precinct, "Ballots Cast", "", "", "", bc,
                        "", "", ""])

    with open(out_path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["county", "precinct", "office", "district", "party",
                    "candidate", "votes", "election_day", "mail",
                    "provisional"])
        w.writerows(rows_out)
        w.writerows(rv_rows)

    print("rows written: %d (+%d RV/BC rows) -> %s"
          % (len(rows_out), len(rv_rows), out_path))
    if problems:
        print("PROBLEMS:")
        for p in problems:
            print("  " + p)
        sys.exit(1)


if __name__ == "__main__":
    main()
