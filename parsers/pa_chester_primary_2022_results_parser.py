#!/usr/bin/env python3
"""Chester County, PA 2022 Primary (May 17, 2022) — precinct-level results parser.

Source situation (documented):
  * The county's certified reports in
    ~/code/openelections-sources-pa/2022/primary/
      - "Chester PA 2022 Primary_Official_Results_ExceptREPSenator_...pdf"
      - "Chester PA Recount_2022PrimaryOfficial_Results_REPSenator.pdf"
    are Electionware "Summary Results Report" printouts (OFFICIAL RESULTS,
    168 + 2 pages, 230 of 230 precincts reporting).  They are COUNTY-LEVEL
    ONLY: contest sections carry one county TOTAL per candidate plus the
    countywide STATISTICS block.  There are no per-precinct vote rows
    anywhere in either file (verified by reading the extracted text;
    copies at work2022p/chester/main.txt and recount.txt).  The county
    does publish per-committee-district results for its County Committee /
    State Committee races in the same files, but those too are countywide
    aggregates per committee district, not per-precinct.

  Precinct-level figures therefore come from the PA Department of State
  official certified precinct-returns bulk file for the primary:
      ElectionReturns_2022_Primary_PrecinctReturns.txt
  (ASCII comma-delimited, quoted fields; election type "P"; Chester =
  county code 15; 9,481 rows across 232 precinct codes / 230 named
  precincts).  A verbatim Chester slice of the bulk file is kept at
  work2022p/chester/chester_prcres_slice.txt.  (Same fallback as the
  Cameron 2022 primary parser.)

Recount handling (REP U.S. Senate, Oz vs McCormick, June 2022):
  The DOS certified bulk file equals the POST-RECOUNT certified results:
  its per-candidate county sums (Barnette 15,666 / Oz 16,751 / McCormick
  18,793 / Bartos 2,987 / Sean Gale 2,045 / Sands 1,671 / Bochetto 868)
  match the county's standalone recount report
  "Chester PA Recount_2022PrimaryOfficial_Results_REPSenator.pdf" for
  every named candidate exactly.  The main county report deliberately
  excludes the GOP U.S. Senate contest ("...ExceptREPSenator").

Consequences (inherent to the DOS bulk source, not parsing losses):
  * No election-day / mail / provisional breakdown -> 7-column output
    header (county,precinct,office,district,party,candidate,votes).
  * No per-precinct write-in rows.  County-level write-in totals exist in
    the county reports (e.g. GOP U.S. Senate 325, DEM U.S. Senate 134,
    DEM Governor 359) and are reconciled in --verify (DOS named-candidate
    sums + write-ins == county PDF contest totals), but they cannot be
    assigned to precincts, so per the no-invention rule no Write Ins rows
    are emitted.
  * No State Committee / County Committee contests in the DOS bulk file;
    their precinct-level figures do not exist in any available source, so
    those contests are omitted.
  * No per-precinct Registered Voters / Ballots Cast (the county reports
    carry these countywide only: RV 374,833 with DEM 156,135 / REP
    150,772 / nonpartisan 67,926; BC 119,984).
  * The DOS file splits Birmingham 2 between Congress 5/6 and Phoenixville
    M-1 between State House 155/157 (and East Bradford N-1 carries a
    "(Cong 16)" copy): the two halves are summed per contest entry, giving
    230 named precincts matching the county's 2022 general file.
  * The uncontested STH 13 contest row (Chamir James) carries an empty
    party field in the DOS file; the contest is the DEM primary ballot
    contest per the county report, so party DEM is applied (one row set).

Candidate names follow the county certified reports' printings (the
county is the official source; the ENR county anchor uses formal ENR
variants such as "Douglas V. Mastriano" - same people).

Usage:
    python parsers/pa_chester_primary_2022_results_parser.py <dos_prcres_txt> <out.csv>
    python parsers/pa_chester_primary_2022_results_parser.py --verify <dos_prcres_txt> <out.csv>

If <dos_prcres_txt> is omitted the parser looks for the Chester slice at
work2022p/chester/chester_prcres_slice.txt relative to the repo root.
"""
from __future__ import annotations

