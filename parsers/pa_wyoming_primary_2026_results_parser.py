#!/usr/bin/env python3
"""Wyoming County, PA 2026 primary (May 19, 2026) results parser.

Builds both files for this county in the 2026 primary set:

* PRECINCT -- 2026/counties/20260519__pa__primary__wyoming__precinct.csv
  from the PA DOS certified bulk precinct-returns export, split per county
  at work2026p/dos/wyoming__dos_precinctreturns.txt (county code 66).
  Wyoming County's own source ("Wyoming County 2026-Summary-Primary-
  Election-Results-5-19-26-OFFICIAL.pdf") is a countywide-only Dominion
  "Statement of Votes Cast by Geography" summary (28 x countywide "All
  Precincts" pages, no per-precinct body), so per the coordinator's brief
  the DOS certified export is the sole per-precinct source.

* COUNTY -- 2026/counties/20260519__pa__primary__wyoming__county.csv
  from the county summary PDF above (dated 2026-05-22, official).
  That PDF's embedded text layer is corrupted (pdftotext/pdfplumber emit
  "0A47%", ".1 97", "Iotal" fragments), so its rows were transcribed from
  the page images into work2026p/wyoming/wyoming_summary_transcribed.txt;
  every transcribed row satisfies ED+MI+PR == votes and the candidate
  columns sum to the report's printed Total rows (checked at parse time).
  ED/MI/PR map to the election_day/mail/provisional columns.

DOS quirks documented (2022/2024 precedent): the certified export carries a
single votes cell -- no write-in rows, no Registered Voters / Ballots Cast
rows, no election-day/absentee/mail/provisional breakdowns and no State
Committee rows -- so the precinct output is the plain 7-column format and
omits both State Committee contests and any Write-In rows.  The two State
Committee races and all Write-In totals are carried by the county summary
file instead (Republican State Committee prints named candidates; the
Democratic State Committee race on both ballots drew write-ins only).

Contests per ballot:
  Governor, Lieutenant Governor, U.S. House 9, State Senate 20,
  State House 110, Member of the Democratic/Republican State
  Committee, plus 25 precinct-level "Democratic Committee Person NN"
  races (Vote for 2, write-in-only this cycle).  The Committee Person
  races are one-per-precinct local party offices; the countywide summary
  is their only source and its candidate value cells are unreliable
  (several row labels show no votes value beside the percentage), so
  those local races are omitted entirely and documented here.

Precinct naming: DOS field 22 prints the municipality name (upper case)
with qualifier/split codes in fields 23/24 (W=ward, D=township district,
X=split).  Two DOS municipalities repeat with no distinguishing name:
Meshoppen (codes 130/140) and Nicholson (160/170).  DOS field 28 is a
municipality index x 5 in the county's ballot order (which runs Borough
before Township of the same name, as the Tunkhannock Boro 100 / Twp 105
pair confirms), so 140=Meshoppen Borough, 130=Meshoppen Township,
170=Nicholson Borough, 160=Nicholson Township.  The mapping matches the
county's own 2022-2025 precinct files, and the 2026 vote magnitudes agree
(Meshoppen/Nicholson Boroughs are the smaller of each pair).  Windham is
split by school district: "WINDHAM   School District 1/2" -> "Windham
Township School District 1/2" (School District 2's split recorded no
votes in any contest).  The Dominion summary prints "28 precincts"
because Windham's two school-district splits are one Dominion geography;
the DOS certified export gives 29 codes.

Verification (run by this script in default/all mode): every contest
covered by the precinct file sums to the countywide anchor
(2026/20260519__pa__primary__county.csv) and to the county summary PDF
exactly for every shared office/party/candidate (Governor Shapiro
1478 = Garrity 3230, Lt Governor Davis 1393 / Ventre 1203 / Richey 1963,
U.S. House Wallace 1355 / Meuser 3260, State Senate Jackie Baker 1376 /
Lisa Baker 2103 / Meyers 1484, State House Hemann 1423 / Pickett 3390);
the State Committee county rows (Koneski 1469, Eggleston 1971, DEM
write-ins 111) also match the anchor.  No disagreements.

Usage:
    python parsers/pa_wyoming_primary_2026_results_parser.py [all|precinct|county] \\
        [dos_txt] [summary_txt] [precinct_out] [county_out]
"""

