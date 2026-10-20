#!/usr/bin/env python3
"""Parse the Delaware County 2022 General "Cumulative Results" HTML (Clarity).

The source file is the Clarity Elections Count_HtmlCumulativeReport saved as
UTF-16 (BOM-encodable): one official COUNTYWIDE table per contest with
Election Day / By Mail / Provisional / Total columns, plus "Write-in Votes"
rows and a "Cast Votes" row per contest. It contains NO precinct detail
(only a "Precincts Reporting 428 of 428" header), so this parser emits
county-level rows in the consolidated county.csv convention:

    county,office,district,party,candidate,votes,election_day,mail,provisional

Registered Voters (TotalRegisteredVoters) and Ballots Cast
(TotalTabulatedBallots) come from the report's META tags. Per-contest
"Cast Votes" totals are used only as an internal cross-check (candidate rows
must equal them; Cast Votes excludes write-ins, which the source prints on a
separate "Write-in Votes" row).

Usage (HTML county-level mode):
    python pa_delaware_general_2022_results_parser.py <input.html> <output.csv> [--county Delaware]

DOS precinct mode (--dos first argument): the Clarity HTML is countywide-only,
so per-precinct rows come from the PA Department of State bulk
precinct-returns file for the 2022 General
(ElectionReturns_2022_General_PrecinctReturns.txt, county code 23; verbatim
slice kept at work2022g/delaware/delaware_precinct_slice.txt — 6,561 rows =
429 precinct codes x 5 USS + 5 GOV + 2 USC(CD5) + SD-8/SD-26 + local State
House candidate rows), exactly as for Cameron 2022 general and the
Delaware 2022 primary:

    python pa_delaware_general_2022_results_parser.py --dos <prcres_slice> \
        <output.csv> [enr_county_csv] [clarity_county_csv]

DOS-mode output header (10 columns, breakdowns empty — the DOS bulk file
carries no election-day/mail/provisional split; fields 16/17 are 0 for every
row): county,precinct,office,district,party,candidate,votes,election_day,
mail,provisional.  The DOS bulk file also carries no write-in rows, no
Registered Voters / Ballots Cast and no local offices (e.g. the Haverford
1st Ward special commissioner election), so those stay countywide-only in
the Clarity-mode county CSV (documented, no-invention rule).

Precinct names reuse pa_delaware_primary_2022_results_parser's
dos_precinct_name (lazy import: that module imports decode_source from this
one, so importing it top-level would cycle) — the same ERStat-style
spellings already in the repo (2020 primary / 2022 primary Delaware files):
ALDAN Eastern, ASTON 1W, BETHEL 1P, CHESTER CITY 10W 1P, DARBY TWP 7W 3P
(DOS township/borough codes split DARBY TWP 840-900 vs DARBY BORO 910-960),
NETHER PROV 1W 1P, UPPER DARBY 1D 1P, TRAINER Lennox Park, ...  All 429
codes map and the resulting name set is identical to the county's 2022
primary precinct file.  Candidate names follow the ENR/county.csv census of
certified spellings (2022/20221108__pa__general__county.csv, Delaware rows:
Joshua D. Shapiro, Douglas V. Mastriano, John K. Fetterman, John Vincent
Hayes Iii, ...) so the precinct sums verify exactly against that anchor.
"""

import argparse
import csv
import os
import re
import sys

from bs4 import BeautifulSoup

# reuse the cell-numbering helper from the same county's 2023 parser (read-only)
from pa_delaware_general_2023_results_parser import numbers_from_cells

COLS = ["county", "office", "district", "party", "candidate",
        "votes", "election_day", "mail", "provisional"]

