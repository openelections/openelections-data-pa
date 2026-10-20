#!/usr/bin/env python3
"""Parse Potter County 2022 primary election precinct results.

Source (in parse order):

1. PA Department of State bulk precinct-returns file for the 2022 Primary
   ("prcres" format; ElectionReturns_2022_Primary_PrecinctReturns.txt from
   https://www.pa.gov/agencies/dos/resources/voting-and-elections-resources/
   voting-and-election-statistics/election-data, county code 53 = Potter).
   The county's own "Potter PA 2022 Primary Official Summary.pdf" is
   COUNTYWIDE-ONLY (one aggregate page per contest, no precinct columns), so
   the DOS file is the precinct-level source -- the same fallback used for
   the 2022 primary in Cameron/Monroe/Pike.  The DOS precinct returns match
   the ENR county-level anchor (2022/20220517__pa__primary__county.csv)
   exactly for every named candidate, including the certified (post-recount)
   GOP U.S. Senate figures, and match the county summary's printed countywide
   candidate totals exactly.

2. The county "Official Summary" PDF, read as OCR markdown
   (PaddleOCR-VL over the scanned PDF -> work2022p/potter/potter_primary_ocr.txt),
   supplies countywide totals used ONLY for verification: per-contest named
   candidate totals, "Write-In Totals" and party "Contest Totals" (ballots
   cast).  The PDF carries no per-precinct detail.  Its page-2 contest header
   line was lost in OCR; the page-2 table is the DEM U.S. Senate race
   (Khalil/Lamb/Kenyatta/Fetterman follow the STATISTICS page and precede
   DEM Governor) -- the parser assigns that title explicitly.

Not available per precinct anywhere: write-in votes (the summary reports only
countywide "Write-In Totals"), Registered Voters / Ballots Cast, and vote-type
breakdowns (election day / absentee-mail / provisional) -- the DOS file has a
single votes cell per (precinct, candidate).  Output is a 7-column file with
named candidates only.

Party-committee contests (countywide-only in the summary: "DEM Member of
Democratic State Committee", "REP Member of Republican State Committee",
"REP County Chairman", "DEM Abbott Twp Committeeman") are omitted, as no
per-precinct data exists; the named-candidate countywide figures are printed
by verification for the record.

Precinct naming: DOS municipal descriptions are mapped to the names used in
the county's own 2022 general file (2022/counties/20221108__pa__general__
potter__precinct.csv): "X Township", "X Borough", "Coudersport/Galeton
First/Second Ward".  Two DOS precinct names are ambiguous (OSWAYO, ULYSSES
each appear for township + borough).  The mapping is pinned by precinct code
and verified against the general DOS file
(work2022g/cameron/ElectionReturns_2022_General_PrecinctReturns.txt, county
53): for both pairs the LOWER code is the township (Oswayo 180, Ulysses 310),
whose general DOS values match the county general file exactly (Oswayo 16/8,
Ulysses 39/41 Molesevich, 99/50 and 255/192 Thompson).

Usage:
  python pa_potter_primary_2022_results_parser.py <prcres.txt> <summary_ocr.txt> \
      <anchor_county.csv> <out.csv>
"""
from __future__ import annotations

import collections
import csv
import re
import sys
from collections import OrderedDict

COUNTY = "Potter"
COUNTY_CODE = "53"
YEAR = "2022"
ETYPE = "P"

FIELDNAMES = ["county", "precinct", "office", "district", "party", "candidate", "votes"]

# ---------------------------------------------------------------- precincts
# precinct code -> output name (county's own 2022 general naming; see module
# docstring for the township/borough pinning)
PRECINCTS = {
    "10": "Abbott Township", "20": "Allegany Township", "30": "Austin Borough",
    "40": "Bingham Township", "50": "Clara Township",
    "60": "Coudersport First Ward", "70": "Coudersport Second Ward",
    "80": "Eulalia Township", "100": "Galeton First Ward",
    "110": "Galeton Second Ward", "120": "Genesee Township",
    "130": "Harrison Township", "140": "Hebron Township", "150": "Hector Township",
    "160": "Homer Township", "170": "Keating Township",
    "180": "Oswayo Township", "190": "Oswayo Borough", "200": "Pike Township",
    "210": "Pleasant Valley Township", "220": "Portage Township",
    "230": "Roulette Township", "240": "Sharon Township",
    "260": "Shinglehouse Borough", "270": "Stewardson Township",
    "280": "Summit Township", "290": "Sweden Township", "300": "Sylvania Township",
    "310": "Ulysses Township", "320": "Ulysses Borough",
    "330": "West Branch Township", "340": "Wharton Township",
}