import csv
import os
import re
import sys

COUNTY_NAME = "Chester"
COUNTY_CODE = "15"
ELECTION_TYPE = "P"  # DOS bulk-file election type for the primary

HEADER = ["county", "precinct", "office", "district", "party", "candidate", "votes"]

OFFICES = {  # DOS office code -> (standard office name, district source field)
    "USS": ("U.S. Senate", None),
    "GOV": ("Governor", None),
    "LTG": ("Lieutenant Governor", None),
    "USC": ("U.S. House", 18),          # congressional district field
    "STS": ("State Senate", 19),        # state senate district field
    "STH": ("State House", 20),         # state house district field
}

OFFICE_ORDER = ["U.S. Senate", "Governor", "Lieutenant Governor", "U.S. House",
                "State Senate", "State House"]

# DOS candidate (Last, First, Middle, Suffix) -> printed name as on the
# county certified reports.
NAME_FIX = {
    ("SHAPIRO", "JOSHUA", "D", ""): "Josh Shapiro",
    ("MASTRIANO", "DOUGLAS", "V", ""): "Douglas V. Mastriano",
    ("BARLETTA", "LOUIS", "", ""): "Lou Barletta",
    ("WHITE", "DAVID", "J", ""): "Dave White",
    ("GALE", "JOSEPH", "CHARLES", ""): "Joe Gale",
    ("MCSWAIN", "WILLIAM", "M", ""): "Bill McSwain",
    ("CORMAN", "JACOB", "DOYLE", "III"): "Jake Corman",
    ("HART", "MELISSA", "A", ""): "Melissa Hart",
    ("GEROW", "CHARLES", "R", ""): "Charlie Gerow",
    ("ZAMA", "NCHE", "", ""): "Nche Zama",
    ("SIMS", "BRIAN", "", ""): "Brian Sims",
    ("DAVIS", "AUSTIN", "", ""): "Austin Davis",
    ("SOSA", "RAYMOND", "L", ""): "Ray Sosa",
    ("SCHILLINGER", "CLARICE", "D", ""): "Clarice Schillinger",
    ("DANIELS", "THEODORE", "", ""): "Teddy Daniels",
    ("SACCONE", "RICHARD", "", ""): "Rick Saccone",
    ("DELROSSO", "CARRIE", "", ""): "Carrie Lewis DelRosso",
    ("DIAMOND", "RUSSELL", "H", ""): "Russ Diamond",
    ("BROWN", "JOHN", "A", ""): "John Brown",
    ("FRYE", "CHRISTOPHER", "C", "JR"): "Chris Frye",
    ("JONES", "JAMES", "E", ""): "James Earl Jones",
    ("COLEMAN", "JEFFREY", "H.", ""): "Jeff Coleman",
    ("MUTH", "KATIE", "J", ""): "Katie Muth",
    ("FLORIO", "JESSICA", "B", ""): "Jessica Florio",
    ("HOULAHAN", "CHRISTINA", "JAMPOLER", ""): "Chrissy Houlahan",
    ("SCANLON", "MARY GAY", "", ""): "Mary Gay Scanlon",
    ("GALLUCH", "DAVID", "LEWCZYK", ""): "David Galluch",
    ("VOGEL", "RONALD", "CHRISTOPHER", ""): "Ron Vogel",
    ("CIARROCCHI", "GUY", "LOUIS", ""): "Guy Ciarrocchi",
    ("FANELLI", "STEPHEN", "A", ""): "Steve Fanelli",
    ("MAURO", "REGINA", "", ""): "Regina Mauro",
    ("KENYATTA", "MALCOLM", "", ""): "Malcolm Kenyatta",
    ("LAMB", "CONOR", "J", ""): "Conor Lamb",
    ("FETTERMAN", "JOHN", "K", ""): "John Fetterman",
    ("KHALIL", "ALEXANDRIA", "GLORIA", ""): "Alex Khalil",
    ("BARNETTE", "KATHY", "J", ""): "Kathy Barnette",
    ("BARTOS", "JEFFREY", "A", ""): "Jeff Bartos",
    ("OZ", "MEHMET", "C", ""): "Mehmet Oz",
    ("SANDS", "CARLA", "HERD", ""): "Carla Sands",
    ("MCCORMICK", "DAVID", "H", ""): "Dave McCormick",
    ("GALE", "SEAN", "PETER", ""): "Sean Gale",
    ("BOCHETTO", "GEORGE", "A", ""): "George Bochetto",
    ("JAMES", "CHAMIR", "M", ""): "Chamir James",
    ("HOWARD", "KRISTINE", "C.", ""): "Kristine C. Howard",
    ("SPAHR", "CATHERINE", "E", ""): "Cathy Spahr",
    ("SHUSTERMAN", "MELISSA", "", ""): "Melissa Shusterman",
    ("OTTEN", "DANIELLE", "FRIEL", ""): "Danielle Friel Otten",
    ("FRIEL", "PAUL", "F", "JR"): "Paul Friel",
    ("PIELLI", "CHRISTOPHER", "", ""): "Chris Pielli",
    ("WILLIAMS", "DAN", "K", ""): "Dan Williams",
    ("SAPPEY", "CHRISTINA", "D.", ""): "Christina Sappey",
    ("SCRIBNER", "KYLE", "P", ""): "Kyle Scribner",
    ("CILIBERTI", "CARMELA", "", ""): "Carmela Ciliberti",
    ("WILLIAMS", "WENDELL", "CRAIG", ""): "Craig Williams",
    ("HENNESSEY", "TIMOTHY", "F.", ""): "Tim Hennessey",
    ("NEWMAN", "GAIL", "A.", ""): "Gail Newman",
    ("SPENCER", "LEON", "R.", "JR."): "Leon Spencer",
    ("VANDERWAAL", "HEIDI", "R.", ""): "Heidi VanderWaal",
    ("LAWRENCE", "JOHN", "ADDA", ""): "John Lawrence",
    ("HENSEL", "DALE", "L", ""): "Dale Hensel",
    ("DICRANIAN", "MELISSA", "C", ""): "Melissa Dicranian",
    ("MARVIN", "SARAH", "K", ""): "Sarah Marvin",
}