CONTEST_MAP = [
    (re.compile(r"^United States Senator", re.I),
     lambda m: ("U.S. Senate", "")),
    (re.compile(r"^Governor and Lieutenant Governor", re.I),
     lambda m: ("Governor", "")),
    (re.compile(r"^Representative in Congress (\d+)\w* District", re.I),
     lambda m: ("U.S. House", m.group(1))),
    (re.compile(r"^Senator in the General Assembly (\d+)\w* District", re.I),
     lambda m: ("State Senate", m.group(1))),
    (re.compile(r"^Representative in the General Assembly (\d+)\w* District", re.I),
     lambda m: ("State House", m.group(1))),
    # "Special Election - Commissioner Haverford 1st Ward - (Vote for One) Unexpired Term"
    (re.compile(r"^Special Election - Commissioner (.+?) (\d+)\w* Ward", re.I),
     lambda m: ("Township Commissioner (Special - Unexpired Term)",
                "%s Township - Ward %s" % (m.group(1).strip(), m.group(2)))),
]


def split_contest_name(name):
    n = re.sub(r"\s+", " ", name).strip()
    n = re.sub(r"\s*-\s*\(Vote for[^)]*\)\s*", " ", n).strip()
    n = re.sub(r"\s+-\s+$", "", n)
    for rx, fn in CONTEST_MAP:
        m = rx.match(n)
        if m:
            return fn(m), n
    return (n, ""), n