# ---------------------------------------------------------------- contests
OFFICE_MAP = {
    "USS": "U.S. Senate",
    "GOV": "Governor",
    "LTG": "Lieutenant Governor",
    "USC": "U.S. House",     # district from the row's district field
    "STS": "State Senate",
    "STH": "State House",
}
NO_DISTRICT = {"USS", "GOV", "LTG"}

# DOS candidate number -> (office code, party, output name).  Names follow the
# dominant short forms already used across the 2022-primary county files
# (statewide races) and the county's own 2022 general file / CD-15 and CD-67
# neighbors (Thompson / Causer / Rossman).
CANDIDATES = {
    "2022C0216": ("USS", "DEM", "Alex Khalil"),
    "2022C0167": ("USS", "DEM", "Conor Lamb"),
    "2022C0126": ("USS", "DEM", "Malcolm Kenyatta"),
    "2022C0203": ("USS", "DEM", "John Fetterman"),
    "2022C0147": ("USS", "REP", "Mehmet Oz"),
    "2022C0176": ("USS", "REP", "Dave McCormick"),
    "2022C0138": ("USS", "REP", "Kathy Barnette"),
    "2022C0142": ("USS", "REP", "Jeff Bartos"),
    "2022C0154": ("USS", "REP", "Carla Sands"),
    "2022C0193": ("USS", "REP", "Sean Gale"),
    "2022C0210": ("USS", "REP", "George Bochetto"),
    "2022C0206": ("GOV", "DEM", "Josh Shapiro"),
    "2022C0161": ("GOV", "REP", "Lou Barletta"),
    "2022C0139": ("GOV", "REP", "Douglas V. Mastriano"),
    "2022C0247": ("GOV", "REP", "Nche Zama"),
    "2022C0163": ("GOV", "REP", "Dave White"),
    "2022C0224": ("GOV", "REP", "Melissa Hart"),
    "2022C0195": ("GOV", "REP", "Bill McSwain"),
    "2022C0238": ("GOV", "REP", "Charlie Gerow"),
    "2022C0192": ("GOV", "REP", "Joe Gale"),
    "2022C0204": ("GOV", "REP", "Jake Corman"),
    "2022C0137": ("LTG", "DEM", "Brian Sims"),
    "2022C0207": ("LTG", "DEM", "Austin Davis"),
    "2022C0249": ("LTG", "DEM", "Ray Sosa"),
    "2022C0145": ("LTG", "REP", "Clarice Schillinger"),
    "2022C0160": ("LTG", "REP", "Teddy Daniels"),
    "2022C0166": ("LTG", "REP", "Rick Saccone"),
    "2022C0169": ("LTG", "REP", "Carrie Lewis Delrosso"),
    "2022C0234": ("LTG", "REP", "Russ Diamond"),
    "2022C0239": ("LTG", "REP", "Chris Frye"),
    "2022C0240": ("LTG", "REP", "James Earl Jones"),
    "2022C0243": ("LTG", "REP", "Jeff Coleman"),
    "2022C0237": ("LTG", "REP", "John Brown"),
    "2022C0153": ("USC", "REP", "Glenn GT Thompson"),
    "2022C0725": ("STH", "REP", "Martin T Causer"),
    "2022C1080": ("STH", "REP", "Robert W Rossman"),
}