import csv
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
COUNTY = "Wyoming"

DOS_TXT = os.path.join(REPO, "work2026p", "dos",
                       "wyoming__dos_precinctreturns.txt")
SUMMARY_TXT = os.path.join(REPO, "work2026p", "wyoming",
                           "wyoming_summary_transcribed.txt")
PRECINCT_OUT = os.path.join(REPO, "2026", "counties",
                            "20260519__pa__primary__wyoming__precinct.csv")
COUNTY_OUT = os.path.join(REPO, "2026", "counties",
                          "20260519__pa__primary__wyoming__county.csv")
ANCHOR = os.path.join(REPO, "2026", "20260519__pa__primary__county.csv")

P7 = ["county", "precinct", "office", "district", "party", "candidate", "votes"]
# countywide file format (no precinct column, as in the other 2026 county files)
C9 = ["county", "office", "district", "party", "candidate", "votes",
      "election_day", "mail", "provisional"]

# DOS precinct code -> (name, qualifier, split) exactly as the certified
# export prints them; the parser aborts on any mismatch here.
DOS_PRECINCTS = {
    "10": ("BRAINTRIM", "", ""),
    "20": ("CLINTON", "", ""),
    "30": ("EATON", "", ""),
    "40": ("EXETER", "", ""),
    "50": ("FACTORYVILLE W 1", "W", "1"),
    "60": ("FACTORYVILLE W 2", "W", "2"),
    "70": ("FALLS", "", ""),
    "90": ("FORKSTON", "", ""),
    "100": ("LACEYVILLE", "", ""),
    "110": ("LEMON", "", ""),
    "120": ("MEHOOPANY", "", ""),
    "140": ("MESHOPPEN", "", ""),       # municipality index 55 (Boro)
    "130": ("MESHOPPEN", "", ""),       # municipality index 60 (Township)
    "150": ("MONROE", "", ""),
    "170": ("NICHOLSON", "", ""),       # municipality index 70 (Boro)
    "160": ("NICHOLSON", "", ""),       # municipality index 75 (Township)
    "190": ("NORTH BRANCH", "", ""),
    "200": ("NORTHMORELAND", "", ""),
    "210": ("NOXEN", "", ""),
    "220": ("OVERFIELD", "", ""),
    "230": ("TUNKHANNOCK D 1", "D", "1"),
    "240": ("TUNKHANNOCK D 2", "D", "2"),
    "250": ("TUNKHANNOCK W 1", "W", "1"),
    "260": ("TUNKHANNOCK W 2", "W", "2"),
    "270": ("TUNKHANNOCK W 3", "W", "3"),
    "280": ("TUNKHANNOCK W 4", "W", "4"),
    "290": ("WASHINGTON", "", ""),
    "300": ("WINDHAM   School District 1", "", ""),
    "305": ("WINDHAM   School District 2", "", ""),
}

# Canonical output names (repo style for this county's files).
PRECINCT_NAMES = {
    "10": "Braintrim Township",
    "20": "Clinton Township",
    "30": "Eaton Township",
    "40": "Exeter Township",
    "50": "Factoryville Borough Ward 1",
    "60": "Factoryville Borough Ward 2",
    "70": "Falls Township",
    "90": "Forkston Township",
    "100": "Laceyville Borough",
    "110": "Lemon Township",
    "120": "Mehoopany Township",
    "140": "Meshoppen Borough",
    "130": "Meshoppen Township",
    "150": "Monroe Township",
    "170": "Nicholson Borough",
    "160": "Nicholson Township",
    "190": "North Branch Township",
    "200": "Northmoreland Township",
    "210": "Noxen Township",
    "220": "Overfield Township",
    "230": "Tunkhannock Township District 1",
    "240": "Tunkhannock Township District 2",
    "250": "Tunkhannock Borough Ward 1",
    "260": "Tunkhannock Borough Ward 2",
    "270": "Tunkhannock Borough Ward 3",
    "280": "Tunkhannock Borough Ward 4",
    "290": "Washington Township",
    "300": "Windham Township School District 1",
    "305": "Windham Township School District 2",
}

