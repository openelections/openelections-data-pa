#!/usr/bin/env python3
"""Parse Bradford County PA 2022 general election "Statement of Votes Cast" PDF.

Source: Bradford PA SOVC_Nov_15_2022_14_06_38FINAL.pdf (32 pages).

Layout (unlike the Bedford/Jefferson rotated-header SOVC crosstabs the text
is horizontal, so pdfplumber word extraction with x-position bucketing works
directly): four contest groups of 8 pages each; every group repeats the full
61-precinct list plus a county "Total" block at the end:

  pages  1- 8  US SENATOR                      turnout(reg,ballots,%) + reg,total + 5 candidates + write-in
  pages  9-16  GOVERNOR and LT GOVERNOR        turnout(reg,ballots) + 5 tickets + write-in
  pages 17-24  REPRESENTATIVE IN CONGRESS 9TH  reg,total + 2 candidates + write-in
               REPRESENTATIVE IN THE GENERAL   reg,total + 1 candidate + write-in
               ASSEMBLY 68TH                   (both contests side by side per page)
  pages 25-32  REPRESENTATIVE IN THE GENERAL ASSEMBLY 110TH   reg,total + 1 candidate + write-in

Each precinct block is an uppercase label line (x < 47) followed by four
sub-rows at x=50: Normal, Absentee, Mail-In, Provisional. Each contest's
value columns carry a (votes, percent) pair per candidate; the percent token
is dropped. Precincts outside a legislative district print an all-dash
placeholder section, which is skipped (Congress 9 is countywide and anchors
group 2; the House 68 section is dash for non-68 precincts, and whole rows
are dash in group 3 for non-110 precincts).

Registered Voters / Ballots Cast come from the Turnout columns of group 0
(every precinct appears there). Column mapping: Normal -> election_day,
Absentee + Mail-In -> early_voting (2022 convention), Provisional ->
provisional, votes = their sum. Write-in columns become candidate
"Write Ins" rows with an empty party. The county "Total" blocks are captured
for internal verification only (precinct sums vs printed county totals).

Usage:
    uv run python parsers/pa_bradford_general_2022_results_parser.py <input_pdf> <output_csv>
"""
from __future__ import annotations

import csv
import re
import sys

COUNTY = "Bradford"

SUBROW_LABELS = {"Normal", "Absentee", "Mail-In", "Provisional", "Total"}
SUBROWS = ("Normal", "Absentee", "Mail-In", "Provisional")

# Contest constants: (office, district, [(candidate, party), ...]) in column
# order; "Write Ins" is the trailing write-in column.
SENATE = ("U.S. Senate", "", (
    ("John Fetterman", "DEM"), ("Mehmet Oz", "REP"), ("Erik Gerhardt", "LIB"),
    ("Richard L. Weiss", "GRN"), ("Daniel Wassmer", "KEY"), ("Write Ins", ""),
))
GOVERNOR = ("Governor", "", (
    ("Josh Shapiro", "DEM"), ("Douglas V. Mastriano", "REP"),
    ("Matt Hackenburg", "LIB"), ("Christina DiGiulio", "GRN"),
    ("Joe Soloski", "KEY"), ("Write Ins", ""),
))
CONGRESS9 = ("U.S. House", "9", (
    ("Amanda R. Waldman", "DEM"), ("Dan Meuser", "REP"), ("Write Ins", ""),
))
HOUSE68 = ("State House", "68", (("Clint Owlett", "REP"), ("Write Ins", "")))
HOUSE110 = ("State House", "110", (("Tina Pickett", "REP"), ("Write Ins", "")))

# Raw token counts per group (each candidate is a (votes, percent) pair; a
# zero percent prints as "-"):
#   group 0 (Senate):  turnout reg, ballots, % | reg, total | 6 pairs
#   group 1 (Governor): turnout reg, ballots | 6 pairs
#   group 2 (Congress9 + House68): [reg, total, 3 pairs] + [reg, total, 2 pairs]
#   group 3 (House110): [reg, total, 2 pairs]
GROUP_N = {0: 17, 1: 14, 2: 14, 3: 6}

ORDINAL_RE = re.compile(r"^(\d+)(ST|ND|RD|TH)$")


def titlecase_precinct(label: str) -> str:
    out = []
    for word in label.split():
        m = ORDINAL_RE.match(word)
        if m:
            out.append(f"{m.group(1)}{m.group(2).lower()}")
        else:
            out.append(word.capitalize())
    return " ".join(out)