def read_prcres(path: str):
    """Read Potter 2022 primary rows from the DOS bulk precinct returns file.

    Returns (contests, seen_precs) with
      contests = {(precinct, office, district): {(name, party): votes}}
    """
    contests: OrderedDict = OrderedDict()
    seen_precs: OrderedDict = OrderedDict()
    n_rows = 0
    with open(path, newline="") as fh:
        for r in csv.reader(fh):
            if len(r) < 27 or r[0] != YEAR or r[1] != ETYPE or r[2] != COUNTY_CODE:
                continue
            n_rows += 1
            off_code, cand_no = r[8], r[10]
            if off_code not in OFFICE_MAP:
                raise ValueError(f"unhandled office code {off_code!r} ({r[22]})")
            cand = CANDIDATES.get(cand_no)
            if cand is None:
                raise ValueError(f"unhandled candidate number {cand_no}")
            if cand[0] != off_code:
                raise ValueError(f"contest mismatch for {cand_no} in {off_code}")
            if cand[1] != r[9]:
                raise ValueError(f"party mismatch for {cand_no}: {r[9]}")
            district = "" if off_code in NO_DISTRICT else str(int(r[5]))
            code = str(int(r[3]))
            if code not in PRECINCTS:
                raise ValueError(f"unknown Potter precinct code {code!r} ({r[22]})")
            # the municipal description is informational only (OSWAYO/ULYSSES
            # are ambiguous township/borough pairs); the code pins the name
            prec = PRECINCTS[code]
            seen_precs.setdefault((prec, code), 0)
            seen_precs[(prec, code)] += 1
            key = (prec, OFFICE_MAP[off_code], district)
            entry = contests.setdefault(key, OrderedDict())
            entry[(cand[2], cand[1])] = entry.get((cand[2], cand[1]), 0) + int(r[15])
    print(f"prcres: {n_rows} rows -> {len(contests)} (precinct, contest) "
          f"groups, {len(seen_precs)} precincts")
    if len(seen_precs) != len(PRECINCTS):
        raise ValueError(f"precinct coverage {len(seen_precs)} != {len(PRECINCTS)}")
    return contests


# ---------------------------------------------------------------- summary #
# county "Official Summary" OCR: countywide-only totals used for verification
# keys are _title_to_key forms (punctuation/space-free, lowercased)
TITLE_MAP = {
    "demunitedstatessenator": ("U.S. Senate", "", "DEM", "emit"),
    "demgovernor": ("Governor", "", "DEM", "emit"),
    "demlieutenantgovernor": ("Lieutenant Governor", "", "DEM", "emit"),
    "demrepresentativeincongress15thdistrict": ("U.S. House", "15", "DEM", "emit"),
    "demrepresentativeinthegeneralassembly67thdistrict":
        ("State House", "67", "DEM", "emit"),
    "demmemberofdemocraticstatecommittee":
        ("Member of Democratic State Committee", "", "DEM", "countywide-only"),
    "demabbotttwpcommitteeman":
        ("Abbott Twp Committeeman", "", "DEM", "countywide-only"),
    "repunitedstatessenator": ("U.S. Senate", "", "REP", "emit"),
    "repgovernor": ("Governor", "", "REP", "emit"),
    "replieutenantgovernor": ("Lieutenant Governor", "", "REP", "emit"),
    "reprepresentativeincongress15thdistrict": ("U.S. House", "15", "REP", "emit"),
    "reprepresentativeinthegeneralassembly67thdistrict":
        ("State House", "67", "REP", "emit"),
    "repmemberofrepublicanstatecommittee":
        ("Member of Republican State Committee", "", "REP", "countywide-only"),
    "repcountychairman": ("County Chairman", "", "REP", "countywide-only"),
}
# page-2 title was lost by OCR; its table is the DEM U.S. Senate race
PAGE2_TITLE = "demunitedstatessenator"


def _norm(name: str) -> str:
    """Case/punctuation-insensitive form for matching names between sources."""
    return re.sub(r"[^A-Z0-9]", "", name.upper())


def _title_to_key(title: str) -> str:
    """'## DEM Representative in Congress 15th District Vote For 1' ->
    lower, punctuation-less key with the trailing ' Vote For N' removed."""
    t = " ".join(title.split())
    t = re.sub(r"\s+[Vv]ote [Ff]or\s+\d+\s*$", "", t)
    if not t:
        raise ValueError("empty title")
    key = _norm(t).lower()
    key = re.sub(r"votefor\d+$", "", key)
    return key


class _Counts:
    def __init__(self):
        self.totals = {}