# DOS (office, party, LAST, FIRST, middle) -> candidate name as printed on
# the county summary; every DOS row for these offices must hit this map.
DOS_CANDIDATES = {
    ("GOV", "DEM", "SHAPIRO", "JOSHUA", "D"): "JOSH SHAPIRO",
    ("GOV", "REP", "GARRITY", "STACY", "LORRAINE"): "STACY GARRITY",
    ("LTG", "DEM", "DAVIS", "AUSTIN", ""): "AUSTIN DAVIS",
    ("LTG", "REP", "VENTRE", "JOHN", ""): "JOHN VENTRE",
    ("LTG", "REP", "RICHEY", "JASON", "L"): "JASON RICHEY",
    ("USC", "DEM", "WALLACE", "RACHEL", "LILLIAN"): "RACHEL WALLACE",
    ("USC", "REP", "MEUSER", "DANIEL", "PHILIP"): "DAN MEUSER",
    ("STS", "DEM", "BAKER", "JACLYN", "ESTELLE"): "JACKIE BAKER",
    ("STS", "REP", "BAKER", "ELISABETH", "J"): "LISA BAKER",
    ("STS", "REP", "MEYERS", "TYLER", "JAMES"): "TYLER MEYERS",
    ("STH", "DEM", "HEMANN", "PAMELA", "J"): "PAMELA J HEMANN",
    ("STH", "REP", "PICKETT", "TINA", ""): "TINA PICKETT",
}

DOS_OFFICES = {
    "GOV": ("Governor", ""),
    "LTG": ("Lieutenant Governor", ""),
    "USC": ("U.S. House", "9"),
    "STS": ("State Senate", "20"),
    "STH": ("State House", "110"),
}
OFFICE_ORDER = ["Governor", "Lieutenant Governor", "U.S. House",
                "State Senate", "State House"]

# Precinct codes in output order, offices in ballot order, DEM before REP.
PARTY_ORDER = {"DEM": 0, "REP": 1}


# --------------------------------------------------------------------------
# DOS -> precinct rows
# --------------------------------------------------------------------------

def parse_dos(path=DOS_TXT):
    rows = []
    with open(path, newline="", encoding="utf-8", errors="replace") as fh:
        for rec in csv.reader(fh):
            if len(rec) < 37 or rec[1] != "P" or rec[2].replace(".0", "") != "66":
                continue
            code = str(int(float(rec[3])))
            if code not in DOS_PRECINCTS:
                raise SystemExit("unmapped DOS precinct code %r" % code)
            observed = (rec[22].strip(), rec[23], rec[24])
            if observed != DOS_PRECINCTS[code]:
                raise SystemExit("precinct %r prints %r, expected %r"
                                 % (code, observed, DOS_PRECINCTS[code]))
            key = (rec[8], rec[9], rec[11].strip().upper(),
                   rec[12].strip().upper(), rec[13].strip().upper())
            if key not in DOS_CANDIDATES:
                raise SystemExit("unmapped DOS candidate %r" % (key,))
            office, district = DOS_OFFICES[rec[8]]
            rows.append((int(code), OFFICE_ORDER.index(office),
                         PARTY_ORDER[rec[9]], COUNTY, PRECINCT_NAMES[code],
                         office, district, rec[9], DOS_CANDIDATES[key],
                         str(int(float(rec[15])))))
    rows.sort()
    # sanity: every precinct got exactly the expected 12 contest rows
    import collections
    per = collections.Counter(r[4] for r in rows)
    bad = {p: n for p, n in per.items() if n != 12}
    if bad:
        raise SystemExit("precincts with unexpected row counts: %r" % bad)
    return [(c, p, o, d, pa, cand, v)
            for (_code, _oi, _pi, c, p, o, d, pa, cand, v) in rows]