def to_num(tok: str) -> int:
    if tok in ("-", ""):
        return 0
    return int(tok.replace(",", ""))


def collect_pages(pdf_path):
    """Return list of (group, precinct_blocks, county_total_block) per page.

    precinct_blocks: {precinct: {subrow: [non-percent tokens]}}
    county_total_block: {subrow: [non-percent tokens]} for the county Total
    block (label "Total" at x<47), when present on the page.
    """
    import pdfplumber

    pages = []
    with pdfplumber.open(pdf_path) as pdf:
        for pi, page in enumerate(pdf.pages):
            group = pi // 8
            lines = {}
            for w in page.extract_words():
                lines.setdefault(round(w["top"]), []).append(w)

            blocks = {}
            county_total = {}
            current = None  # precinct name or "__COUNTY__"
            for top in sorted(lines):
                ws = sorted(lines[top], key=lambda w: w["x0"])
                x0 = ws[0]["x0"]
                if x0 < 47 and ws[0]["top"] > 158:
                    label = " ".join(w["text"] for w in ws)
                    if label == "Jurisdiction Wide":
                        current = None
                        continue
                    if label == "Total":
                        current = "__COUNTY__"
                        continue
                    current = titlecase_precinct(label)
                    blocks.setdefault(current, {})
                    continue
                if (47 <= x0 <= 55 and ws[0]["text"] in SUBROW_LABELS
                        and len(ws) > 1):
                    subrow = ws[0]["text"]
                    vals = [w["text"] for w in ws[1:]]
                    target = (county_total if current == "__COUNTY__"
                              else blocks.setdefault(current, {})
                              if current else None)
                    if target is None:
                        continue
                    if subrow in target:
                        raise ValueError(
                            f"page {pi+1}: duplicate sub-row {subrow!r} for "
                            f"{current!r}")
                    target[subrow] = vals
            pages.append((group, blocks, county_total))
    return pages


def votes_for(seq, group, which):
    """Extract the candidate-votes list from a raw sub-row token sequence.

    Positions: each candidate occupies a (votes, pct) pair. which: 'single'
    for groups 0/1/3, or 'first'/'second' for group 2's side-by-side
    contests. Returns None when the section is an all-dash placeholder
    (contest not applicable to the precinct).
    """
    if group == 0:
        return [to_num(seq[i]) for i in range(5, 17, 2)]
    if group == 1:
        return [to_num(seq[i]) for i in range(2, 14, 2)]
    if group == 2:
        if which == "first":
            if seq[0] == "-":
                raise ValueError("Congress 9 section all-dash")
            return [to_num(seq[i]) for i in (2, 4, 6)]
        if seq[8] == "-":
            return None
        return [to_num(seq[i]) for i in (10, 12)]
    # group 3
    if seq[0] == "-":
        return None
    return [to_num(seq[i]) for i in (2, 4)]