def read_summary_ocr(path: str):
    """Parse the OCR'd county 'Official Summary' markdown.

    Returns OrderedDict {title key: {"candidates": {normalized-name -> total},
    "writein": int, "counts": totals-dict}} for the contests whose header
    rows survived OCR, plus the page-2 table (whose header line the OCR
    dropped) under the DEM-U.S.-Senate key.
    """
    with open(path, encoding="utf-8") as fh:
        raw = fh.read()
    parts = re.split(r"===== PAGE (\d+) =====", raw)
    pages: OrderedDict = OrderedDict()
    for i in range(1, len(parts), 2):
        pages[int(parts[i])] = parts[i + 1]

    # titles matched as "## TITLE" lines or centered-div lines
    title_re = re.compile(
        r"^(?:##\s*(?P<h1>.+?)|<div[^>]*>\s*(?P<h2>.+?)\s*</div>)\s*$", re.M)
    table_re = re.compile(r"<table.*?</table>", re.S)

    out: OrderedDict = OrderedDict()
    for pno, text in pages.items():
        marks = sorted(
            [("title", m.start(), (m.group("h1") or m.group("h2")).strip())
             for m in title_re.finditer(text)]
            + [("table", m.start(), m.group(0)) for m in table_re.finditer(text)],
            key=lambda x: x[1])
        cur_title = None
        for kind, _pos, payload in marks:
            if kind == "title":
                key = _title_to_key(payload)
                if key not in TITLE_MAP:
                    if key == "statistics" or key == "statistic":
                        cur_title = None
                        continue  # the "STATISTICS" page banner: not a contest
                    raise ValueError(f"page {pno}: unknown title {payload!r}")
                cur_title = key
            else:
                if cur_title is None:
                    if pno != 2:
                        continue    # STATISTICS etc.: a tableless page header
                    cur_title = PAGE2_TITLE
                out.setdefault(cur_title, {"candidates": OrderedDict(),
                                           "writein": 0, "counts": _Counts()})
                entry = out[cur_title]
                rows = re.findall(r"<tr>(.*?)</tr>", payload, re.S)
                for row in rows:
                    cells = [re.sub(r"<[^>]+>", "", c).strip()
                             for c in re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)]
                    cells = [" ".join(c.split()) for c in cells]
                    if len(cells) < 2 or not cells[0]:
                        continue
                    label = cells[0]
                    total = int(cells[1].replace(",", "")) if cells[1] else 0
                    if label.startswith("Write-In:"):
                        continue    # named write-ins: countywide detail only
                    if label == "Write-In Totals":
                        entry["writein"] += total
                    elif label in ("Not Assigned", "Total Votes Cast",
                                   "Contest Totals"):
                        entry["counts"].totals[label] = total
                    else:
                        name = _norm(label)
                        entry["candidates"][name] = \
                            entry["candidates"].get(name, 0) + total
                cur_title = None
    return out


# ENR anchor formal candidate name -> our output name (party-level check when
# a name is missing here) -- state committee contests are countywide-only and
# not emitted, handled separately in verify()
ANCHOR_TO_NAME = {
    "Alexandria Gloria Khalil": "Alex Khalil", "Conor J. Lamb": "Conor Lamb",
    "John K. Fetterman": "John Fetterman", "Malcolm Kenyatta": "Malcolm Kenyatta",
    "Mehmet C. Oz": "Mehmet Oz", "David H. McCormick": "Dave McCormick",
    "Kathy J. Barnette": "Kathy Barnette", "Jeffrey A. Bartos": "Jeff Bartos",
    "Carla Herd Sands": "Carla Sands", "Sean Peter Gale": "Sean Gale",
    "George A. Bochetto": "George Bochetto",
    "Joshua D. Shapiro": "Josh Shapiro",
    "Louis Barletta": "Lou Barletta", "Douglas V. Mastriano": "Douglas V. Mastriano",
    "Jacob Doyle Corman III": "Jake Corman", "Joseph Charles Gale": "Joe Gale",
    "Melissa A. Hart": "Melissa Hart", "William M. McSwain": "Bill McSwain",
    "Charles R. Gerow": "Charlie Gerow", "David J. White": "Dave White",
    "Nche Zama": "Nche Zama",
    "Austin Davis": "Austin Davis", "Brian Sims": "Brian Sims",
    "Raymond L. Sosa": "Ray Sosa",
    "Clarice D. Schillinger": "Clarice Schillinger",
    "Theodore Daniels": "Teddy Daniels", "Richard Saccone": "Rick Saccone",
    "Carrie Delrosso": "Carrie Lewis Delrosso", "Russell H. Diamond": "Russ Diamond",
    "Christopher C. Frye Jr": "Chris Frye", "James E. Jones": "James Earl Jones",
    "Jeffrey H. Coleman": "Jeff Coleman", "John A. Brown": "John Brown",
    "Glenn William Thompson Jr": "Glenn GT Thompson",
    "Martin T. Causer": "Martin T Causer", "Robert W. Rossman": "Robert W Rossman",
}
COUNTYWISE_ONLY_OFFICES = {
    "Member of Democratic State Committee", "Member of Republican State Committee",
}