# --------------------------------------------------------------------------
# summary transcription -> county rows
# --------------------------------------------------------------------------

# transcribed header -> (office, district, party)
CONTESTS = {
    "GOVERNOR DEM": ("Governor", "", "DEM"),
    "GOVERNOR REP": ("Governor", "", "REP"),
    "LIEUTENANT GOVERNOR DEM": ("Lieutenant Governor", "", "DEM"),
    "LIEUTENANT GOVERNOR REP": ("Lieutenant Governor", "", "REP"),
    "REPRESENTATIVE IN CONGRESS DEM": ("U.S. House", "9", "DEM"),
    "REPRESENTATIVE IN CONGRESS REP": ("U.S. House", "9", "REP"),
    "SENATOR IN GENERAL ASSEMBLY DEM": ("State Senate", "20", "DEM"),
    "SENATOR IN GENERAL ASSEMBLY REP": ("State Senate", "20", "REP"),
    "REPRESENTATIVE IN THE GENERAL ASSEMBLY D DEM": ("State House", "110", "DEM"),
    "REPRESENTATIVE IN THE GENERAL ASSEMBLY REP": ("State House", "110", "REP"),
    "MEMBER OF THE DEMOCRATIC STATE COMMITTEE":
        ("Member of Democratic State Committee", "", "DEM"),
    "MEMBER OF THE REPUBLICAN STATE COMMITTEE":
        ("Member of Republican State Committee", "", "REP"),
}


def parse_summary(path=SUMMARY_TXT):
    """Return (rows, disputes). 'Total' lines validate column sums."""
    rows, disputes = [], []
    order = {c: i for i, c in enumerate(CONTESTS.values())}
    contest = None
    cand_sums = None
    for line in open(path, encoding="utf-8"):
        line = line.rstrip("\n")
        # contest headers ("## ...") must be matched before the general
        # "#" comment skip, which would otherwise swallow them too
        if line.startswith("##"):
            contest = CONTESTS.get(line[2:].strip())
            if contest is None:
                raise SystemExit("unmapped summary contest %r" % line)
            cand_sums = []
            continue
        if not line or line.startswith("#"):
            continue
        name, votes, ed, mi, pr = line.split("\t")
        votes, ed, mi, pr = int(votes), int(ed), int(mi), int(pr)
        if sum((ed, mi, pr)) != votes:
            raise SystemExit("breakdown disagrees for %r: %d != %d"
                             % (name, sum((ed, mi, pr)), votes))
        if name == "Total":
            if cand_sums and tuple(cand_sums) != (votes, ed, mi, pr):
                disputes.append("%s: columns sum to %r, report Total %r"
                                % (contest, cand_sums, (votes, ed, mi, pr)))
            cand_sums = None
            continue
        if cand_sums is not None:
            if len(cand_sums) == 0:
                cand_sums = [0, 0, 0, 0]
            cand_sums = [a + b for a, b in
                         zip(cand_sums, (votes, ed, mi, pr))]
        office, district, party = contest
        rows.append((order[contest], PARTY_ORDER[party],
                     office, district, party, name, votes, ed, mi, pr))
    rows.sort()
    return [(o, d, pa, name, v, ed, mi, pr)
            for (_o, _p, o, d, pa, name, v, ed, mi, pr) in rows], disputes


# --------------------------------------------------------------------------
# verification
# --------------------------------------------------------------------------

TITLE = {
    "JOSH SHAPIRO": "Josh Shapiro", "STACY GARRITY": "Stacy Garrity",
    "AUSTIN DAVIS": "Austin Davis", "JOHN VENTRE": "John Ventre",
    "JASON RICHEY": "Jason Richey", "RACHEL WALLACE": "Rachel Wallace",
    "DAN MEUSER": "Dan Meuser", "JACKIE BAKER": "Jackie Baker",
    "LISA BAKER": "Lisa Baker", "TYLER MEYERS": "Tyler Meyers",
    "PAMELA J HEMANN": "Pamela J Hemann", "TINA PICKETT": "Tina Pickett",
    "TAD KONESKI": "Tad Koneski", "WILLIAM EGGLESTON": "William Eggleston",
}
PARTY_FULL = {"DEM": "Democratic", "REP": "Republican"}