# Contests whose ballot section carries an empty party field in the DOS file
# (uncontested single-party State House primaries seen in the county report).
PARTY_FIX = {"2022C0681": "DEM"}  # State House 13: Chamir James (DEM primary)

# ---------------------------------------------------------------------------
# Precinct names: DOS all-caps Electionware codes -> the 230 precinct names
# already used by the county's 2022 general file
# (2022/counties/20221108__pa__general__chester__precinct.csv).  232 DOS
# precinct codes collapse onto 230 names: Birmingham 2 is split between
# Congress 5 and 6, East Bradford N-1 carries a "(Cong 16)" copy, and
# Phoenixville M-1 is split between State House 155 and 157.
# ---------------------------------------------------------------------------

DIR = {"EAST": "E", "WEST": "W", "NORTH": "N", "SOUTH": "S", "MIDDLE": "M",
       "CHATHAM": "Ch"}
FULL = {"E": "East", "W": "West", "N": "North", "S": "South", "M": "Middle"}
ABBR = set(DIR.values())
ABBRV_TOWNS = {"BRADFORD", "BRANDYWINE", "COVENTRY", "DOWNINGTOWN", "FALLOWFIELD",
               "GOSHEN", "MARLBOROUGH", "NANTMEAL", "NOTTINGHAM", "PIKELAND",
               "VINCENT", "WHITELAND", "SADSBURY", "COATESVILLE"}
SPECIAL = {"HONEY BROOK": "Honey Brook Borough"}