def parse(pdf_path, out_csv):
    pages = collect_pages(pdf_path)

    # turnout[precinct][subrow] = (reg, ballots)  -- from group 0 pages.
    turnout = {}
    # contest_data[precinct][contest][subrow] = [candidate votes]
    contest_data = {}
    # county_totals[group] = {contest: {subrow: [candidate votes]}}
    county_totals = {}

    for group, blocks, county_total in pages:
        if county_total:
            ct = {}
            for subrow, seq in county_total.items():
                if group == 0:
                    ct.setdefault(SENATE, {})[subrow] = votes_for(
                        seq, 0, "single")
                elif group == 1:
                    ct.setdefault(GOVERNOR, {})[subrow] = votes_for(
                        seq, 1, "single")
                elif group == 2:
                    ct.setdefault(CONGRESS9, {})[subrow] = votes_for(
                        seq, 2, "first")
                    h68 = votes_for(seq, 2, "second")
                    if h68 is not None:
                        ct.setdefault(HOUSE68, {})[subrow] = h68
                else:
                    v = votes_for(seq, 3, "single")
                    if v is not None:
                        ct.setdefault(HOUSE110, {})[subrow] = v
            county_totals[group] = ct

        for precinct, vals in blocks.items():
            if precinct == "__COUNTY__":
                continue
            for subrow in SUBROWS:
                if subrow not in vals:
                    raise ValueError(
                        f"page group {group}: {precinct!r} missing sub-row "
                        f"{subrow!r}")
                seq = vals[subrow]
                if len(seq) != GROUP_N[group]:
                    raise ValueError(
                        f"page group {group}: {precinct!r} {subrow!r}: "
                        f"expected {GROUP_N[group]} tokens, got {len(seq)}: "
                        f"{seq}")
                if group == 0:
                    turnout.setdefault(precinct, {})[subrow] = (
                        to_num(seq[0]), to_num(seq[1]))
                contests = ([SENATE] if group == 0
                            else [GOVERNOR] if group == 1
                            else [CONGRESS9, HOUSE68] if group == 2
                            else [HOUSE110])
                for which, contest in (("first", contests[0]),
                                       ("second", contests[1] if len(contests) > 1 else None)):
                    if contest is None:
                        continue
                    v = votes_for(seq, group, which)
                    if v is None:
                        continue
                    contest_data.setdefault(precinct, {}).setdefault(
                        contest, {})[subrow] = v

    precincts = sorted(turnout)
    print(f"Precincts found: {len(precincts)}")
    if len(precincts) != 61:
        raise ValueError(f"expected 61 precincts, got {len(precincts)}")

    rows = []
    for precinct in precincts:
        t = turnout[precinct]
        reg = t["Normal"][0]
        for sub, (r, _) in t.items():
            if r != reg:
                raise ValueError(f"{precinct}: registered-voters mismatch "
                                 f"({sub}: {r} vs Normal {reg})")
        rows.append([COUNTY, precinct, "Registered Voters", "", "", "",
                     reg, "", "", ""])
        rows.append([COUNTY, precinct, "Ballots Cast", "", "", "",
                     sum(b for _, b in t.values()),
                     t["Normal"][1],
                     t["Absentee"][1] + t["Mail-In"][1],
                     t["Provisional"][1]])

        for contest in (SENATE, GOVERNOR, CONGRESS9, HOUSE68, HOUSE110):
            per_subrow = contest_data.get(precinct, {}).get(contest)
            if not per_subrow:
                continue
            ncand = len(contest[2])
            for i, (cand, party) in enumerate(contest[2]):
                votes = [per_subrow.get(sr, [0] * ncand)[i] for sr in SUBROWS]
                rows.append([
                    COUNTY, precinct, contest[0], contest[1], party, cand,
                    sum(votes), votes[0], votes[1] + votes[2], votes[3],
                ])

    with open(out_csv, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["county", "precinct", "office", "district", "party",
                    "candidate", "votes", "election_day", "early_voting",
                    "provisional"])
        w.writerows(rows)
    print(f"Wrote {len(rows)} rows to {out_csv}")
    return rows, county_totals


def verify(rows, county_totals):
    """Compare summed precinct votes against the county Total blocks."""
    problems = []
    for contest in (SENATE, GOVERNOR, CONGRESS9, HOUSE68, HOUSE110):
        ct = county_totals.get({"U.S. Senate": 0, "Governor": 1}.get(contest[0], 2)
                               if contest[0] in ("U.S. Senate", "Governor")
                               else (2 if contest[1] in ("9", "68") else 3))
        printed = ct.get(contest, {}).get("Total") if ct else None
        if printed is None:
            problems.append(f"{contest[0]} {contest[1]}: no county Total row")
            continue
        for i, (cand, _) in enumerate(contest[2]):
            prec = [r for r in rows
                    if r[2] == contest[0] and r[3] == contest[1]
                    and r[5] == cand]
            if not prec:
                problems.append(f"{contest[0]} {contest[1]} {cand}: no rows")
                continue
            summed = sum(int(r[6]) for r in prec)
            if summed != printed[i]:
                problems.append(
                    f"{contest[0]} {contest[1]} {cand}: precinct sum {summed}"
                    f" != county Total {printed[i]}")
    return problems


def main():
    if len(sys.argv) != 3:
        print(__doc__)
        sys.exit(1)
    rows, county_totals = parse(sys.argv[1], sys.argv[2])
    problems = verify(rows, county_totals)
    if problems:
        print("VERIFY PROBLEMS:")
        for p in problems:
            print("  " + p)
    else:
        print("VERIFY: all contest precinct sums match county Total blocks")


if __name__ == "__main__":
    main()