def decode_source(path):
    raw = open(path, "rb").read()
    for enc in ("utf-16", "utf-16-le", "utf-8-sig", "utf-8"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def parse(input_path, county="Delaware"):
    soup = BeautifulSoup(decode_source(input_path), "html.parser")

    meta = {}
    for m in soup.find_all("meta"):
        if m.get("name") and m.get("content") is not None:
            meta[m["name"]] = m["content"]
    rv = int(meta.get("TotalRegisteredVoters", "0") or 0)
    bc = int(meta.get("TotalTabulatedBallots", "0") or 0)

    headers = [s for s in soup.find_all(string=re.compile(r"\(Vote for|Retention Question"))]

    rows = [{
        "county": county, "office": "Registered Voters", "district": "",
        "party": "", "candidate": "", "votes": rv,
        "election_day": "", "mail": "", "provisional": "",
    }, {
        "county": county, "office": "Ballots Cast", "district": "",
        "party": "", "candidate": "", "votes": bc,
        "election_day": "", "mail": "", "provisional": "",
    }]

    problems = []
    seen_contests = set()
    contest_totals = []
    for h in headers:
        contest_name = re.sub(r"\s+", " ", h.strip())
        if contest_name in seen_contests:
            problems.append("duplicate contest header: %s (skipped)" % contest_name)
            continue
        seen_contests.add(contest_name)
        (office, district), _ = split_contest_name(contest_name)

        tbl = h
        while tbl is not None and tbl.name != "table":
            tbl = tbl.parent
        if tbl is None:
            problems.append("%s: no table" % contest_name)
            continue

        cast_by_col = None
        for tr in tbl.find_all("tr"):
            cells = [c.get_text(" ", strip=True) for c in tr.find_all(["th", "td"])]
            if not any(cells):
                continue
            label = cells[0].strip()
            if label.startswith("Cast Votes"):
                nums = numbers_from_cells(cells)
                if len(nums) >= 4:
                    cast_by_col = nums
                continue
            if label.startswith("Write-in Votes"):
                nums = numbers_from_cells(cells)
                if len(nums) < 4:
                    continue
                ed, mail, prov, total = nums[:4]
                if ed + mail + prov != total:
                    problems.append("%s / Write-ins: ED+Mail+Prov=%d != Total=%d"
                                    % (contest_name, ed + mail + prov, total))
                rows.append({
                    "county": county, "office": office, "district": district,
                    "party": "", "candidate": "Write Ins",
                    "votes": total, "election_day": ed, "mail": mail,
                    "provisional": prov,
                })
                continue
            m = re.match(r"^\(\d+\)\s+(.+)$", label)
            if not m:
                continue
            cand = m.group(1).strip()
            party = cells[1].strip() if len(cells) > 1 and cells[1].strip() else ""
            nums = numbers_from_cells(cells)
            if len(nums) < 4:
                problems.append("%s / %s: short row %r" % (contest_name, cand, cells))
                continue
            ed, mail, prov, total = nums[0], nums[1], nums[2], nums[3]
            rows.append({
                "county": county, "office": office, "district": district,
                "party": party, "candidate": cand,
                "votes": total, "election_day": ed, "mail": mail,
                "provisional": prov,
            })
            if ed + mail + prov != total:
                problems.append("%s / %s: ED+Mail+Prov=%d != Total=%d"
                                % (contest_name, cand, ed + mail + prov, total))

        # cross-check: candidate rows only (Cast Votes excludes write-ins)
        mine = {}
        for r in rows:
            if (r["office"] == office and r["district"] == district
                    and r["candidate"] and r["candidate"] != "Write Ins"):
                for c in ("votes", "election_day", "mail", "provisional"):
                    mine[c] = mine.get(c, 0) + r[c]
        if cast_by_col and len(cast_by_col) >= 4:
            want = {"election_day": cast_by_col[0], "mail": cast_by_col[1],
                    "provisional": cast_by_col[2], "votes": cast_by_col[3]}
            for c, v in want.items():
                if mine.get(c, 0) != v:
                    problems.append(
                        "%s: summed candidate %s=%d != Cast Votes=%d"
                        % (contest_name, c, mine.get(c, 0), v))

        # sanity: no contest's cast votes may exceed Ballots Cast
        if cast_by_col and bc and cast_by_col[-1] > bc:
            problems.append("%s: Cast Votes=%d > Ballots Cast=%d"
                            % (contest_name, cast_by_col[-1], bc))
        if cast_by_col:
            contest_totals.append((office, district, cast_by_col[-1]))

    return rows, problems, len(seen_contests), contest_totals, (rv, bc)


# =================================================================== DOS mode
DOS_COUNTY_CODE = "23"
DOS_ETYPE = "G"
DOS_YEAR = "2022"
DOS_FIELDNAMES = ["county", "precinct", "office", "district", "party",
                  "candidate", "votes", "election_day", "mail", "provisional"]

# DOS office code -> (office, district from row[5]); same code set the Cameron
# 2022-general parser uses (no LTG on the 2022 general ballot).
DOS_OFFICES = {
    "USS": ("U.S. Senate", False),
    "GOV": ("Governor", False),
    "USC": ("U.S. House", True),
    "STS": ("State Senate", True),
    "STH": ("State House", True),
}
DOS_OFFICE_ORDER = ["U.S. Senate", "Governor", "U.S. House",
                    "State Senate", "State House"]

# DOS (LAST, FIRST, MIDDLE, SUFFIX) -> printed name.  Spellings are the
# certified ENR/DOS forms already in 2022/20221108__pa__general__county.csv
# (Delaware rows), so precinct sums verify exactly against that anchor.
DOS_CANDIDATES = {
    ("FETTERMAN", "JOHN", "K", ""): "John K. Fetterman",
    ("OZ", "MEHMET", "C", ""): "Mehmet C. Oz",
    ("GERHARDT", "ERIK", "", ""): "Erik Gerhardt",
    ("WEISS", "RICHARD", "L", ""): "Richard L. Weiss",
    ("WASSMER", "DANIEL", "", ""): "Daniel Wassmer",
    ("SHAPIRO", "JOSHUA", "D", ""): "Joshua D. Shapiro",
    ("MASTRIANO", "DOUGLAS", "V", ""): "Douglas V. Mastriano",
    ("HACKENBURG", "JONATHAN", "MATTHEW", ""): "Jonathan Matthew Hackenburg",
    ("DIGIULIO", "CHRISTINA", "PK", ""): "Christina Pk Digiulio",
    ("SOLOSKI", "JOSEPH", "P", ""): "Joseph P. Soloski",
    ("SCANLON", "MARY GAY", "", ""): "Mary Gay Scanlon",
    ("GALLUCH", "DAVID", "LEWCZYK", ""): "David Lewczyk Galluch",
    ("WILLIAMS", "ANTHONY", "HARDY", ""): "Anthony Hardy Williams",
    ("HAYES", "JOHN", "VINCENT", "III"): "John Vincent Hayes Iii",
    ("KEARNEY", "TIMOTHY", "P", ""): "Timothy P. Kearney",
    ("AGOVINO", "FRANK", "", ""): "Frank Agovino",
    ("KAZEEM", "CAROL", "", ""): "Carol Kazeem",
    ("MOTON", "RUTH", "", ""): "Ruth Moton",
    ("SPAHR", "CATHERINE", "E", ""): "Catherine E. Spahr",
    ("WILLIAMS", "WENDELL", "CRAIG", ""): "Wendell Craig Williams",
    ("KRUEGER", "LEANNE", "T.", ""): "Leanne T. Krueger",
    ("MONGELLUZZO", "EDWARD", "A", ""): "Edward A. Mongelluzzo",
    ("DELLOSO", "DAVID", "M", ""): "David M. Delloso",
    ("MATTUS", "MICHELLE", "STOCK", ""): "Michelle Stock Mattus",
    ("ZABEL", "MICHAEL", "P", ""): "Michael P. Zabel",
    ("RUCCI", "KENNETH", "P", ""): "Kenneth P. Rucci",
    ("GOODWIN", "ALFEIA", "DEVAUGHN", ""): "Alfeia Devaughn Goodwin",
    ("CURRY", "GINA", "H.", ""): "Gina H. Curry",
    ("O'MARA", "JENNIFER", "", ""): "Jennifer O'Mara",  # trailing space stripped
    ("MISSINO", "NICHOLE", "LEANN", ""): "Nichole Leann Missino",
    ("FOSTER", "WILLIAM", "R", ""): "William R. Foster",
    ("VITALI", "GREGORY", "S.", ""): "Gregory S. Vitali",
    ("RAZZANO", "KIMBERLY", "J", ""): "Kimberly J. Razzano",
    ("CLIFFORD", "EDWARD", "T", "III"): "Edward T. Clifford Iii",
    ("BOROWSKI", "LISA", "", ""): "Lisa Borowski",
    ("QUINN", "CHRISTOPHER", "B.", ""): "Christopher B. Quinn",
    ("YOUNG", "REGINA", "G", ""): "Regina G. Young",
    ("MCCLINTON", "JOANNA", "E.", ""): "Joanna E. McClinton",
}


def read_dos_precincts(path):
    """Read the Delaware (county code 23) 2022-general rows from a DOS
    precinct-returns slice.  Returns (rows, n_read, n_precinct_codes)."""
    # lazy import: the primary parser imports decode_source from this module,
    # so a top-level import would be circular.
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from pa_delaware_primary_2022_results_parser import dos_precinct_name

    rows = []
    n_read = 0
    codes = set()
    with open(path, newline="", encoding="latin-1") as fh:
        for r in csv.reader(fh):
            if not r or r[:3] != [DOS_YEAR, DOS_ETYPE, DOS_COUNTY_CODE]:
                continue
            n_read += 1
            if len(r) < 25:
                raise ValueError("short DOS row: %r" % (r[:10],))
            off_code = r[8]
            if off_code not in DOS_OFFICES:
                raise ValueError("unexpected office code %r (candidate %r)"
                                 % (off_code, r[10]))
            last, first, mid, suffix = (r[11].strip(), (r[12] or "").strip(),
                                        (r[13] or "").strip(), r[14].strip())
            key = (last, first, mid, suffix)
            name = DOS_CANDIDATES.get(key)
            if name is None:
                raise ValueError("unmapped DOS candidate %r (office %s)"
                                 % (key, off_code))
            office, has_dist = DOS_OFFICES[off_code]
            # the office's own district number for USC/STS/STH is row[5]
            district = str(int(r[5])) if has_dist else ""
            code = str(int(r[3]))
            precinct = dos_precinct_name(code, r[22], r[23], r[24])
            votes = int(r[15])
            if votes < 0:
                raise ValueError("negative votes in row %r" % (r[:16],))
            # no election-day/mail/provisional split in the DOS bulk file
            # (fields 16/17 are 0 for every Delaware row) -> breakdowns empty
            rows.append({
                "county": "Delaware", "precinct": precinct,
                "office": office, "district": district, "party": r[9],
                "candidate": name, "votes": votes,
                "election_day": "", "mail": "", "provisional": "",
                "_code": int(code), "_off": DOS_OFFICE_ORDER.index(office),
            })
            codes.add(code)
    if not rows:
        raise ValueError("no %s general rows found in %s" % (DOS_COUNTY_CODE, path))
    rows.sort(key=lambda x: (x["_code"], x["_off"], x["candidate"]))
    return rows, n_read, len(codes)


def read_clarity_rows(path):
    """Read the HTML-mode county-level CSV (Clarity spellings) and return
    rows standardized for cross-checking against the DOS precinct sums:
    (county, office, district, party, candidate, votes)."""
    OFFICE_RX = [
        (re.compile(r"^United States Senator$", re.I), ("U.S. Senate", "")),
        (re.compile(r"^Governor and Lieutenant Governor$", re.I), ("Governor", "")),
        (re.compile(r"^Representative in Congress (\d+)\w*(?:\s+District)?$", re.I), None),
        (re.compile(r"^Senator in the General Assembly (\d+)\w*(?:\s+District)?$", re.I), None),
        (re.compile(r"^Representative in the General Assembly (\d+)\w*(?:\s+District)?$", re.I), None),
    ]
    # Clarity concatenated tickets / trimmed names -> certified ENR spelling
    CLARITY_CANDMAP = {
        "John Fetterman": "John K. Fetterman",
        "Mehmet Oz": "Mehmet C. Oz",
        "Josh Shapiro Austin Davis": "Joshua D. Shapiro",
        "Douglas V. Mastriano Carrie Lewis DelRosso":
            "Douglas V. Mastriano",
        "Matt Hackenburg Tim McMaster": "Jonathan Matthew Hackenburg",
        "Christina DiGiulio Michael Bagdes-Canning": "Christina Pk Digiulio",
        "Joe Soloski Nicole Shultz": "Joseph P. Soloski",
        "David Galluch": "David Lewczyk Galluch",
        "John Hayes": "John Vincent Hayes Iii",
        "Tim Kearney": "Timothy P. Kearney",
        "Cathy Spahr": "Catherine E. Spahr",
        "Craig Williams": "Wendell Craig Williams",
        "Leanne Krueger": "Leanne T. Krueger",
        "Ed Mongelluzzo": "Edward A. Mongelluzzo",
        "Michelle Mattus": "Michelle Stock Mattus",
        "Mike Zabel": "Michael P. Zabel",
        "Alfe Goodwin": "Alfeia Devaughn Goodwin",
        "Nichole Missino": "Nichole Leann Missino",
        "Bill Foster": "William R. Foster",
        "Greg Vitali": "Gregory S. Vitali",
        "Kim Razzano": "Kimberly J. Razzano",
        "Lisa A. Borowski": "Lisa Borowski",
        "Chris Quinn": "Christopher B. Quinn",
        # already matching: Erik Gerhardt, Richard L. Weiss, Daniel Wassmer,
        # Mary Gay Scanlon, Anthony Hardy Williams, Frank Agovino,
        # Carol Kazeem, Ruth Moton, David M. Delloso, Kenneth P. Rucci,
        # Gina H. Curry, Jennifer O'Mara, Regina G. Young,
        # Joanna E. McClinton, Edward T. Clifford III (Iii handled below if wanted)
        "Edward T. Clifford III": "Edward T. Clifford Iii",
    }
    out = []
    with open(path, newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            if r["office"] in ("Registered Voters", "Ballots Cast"):
                continue    # countywide-only META figures; DOS has none
            office = r["office"]
            district = r["district"]
            for rx, fixed in OFFICE_RX:
                m = rx.match(office)
                if not m:
                    continue
                if fixed:
                    office, district = fixed
                else:
                    office = ("U.S. House" if "Congress" in rx.pattern
                              else "State Senate" if "Senator in" in rx.pattern
                              else "State House")
                    district = str(int(m.group(1)))
                break
            else:
                # local/other contests (e.g. the Haverford special
                # commissioner) stay unstandardized; DOS has no local rows
                pass
            cand = CLARITY_CANDMAP.get(r["candidate"].strip(),
                                       r["candidate"].strip())
            out.append({
                "county": r["county"], "office": office, "district": district,
                "party": r["party"], "candidate": cand,
                "int": int(r["votes"]),
            })
    return out


def cross_check(rows, csv_path, label="county.csv", clarity=False):
    """Sum DOS precinct votes per (office, district, party, candidate) and
    compare to the anchor CSV rows.  Returns (n_checked, mismatches)."""
    if clarity:
        anchor_rows = read_clarity_rows(csv_path)
    else:
        anchor_rows = []
        with open(csv_path, newline="", encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                if r["county"] != "Delaware":
                    continue
                if r["office"] in ("Registered Voters", "Ballots Cast"):
                    continue  # DOS bulk file carries no RV / Ballots Cast
                anchor_rows.append({
                    "office": r["office"], "district": r["district"],
                    "party": r["party"], "candidate": r["candidate"],
                    "int": int(r["votes"]),
                })
    ours = {}
    for r in rows:
        k = (r["office"], r["district"], r["party"], r["candidate"])
        ours[k] = ours.get(k, 0) + r["votes"]
    anchor = {}
    for r in anchor_rows:
        k = (r["office"], r["district"], r["party"], r["candidate"])
        anchor[k] = anchor.get(k, 0) + r["int"]
    n = mismatches = 0
    for k in sorted(anchor):
        if clarity and "Special Election" in k[0]:
            print("  excluded (countywide-only; DOS bulk has no local rows): "
                  "%s %s %s=%d" % (k[0], k[2], k[3], anchor[k]))
            continue
        if ours.get(k) == anchor[k]:
            n += 1
        else:
            mismatches += 1
            print("  MISMATCH vs %s: ours %s vs anchor %s for %s"
                  % (label, ours.get(k, 0), anchor[k], k))
    return n, mismatches


def dos_main(argv):
    ap = argparse.ArgumentParser(
        description="Delaware 2022 general DOS precinct-returns mode")
    ap.add_argument("--dos", action="store_true")
    ap.add_argument("source")
    ap.add_argument("output")
    ap.add_argument("county_csv", nargs="?",
                    help="ENR-pegged consolidated county CSV "
                         "(2022/20221108__pa__general__county.csv)")
    ap.add_argument("clarity_csv", nargs="?",
                    help="HTML-mode county-level CSV (Clarity spellings)")
    args = ap.parse_args(argv)

    rows, n_read, n_codes = read_dos_precincts(args.source)
    precincts = sorted({r["precinct"] for r in rows})
    print("DOS: %d source rows -> %d output rows, %d precincts, "
          "%d unique (precinct,contest,candidate) keys"
          % (n_read, len(rows), len(precincts),
             len({(r["precinct"], r["office"], r["district"], r["party"],
                   r["candidate"]) for r in rows})))

    ok = True
    if args.county_csv:
        n, mism = cross_check(rows, args.county_csv, "ENR county.csv")
        print("ENR county.csv cross-check: %d exact, %d mismatch(es)" % (n, mism))
        ok = ok and mism == 0
    if args.clarity_csv:
        n, mism = cross_check(rows, args.clarity_csv, "Clarity county html",
                              clarity=True)
        print("Clarity (HTML-mode) cross-check: %d exact, %d mismatch(es) "
              "(expected 0; the certified DOS file and the Clarity Official "
              "Cumulative report tabulate the same values)" % (n, mism))
        ok = ok and mism == 0

    with open(args.output, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=DOS_FIELDNAMES, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({k: r[k] for k in DOS_FIELDNAMES})
    print("wrote %d rows -> %s" % (len(rows), args.output))
    return 0 if ok else 1


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("input")
    ap.add_argument("output")
    ap.add_argument("--county", default="Delaware")
    args = ap.parse_args(argv)

    rows, problems, ncontests, contest_totals, (rv, bc) = parse(args.input, args.county)
    with open(args.output, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=COLS)
        w.writeheader()
        w.writerows(rows)

    print("wrote %d rows (%d contests; RV=%d BC=%d) to %s"
          % (len(rows), ncontests, rv, bc, args.output))
    if problems:
        print("VALIDATION PROBLEMS (%d):" % len(problems), file=sys.stderr)
        for p in problems:
            print("  " + p, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    if len(sys.argv) > 2 and sys.argv[1] == "--dos":
        sys.exit(dos_main(sys.argv[2:]))
    sys.exit(main())