def prettify_precinct(name: str) -> str:
    base = re.sub(r"\s*\((?:Cong|STH)[^)]*\)\s*$", "", name).strip()
    m = re.match(r"^KENNETT P (\d)$", base)
    if m:
        return "Kennett Township " + m.group(1)
    m = re.match(r"^HONEY BROOK P (\d)$", base)
    if m:
        return "Honey Brook Township " + m.group(1)
    if base in SPECIAL:
        return SPECIAL[base]
    toks = [DIR.get(t.upper(), t) for t in base.split()
            if t.upper() not in ("X", "P", "D", "W")]
    out = []
    i = 0
    while i < len(toks):
        t = toks[i]
        nxt = toks[i + 1] if i + 1 < len(toks) else None
        if nxt and t in ABBR and re.fullmatch(r"\d+", nxt):
            out.append(t + "-" + str(int(nxt))); i += 2; continue
        if nxt and t.isdigit() and nxt in ABBR:
            out.append(str(int(t)) + "-" + nxt); i += 2; continue
        if nxt and t.isdigit() and nxt.isdigit():
            out.append(str(int(t)) + "-" + str(int(nxt))); i += 2; continue
        out.append(t); i += 1
    # leading township direction ("DOWNINGTOWN X EAST X EAST" -> E ... E)
    if len(out) >= 3 and out[1] in ABBR and out[0].upper() not in ABBR:
        out = [out[1]] + out[:1] + out[2:]
    words = []
    for idx, w in enumerate(out):
        if idx == 0 and w in ABBR:
            nxt = out[1] if len(out) > 1 else ""
            words.append(w if (nxt.upper() in ABBRV_TOWNS or nxt == "") else FULL[w])
            continue
        if w in ABBR:
            words.append(w)
        elif re.fullmatch(r"\S*-\S*", w):
            words.append(w)
        else:
            words.append(w.capitalize())
    return " ".join(words)


DEFAULT_SOURCE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "work2022p", "chester", "chester_prcres_slice.txt",
)

# ---------------------------------------------------------------------------
# County-level totals from the certified county reports (main + recount PDFs;
# embedded for --verify).  Contest totals include the county's write-ins,
# which the DOS bulk file does not carry per precinct; each entry carries the
# county report's own write-in total so --verify can bridge the gap.
# ---------------------------------------------------------------------------

