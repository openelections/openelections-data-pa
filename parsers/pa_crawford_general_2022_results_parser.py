#!/usr/bin/env python3
"""Parser for Crawford County 2022 General Election - Official Computations of
Votes Cast (Federal and State Offices).

Source: "Crawford PA 2022_General_Federal_and_State.pdf" - a 16-page
"COMPUTING PAPER / OFFICIAL RETURNS" crosstab (one section of 1-2 landscape
pages per contest).  Each contest page is a grid: precinct rows x candidate
column groups, where every candidate has three rotated sub-column labels
(Machine / Hand / Total) and data cells are printed only when non-zero (the
Total cell is always printed).  The PDF is text-based, so the parser works
from pdfplumber word coordinates rather than OCR:

  * office title  - upright words near the top of the page
  * column labels - rotated (90-degree) words 'Machine'/'Hand'/'Total' whose
    x0 positions define the 36 numeric columns (12 groups of 3)
  * candidates    - rotated header words above the labels, assigned to their
    nearest column group and validated against the expected surnames
  * data rows     - upright words clustered by 'top' (float drift within a
    row is handled); leading non-numeric words left of the grid are the
    precinct name; trailing rows ('Page Totals', 'Totals from Page 1',
    'Grand Total') are used for verification, not output

Machine/Hand/Total semantics (confirmed against the ENR-derived county file:
e.g. U.S. Senate Fetterman Machine 10961 + Hand 120 = Total 11081, and ENR
reports election_day 7051 + mail 3910 = 10961, provisional 120):

    votes        = Total
    election_day = Machine   (machine-counted ballots)
    provisional  = Hand      (hand-counted paper ballots)
    early_voting = ""        (source does not separate absentee/mail)

Candidate display names are top-of-ticket names as printed.  Write-in rows
(the SCATTERED column) use candidate='Write Ins', party="".

Usage:
    python parsers/pa_crawford_general_2022_results_parser.py <input_pdf> <output_csv>
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
from collections import OrderedDict

import pdfplumber

FIELDNAMES = ["county", "precinct", "office", "district", "party", "candidate",
              "votes", "election_day", "early_voting", "provisional"]

# Contests in column order per page: (display name, party, expected surname).
# The surname check validates that columns were read in the right order.
SENATE_CANDS = [
    ("John Fetterman", "DEM", "FETTERMAN"),
    ("Mehmet Oz", "REP", "OZ"),
    ("Erik Gerhardt", "LIB", "GERHARDT"),
    ("Richard L Weiss", "GRN", "WEISS"),
    ("Daniel Wassmer", "KEY", "WASSMER"),
    ("Write Ins", "", "SCATTERED"),
]
GOV_CANDS = [
    ("Josh Shapiro", "DEM", "SHAPIRO"),
    ("Douglas V Mastriano", "REP", "MASTRIANO"),
    ("Matt Hackenburg", "LIB", "HACKENBURG"),
    ("Christina Digiulio", "GRN", "DIGIULIO"),
    ("Joe Soloski", "KEY", "SOLOSKI"),
    ("Write Ins", "", "SCATTERED"),
]
STATE_HOUSE = {
    "6": [("Nerissa Galt", "DEM", "GALT"), ("Brad Roae", "REP", "ROAE"),
          ("Write Ins", "", "SCATTERED")],
    "64": [("R Lee James", "REP", "JAMES"), ("Write Ins", "", "SCATTERED")],
    "65": [("Kathy Rapp", "REP", "RAPP"), ("Write Ins", "", "SCATTERED")],
}
US_HOUSE_16 = [("Dan Pastore", "DEM", "PASTORE"), ("Mike Kelly", "REP", "KELLY"),
               ("Write Ins", "", "SCATTERED")]
ST_SENATE_50 = [("Rianna Czech", "DEM", "CZECH"), ("Michele Brooks", "REP", "BROOKS"),
                ("Write Ins", "", "SCATTERED")]


def match_office(title: str):
    """Return (office, district, candidates) for a page title, or None."""
    up = title.upper()
    if "GOVERNOR & LIEUTENANT GOVERNOR" in up:
        return ("Governor", "", GOV_CANDS)
    if "US SENATOR" in up:
        return ("U.S. Senate", "", SENATE_CANDS)
    m = re.search(r"(\d+)TH CONGRESSIONAL", up)
    if "UNITED STATES REPRESENTATIVE" in up and m:
        return ("U.S. House", m.group(1), US_HOUSE_16)
    m = re.search(r"(\d+)TH SENATORIAL", up)
    if "SENATOR IN THE GENERAL ASSEMBLY" in up and m:
        return ("State Senate", m.group(1), ST_SENATE_50)
    m = re.search(r"(\d+)TH LEGISLATIVE", up)
    if "REPRESENTATIVE IN THE GENERAL ASSEMBLY" in up and m:
        cand = STATE_HOUSE.get(m.group(1))
        if cand is None:
            raise ValueError(f"unknown State House district {m.group(1)!r}")
        return ("State House", m.group(1), cand)
    return None


def nearest_anchor(center, anchors):
    kind, ax = min(anchors, key=lambda a: abs(center - a[1]))
    if abs(center - ax) > 10:
        return None
    return anchors.index((kind, ax))


def parse_page(page, page_no):
    """Parse one contest page.

    Returns (office, district, candidates, rows, totals_rows, col_fn) where
    rows is a list of (precinct, {candidate_idx: (machine, hand, total)}) and
    totals_rows maps printed totals labels to [(word, value), ...].  col_fn
    maps a totals-row word to its column index.
    """
    words = page.extract_words()
    upright = [w for w in words if w.get("upright", True)]
    rotated = [w for w in words if not w.get("upright", True)]

    title_words = [w["text"] for w in sorted(
        (w for w in upright if w["top"] < 60), key=lambda w: w["x0"])]
    matched = match_office(" ".join(title_words))
    if matched is None:
        return None
    office, district, candidates = matched

    # --- Machine/Hand/Total column labels -----------------------------------
    col_labels = [w for w in rotated if 140 <= w["top"] < 156]
    machines = sorted(w["x0"] for w in col_labels if w["text"][::-1] == "Machine")
    hands = sorted(w["x0"] for w in col_labels if w["text"][::-1] == "Hand")
    totals = sorted(w["x0"] for w in col_labels if w["text"][::-1] == "Total")
    if not (len(machines) == len(hands) == len(totals)) or not machines:
        raise ValueError(f"page {page_no}: inconsistent column labels")
    n_groups = len(machines)
    anchors = []
    for m, h, t in zip(machines, hands, totals):
        anchors += [("M", m), ("H", h), ("T", t)]

    def col_fn(word):
        center = (word["x0"] + word["x1"]) / 2
        idx = nearest_anchor(center, anchors)
        if idx is None:
            raise ValueError(
                f"page {page_no} {word['text']!r} at x={center:.1f}: no column anchor")
        return idx

    # --- candidate header validation ----------------------------------------
    header_words = [w for w in rotated if w["top"] < 140]
    groups = [[] for _ in range(n_groups)]
    for w in header_words:
        gi = min(range(n_groups), key=lambda i: abs(w["x0"] - machines[i]))
        groups[gi].append(w["text"][::-1])
    for ci, (_name, _party, surname) in enumerate(candidates):
        joined = " ".join(groups[ci]).upper()
        if surname not in joined:
            raise ValueError(
                f"page {page_no}: expected {surname!r} in candidate column {ci}, "
                f"got {joined!r}")

    # --- data rows (cluster tops; rows are ~9.6pt apart) ---------------------
    row_tops = []
    for w in sorted((w for w in upright if w["top"] >= 182),
                    key=lambda w: w["top"]):
        if not row_tops or abs(w["top"] - row_tops[-1]) >= 3:
            row_tops.append(w["top"])
    rows_by_top = {}
    for w in upright:
        if w["top"] < 182:
            continue
        bucket = min(row_tops, key=lambda t: abs(w["top"] - t))
        rows_by_top.setdefault(bucket, []).append(w)

    rows = []
    totals_rows = {}
    for top in sorted(rows_by_top):
        ws = sorted(rows_by_top[top], key=lambda w: w["x0"])
        joined = " ".join(w["text"] for w in ws)
        if joined.strip() in ("PAGE 1", "PAGE 2"):
            continue
        # the grid starts at x~163; everything left of it is the row label
        # (precinct names may end in a number, e.g. 'Meadville 4')
        label_words = [w["text"] for w in ws if w["x0"] < 155]
        nums = [w for w in ws if w["x0"] >= 155 and re.fullmatch(r"[\d,]+", w["text"])]
        label = " ".join(label_words).strip()
        if label.lower().startswith(("page totals", "totals from page", "grand total")):
            totals_rows[label] = [(w, int(w["text"].replace(",", ""))) for w in nums]
            continue
        if not label or not nums:
            continue

        cells = {}
        for w in nums:
            idx = col_fn(w)
            if idx in cells:
                raise ValueError(
                    f"page {page_no} row {label!r}: two values in column {idx}")
            cells[idx] = int(w["text"].replace(",", ""))

        per_cand = {}
        for ci in range(len(candidates)):
            m = cells.get(ci * 3 + 0, 0)
            h = cells.get(ci * 3 + 1, 0)
            t = cells.get(ci * 3 + 2, 0)
            if m + h != t:
                raise ValueError(
                    f"page {page_no} row {label!r} candidate {candidates[ci][0]!r}: "
                    f"machine {m} + hand {h} != total {t}")
            per_cand[ci] = (m, h, t)
        rows.append((label, per_cand))

    return office, district, candidates, rows, totals_rows, col_fn


def verify_contest(page_label, candidates, page_sets, totals_sets, col_fn):
    """Verify parsed rows against the contest's printed totals rows.

    page_sets: list of row lists (one per page, in order).  totals_sets:
    list of (page_no, totals_rows) aligned with page_sets.
    """
    def check(page_no, label, nums, target_rows):
        got = {}
        for w, val in nums:
            idx = col_fn(w)
            ci, sub = divmod(idx, 3)
            got[(ci, sub)] = val
        for ci in range(len(candidates)):
            m = got.get((ci, 0), 0)
            h = got.get((ci, 1), 0)
            t = got.get((ci, 2), 0)
            if m + h != t:
                raise ValueError(
                    f"{page_label} page {page_no} {label!r} candidate "
                    f"{candidates[ci][0]!r}: printed machine {m} + hand {h} != total {t}")
            summed = [0, 0, 0]
            for _precinct, per_cand in target_rows:
                if ci in per_cand:
                    for k in range(3):
                        summed[k] += per_cand[ci][k]
            if (summed[0], summed[1], summed[2]) != (m, h, t):
                raise ValueError(
                    f"{page_label} page {page_no} {label!r} candidate "
                    f"{candidates[ci][0]!r}: printed (M={m},H={h},T={t}) but precinct "
                    f"rows sum to (M={summed[0]},H={summed[1]},T={summed[2]})")

    for page_no, totals_rows in totals_sets:
        for label, nums in totals_rows.items():
            lab = label.lower()
            if lab.startswith("page totals"):
                check(page_no, label, nums, page_sets[page_no])
            elif lab.startswith("totals from page"):
                check(page_no, label, nums, page_sets[0])
            elif lab.startswith("grand total"):
                check(page_no, label, nums,
                      [row for page in page_sets for row in page])
            else:
                raise ValueError(f"unrecognized totals row {label!r}")


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("input_pdf")
    ap.add_argument("output_csv")
    ap.add_argument("--county", default="Crawford")
    args = ap.parse_args(argv)

    out_rows = []
    offices = OrderedDict()  # (office, district) -> state dict
    with pdfplumber.open(args.input_pdf) as pdf:
        for page_no, page in enumerate(pdf.pages):
            parsed = parse_page(page, page_no)
            if parsed is None:
                continue
            office, district, candidates, rows, totals_rows, col_fn = parsed
            st = offices.setdefault((office, district), {
                "candidates": candidates, "col_fn": col_fn, "label": page_no,
                "pages": [], "totals": [], "grand": {}})
            st["pages"].append(rows)
            st["totals"].append((len(st["pages"]) - 1, totals_rows))

    for (office, district), st in offices.items():
        verify_contest(f"{office} {district}".strip(), st["candidates"],
                       st["pages"], st["totals"], st["col_fn"])
        contest_totals = {name: 0 for name, _p, _s in st["candidates"]}
        for page_rows in st["pages"]:
            for precinct, per_cand in page_rows:
                for ci, (m, h, t) in sorted(per_cand.items()):
                    name, party, _s = st["candidates"][ci]
                    if name == "Write Ins":
                        out_rows.append({
                            "county": args.county, "precinct": precinct,
                            "office": office, "district": district, "party": "",
                            "candidate": "Write Ins", "votes": t,
                            "election_day": "", "early_voting": "",
                            "provisional": h if h else "",
                        })
                    else:
                        out_rows.append({
                            "county": args.county, "precinct": precinct,
                            "office": office, "district": district, "party": party,
                            "candidate": name, "votes": t,
                            "election_day": m if m else "",
                            "early_voting": "",
                            "provisional": h if h else "",
                        })
                    contest_totals[name] += t
        st["grand"] = contest_totals

    with open(args.output_csv, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDNAMES)
        w.writeheader()
        w.writerows(out_rows)

    print(f"wrote {len(out_rows)} rows, {len(offices)} contests "
          f"({len({r['precinct'] for r in out_rows})} precincts) -> {args.output_csv}",
          file=sys.stderr)
    for (office, district), st in offices.items():
        lab = f"{office} {district}".strip()
        summary = ", ".join(f"{n}={v}" for n, v in st["grand"].items())
        print(f"  {lab}: {summary}", file=sys.stderr)


if __name__ == "__main__":
    main()