def verify(contests, summary, anchor_path):
    """Verify per-candidate county sums against summary + anchor; report
    countywide-only residuals."""
    print("\n--- verification ---")
    ours: dict = collections.defaultdict(int)
    for (prec, office, district), cands in contests.items():
        for (name, party), v in cands.items():
            ours[(office, district, party, name)] += v

    # ENR anchor (named candidates only)
    anchor: dict = {}
    with open(anchor_path, newline="") as fh:
        for row in csv.DictReader(fh):
            if row["county"] != COUNTY:
                continue
            anchor[(row["office"], row["district"], row["party"],
                    row["candidate"])] = int(row["votes"])

    exact = mismatches = 0
    skipped = set()
    for k, v in sorted(anchor.items()):
        office, dist, party, cand = k
        if office in COUNTYWISE_ONLY_OFFICES:
            skipped.add((office, party)); continue
        mapped = ANCHOR_TO_NAME.get(cand)
        if mapped is None:
            mismatches += 1
            print(f"  ANCHOR name unmapped {office} {dist} {party} {cand}")
        got = sum(val for (o, d, p, name), val in ours.items()
                  if o == office and d == dist and p == party and name == mapped)
        if got == v:
            exact += 1
        else:
            mismatches += 1
            print(f"  ANCHOR MISMATCH {office} {dist} {party} {cand} "
                  f"(as {mapped}): ours {got} vs anchor {v}")
    print(f"ENR anchor candidate-level: {exact} exact, {mismatches} mismatches; "
          f"state committee (countywide-only in every source) skipped: "
          f"{sorted(skipped)}")

    # county summary (OCR) per contest: named candidates + write-in totals
    print("county 'Official Summary' countywide check "
          "(named candidates = our precinct sum):")
    issues = 0
    for party_title, entry in summary.items():
        office, district, party, status = TITLE_MAP[party_title]
        if status != "emit":
            print(f"  [countywide-only, not emitted] {office} {party}: "
                  f"summary write-in row {entry['writein']}, "
                  f" Contest Totals {entry['counts'].totals.get('Contest Totals')}")
            continue
        ok_rows = bad_rows = 0
        for name, total in entry["candidates"].items():
            got = {val for (o, d, p, name2), val in ours.items()
                   if o == office and d == district and p == party
                   and _norm(name2) == name}
            gotv = sum(got) if got else 0
            if gotv == total or (not got and total == 0):
                ok_rows += 1
            else:
                bad_rows += 1
                issues += 1
                print(f"  SUMMARY MISMATCH {office} {party} {name}: ours {gotv} "
                      f"vs summary {total}")
        tvc = entry["counts"].totals.get("Total Votes Cast")
        ct = entry["counts"].totals.get("Contest Totals")
        named = sum(entry["candidates"].values())
        note = ""
        if tvc is not None and named + entry["writein"] != tvc:
            note += (f" [named+writein {named + entry['writein']} != TVC {tvc}]")
            issues += 1
        print(f"  {office or '-'} {party}: {ok_rows} candidates exact, "
              f"{bad_rows} mismatched; write-in totals {entry['writein']} "
              f"(countywide only, not emitted); contest totals {ct}{note}")
    return issues


def main(argv):
    if len(argv) < 4:
        print(__doc__)
        return 2
    prcres, summary_ocr, anchor_path, outfile = argv[1:5]
    contests = read_prcres(prcres)

    merged = contests   # named candidates only; no per-precinct write-ins exist
    rows = []
    for (prec, office, district), cands in merged.items():
        for (name, party), votes in cands.items():
            rows.append({"county": COUNTY, "precinct": prec, "office": office,
                         "district": district, "party": party, "candidate": name,
                         "votes": votes})
    with open(outfile, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDNAMES)
        w.writeheader()
        for r in sorted(rows, key=lambda x: (
                prec_order[x["precinct"]], x["office"], x["district"],
                x["party"], x["candidate"])):
            w.writerow(r)
    print(f"wrote {len(rows)} rows -> {outfile}")

    summary = read_summary_ocr(summary_ocr)
    verify(merged, summary, anchor_path)
    return 0


prec_order = {name: i for i, name in enumerate(PRECINCTS.values())}


if __name__ == "__main__":
    sys.exit(main(sys.argv))