CONTEST_TOTALS = {
    # tag: (named-candidate county totals, county report contest total,
    #       county report write-in total).  GOP U.S. Senate figures come from
    #       the county RECOUNT report; everything else from the main report.
    "GOV-DEM": ({"Josh Shapiro": 56991}, 57350, 359),
    "GOV-REP": ({"Lou Barletta": 7323, "Douglas V. Mastriano": 19176, "Nche Zama": 631,
                 "Dave White": 5495, "Melissa Hart": 1233, "Bill McSwain": 22079,
                 "Charlie Gerow": 483, "Joe Gale": 2222, "Jake Corman": 495}, 59425, 288),
    "LTG-DEM": ({"Austin Davis": 33968, "Ray Sosa": 7883, "Brian Sims": 15369}, 57367, 147),
    "LTG-REP": ({"Clarice Schillinger": 12415, "James Earl Jones": 7276, "Rick Saccone": 9979,
                 "John Brown": 1541, "Chris Frye": 2493, "Jeff Coleman": 4251,
                 "Russ Diamond": 2682, "Carrie Lewis DelRosso": 7982, "Teddy Daniels": 5095},
                54068, 354),
    "USS-DEM": ({"Alex Khalil": 2840, "Conor Lamb": 17170, "Malcolm Kenyatta": 6312,
                 "John Fetterman": 31930}, 58386, 134),
    "USS-REP": ({"Kathy Barnette": 15666, "Mehmet Oz": 16751, "George Bochetto": 868,
                 "Jeff Bartos": 2987, "Dave McCormick": 18793, "Sean Gale": 2045,
                 "Carla Sands": 1671}, 59106, 325),  # recount-report total, post-recount
    "USC-DEM-6": ({"Chrissy Houlahan": 58017}, 58257, 240),
    "USC-REP-6": ({"Guy Ciarrocchi": 20241, "Ron Vogel": 11203, "Steve Fanelli": 17862,
                   "Regina Mauro": 7488}, 57052, 258),
    # Congress 5 split precincts report zero votes for these candidates in the
    # certified DOS file (and in the ENR anchor); the county report has no CD5
    # contest section.  Kept as zero-vote rows, not invented.
    "USC-DEM-5": ({}, 0, 0),
    "USC-REP-5": ({}, 0, 0),
    "STS-DEM-44": ({"Katie Muth": 20967}, 21051, 84),
    "STS-REP-44": ({"Jessica Florio": 19766}, 19966, 200),
    "STH-DEM-13": ({"Chamir James": 4510}, 4551, 41),
    "STH-REP-13": ({"Carmela Ciliberti": 2871, "John Lawrence": 4891}, 7779, 17),
    "STH-DEM-26": ({"Paul Friel": 7003}, 7033, 30),
    "STH-REP-26": ({"Tim Hennessey": 6732}, 6821, 89),
    "STH-DEM-74": ({"Dan Williams": 4989}, 5024, 35),
    "STH-REP-74": ({"Dale Hensel": 5003}, 5054, 51),
    "STH-DEM-155": ({"Danielle Friel Otten": 7002}, 7037, 35),
    "STH-REP-155": ({"Kyle Scribner": 6514}, 6569, 55),
    "STH-DEM-156": ({"Chris Pielli": 7391}, 7419, 28),
    "STH-REP-156": ({"Heidi VanderWaal": 6215}, 6283, 68),
    "STH-DEM-157": ({"Melissa Shusterman": 8689}, 8713, 24),
    "STH-REP-157": ({"Sarah Marvin": 6455}, 6528, 73),
    "STH-DEM-158": ({"Christina Sappey": 6807}, 6836, 29),
    "STH-REP-158": ({"Leon Spencer": 5877}, 5938, 61),
    "STH-DEM-160": ({"Cathy Spahr": 2645}, 2653, 8),
    "STH-REP-160": ({"Craig Williams": 2781}, 2813, 32),
    "STH-DEM-167": ({"Kristine C. Howard": 7433}, 7473, 40),
    "STH-REP-167": ({"Gail Newman": 4211, "Melissa Dicranian": 2052}, 6302, 39),
}

# ---------------------------------------------------------------------------


def county_rows(path: str) -> list[dict]:
    out = []
    with open(path, newline="", encoding="latin-1") as fh:
        for row in csv.reader(fh):
            if len(row) < 26 or row[:2] != ["2022", ELECTION_TYPE]:
                continue
            if row[2] != COUNTY_CODE:
                continue
            if row[8] not in OFFICES:
                raise SystemExit("unexpected office code %r in %s" % (row[8], path))
            cand_key = (row[11].strip(), row[12].strip(), row[13].strip(), row[14].strip())
            name = NAME_FIX.get(cand_key)
            if name is None:
                name = " ".join(p for p in (row[12].strip().title(),
                                            row[11].strip().title()) if p)
                if row[14]:
                    name += " " + row[14].title()
            party = row[9] or PARTY_FIX.get(row[10], row[9])
            out.append({
                "precinct_code": row[3],
                "precinct": prettify_precinct(row[22]),
                "office_code": row[8],
                "office": OFFICES[row[8]][0],
                "district": str(int(row[OFFICES[row[8]][1]])) if OFFICES[row[8]][1] else "",
                "party": party,
                "candidate": name,
                "votes": int(row[15]),
                "contest": row[10],
            })
    # The DOS file splits Birmingham 2 between Congress 5/6 and Phoenixville
    # M-1 between State House 155/157; both halves are separate precinct
    # codes for the shared contests (U.S. Senate / Governor / ...).  Sum the
    # split halves' rows per (precinct, office, district, party, candidate)
    # so the named precinct carries one row per contest entry (the district
    # in the key keeps the STH 155 / 157 and Cong 5 / 6 contest rows apart).)
    merged: dict[tuple, dict] = {}
    for r in out:
        key = (r["precinct"], r["office"], r["district"], r["party"], r["candidate"])
        if key in merged:
            merged[key]["votes"] += r["votes"]
        else:
            merged[key] = r
    return list(merged.values())