def anchor_totals():
    tt = {}
    with open(ANCHOR, newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            if r["county"] != "WYOMING":
                continue
            # the anchor spells parties out in full already
            p = r["party"].strip()
            key = (r["office"], r["district"].strip(),
                   PARTY_FULL[p] if p in PARTY_FULL else p,
                   r["candidate"].upper())
            tt[key] = int(r["votes"])
    return tt


def verify(precinct_rows, county_rows, problems):
    anchor = anchor_totals()

    # 1. precinct sums vs anchor
    sums = {}
    for (_c, p, o, d, pa, cand, v) in precinct_rows:
        k = (o, d, pa, cand.upper())
        sums[k] = sums.get(k, 0) + int(v)
    for k in sorted(sums, key=lambda k: (OFFICE_ORDER.index(k[0]), k[1], k[2])):
        o, d, pa, cand = k
        ak = (o, d, PARTY_FULL[pa], cand)
        if ak not in anchor:
            problems.append("precinct sum key %r not in anchor" % (ak,))
        elif sums[k] != anchor[ak]:
            problems.append("precinct sum %s %s %s: %d vs anchor %d"
                            % (o, pa, cand, sums[k], anchor[ak]))

    # 2. county rows vs anchor (write-in rows have no anchor counterpart --
    #    the anchor lists named candidates only -- so absence is expected)
    for (o, d, pa, cand, v, *_r) in county_rows:
        ak = (o, d, PARTY_FULL[pa], cand.upper())
        if ak in anchor:
            if int(v) != anchor[ak]:
                problems.append("county row %s %s %s: %d vs anchor %d"
                                % (o, pa, cand, int(v), anchor[ak]))
        elif cand.startswith("Write"):
            print("note: write-in row %s %s has no anchor counterpart (expected)"
                  % (o, pa))
        else:
            problems.append("county row %s %s %s not in anchor" % (o, pa, cand))

    # 3. county rows vs precinct sums (same key space)
    for (o, d, pa, cand, v, *_r) in county_rows:
        pk = (o, d, pa, cand.upper())
        if pk in sums and int(v) != sums[pk]:
            problems.append("county %s %s %s=%d but precinct sum=%d"
                            % (o, pa, cand, int(v), sums[pk]))


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------

def main(argv):
    mode = argv[1] if len(argv) > 1 else "all"
    dos = argv[2] if len(argv) > 2 else DOS_TXT
    summ = argv[3] if len(argv) > 3 else SUMMARY_TXT
    pout = argv[4] if len(argv) > 4 else PRECINCT_OUT
    cout = argv[5] if len(argv) > 5 else COUNTY_OUT

    precinct_rows = county_rows = None
    if mode in ("all", "precinct"):
        precinct_rows = parse_dos(dos)
        with open(pout, "w", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(P7)
            for row in precinct_rows:
                w.writerow(row)
        print("wrote %s (%d rows)" % (pout, len(precinct_rows)))
    if mode in ("all", "county"):
        county_rows, disputes = parse_summary(summ)
        for d in disputes:
            print("DISPUTE: %s" % d)
        with open(cout, "w", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(C9)
            for (o, d, pa, cand, v, ed, mi, pr) in county_rows:
                w.writerow([COUNTY, o, d, pa, cand, v, ed, mi, pr])
        print("wrote %s (%d rows)" % (cout, len(county_rows)))
    if mode == "all" or (precinct_rows and county_rows):
        problems = []
        verify(precinct_rows, county_rows, problems)
        for p in problems:
            print("PROBLEM:", p)
        print("VERIFICATION: %d problem(s)" % len(problems))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