def to_output_rows(rows: list[dict]) -> list[list]:
    out = sorted(rows, key=lambda r: (int(r["precinct_code"]),
                                      OFFICE_ORDER.index(r["office"]),
                                      int(r["district"]) if r["district"] else -1,
                                      r["party"], r["candidate"]))
    return [[COUNTY_NAME, r["precinct"], r["office"], r["district"], r["party"],
             r["candidate"], str(r["votes"])] for r in out]


def run_checks(rows: list[dict]) -> tuple[int, int, list[str]]:
    """Sum precinct votes per (office, party, district, candidate) and per
    contest, and compare with the county certified reports' printed totals.
    Candidate sums must match exactly; contest sums must match exactly once
    the county report's own write-in total is added back."""
    sums: dict[tuple, int] = {}
    contest_sums: dict[str, int] = {}
    for r in rows:
        key = (r["office_code"], r["party"], r["district"], r["candidate"])
        sums[key] = sums.get(key, 0) + r["votes"]
        tag = r["office_code"] + "-" + r["party"] + \
              ("-" + r["district"] if r["office_code"] in ("USC", "STS", "STH") else "")
        contest_sums[tag] = contest_sums.get(tag, 0) + r["votes"]
    problems = []
    checked = 0
    for tag, total in contest_sums.items():
        checked += 1
        want = CONTEST_TOTALS.get(tag)
        if want is None:
            problems.append("no CONTEST_TOTALS entry for contest %s" % tag)
        else:
            named, report_total, write_ins = want
            if total + write_ins != report_total:
                problems.append("contest %s: DOS sum %d + write-ins %d != county total %d"
                                % (tag, total, write_ins, report_total))
    for key, val in sums.items():
        office_code, party, district, cand = key
        tag = office_code + "-" + party + ("-" + district if office_code in ("USC", "STS", "STH") else "")
        want = CONTEST_TOTALS.get(tag)
        if want and cand in want[0]:
            checked += 1
            if val != want[0][cand]:
                problems.append("%s [%s] %s d%s: precinct sum %d != county PDF %d"
                                % (office_code, party, cand, district, val, want[0][cand]))
    # any embedded named total never seen in the rows is a problem too
    for tag, (cands, _total, _wi) in CONTEST_TOTALS.items():
        for cand in cands:
            if not any(k[3] == cand for k in sums):
                problems.append("county report candidate %r (%s) missing from output"
                                % (cand, tag))
    return checked, len(sums), problems


def main(argv: list[str]) -> int:
    args = [a for a in argv if a != "--verify"]
    src = args[0] if args and os.path.exists(args[0]) else DEFAULT_SOURCE
    if not os.path.exists(src):
        print("No Chester 2022 primary DOS source found (tried %s)" % (args or DEFAULT_SOURCE),
              file=sys.stderr)
        return 1
    rows = county_rows(src)
    if not rows:
        print("No Chester primary rows found in source %s" % src, file=sys.stderr)
        return 1
    out_rows = to_output_rows(rows)

    if "--verify" in argv:
        checked, ncand, problems = run_checks(rows)
        print("verify: %d value checks against the county certified reports" % checked)
        print("verify: %d (office, party, district, candidate) keys; %d precinct rows"
              % (ncand, len(out_rows)))
        if problems:
            for p in problems:
                print("  MISMATCH " + p)
            return 1
        print("verify: all named-candidate sums match the county reports exactly")
        print("verify: all contest sums match report totals once each report's "
              "own write-in total is added back")

    if len(args) > 1:
        with open(args[1], "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(HEADER)
            w.writerows(out_rows)
        print("wrote %s (%d data rows, HEADER=%d cols) from %s"
              % (args[1], len(out_rows), len(HEADER), src))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))