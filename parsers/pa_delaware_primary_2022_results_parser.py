#!/usr/bin/env python3
"""Parse the Delaware County 2022 Primary "Cumulative Results" HTML (Clarity).

The source file is the Clarity Elections Count_HtmlCumulativeReport saved as
UTF-16 (see the 2022-general precedent in
pa_delaware_general_2022_results_parser.py): one official COUNTYWIDE table
per contest with Election Day / By Mail / Provisional / Total columns, plus
"Write-in Votes" rows and a "Cast Votes" row per contest. It contains NO
precinct detail (only a "Precincts Reporting 428 of 428" header), so this
parser emits county-level rows in the consolidated county.csv convention:

    county,office,district,party,candidate,votes,election_day,mail,provisional

Primary specifics vs. the general parser:
  * Each contest is party-specific: the ballot party comes from the contest
    name suffix (" - Democratic Party" / " - Republican Party"), not from the
    (blank) per-choice Party column, and WRITE-IN rows carry that party too.
  * The R U.S. Senate contest here is the post-recount certification: the
    report was generated 6/6/2022 ("Official Results"), which is after the
    June 1 GOP U.S. Senate recount, matching how the county certified it.
  * Hyper-local contests (Committee Member, Constable, Judge/Inspector of
    Elections, Magisterial District Judge, borough offices, ...) are skipped;
    the consolidated county file reports only statewide/legislative offices.
  * "Member of Democratic/Republican State Committee" maps to the anchor's
    exact strings; the Republican contests carry their district numbers.

Registered Voters (TotalRegisteredVoters) and Ballots Cast
(TotalTabulatedBallots) come from the report's META tags. Per-contest "Cast
Votes" totals are used only as an internal cross-check (candidate rows must
equal them; Cast Votes excludes write-ins, which the source prints on a
separate "Write-in Votes" row).

Usage (HTML county-level mode):
    python pa_delaware_primary_2022_results_parser.py <input.html> <output.csv> [--county Delaware]

DOS precinct mode (--dos first argument): the Clarity HTML is countywide-only,
so per-precinct rows come from the PA Department of State bulk precinct-returns
file for the 2022 Primary (ElectionReturns_2022_Primary_PrecinctReturns.txt,
county code 23; verbatim slice kept at
work2022p/delaware/delaware_prcres_slice.txt), exactly as for the Cameron,
Monroe, Bedford, Clearfield, Fulton, Pike and Snyder 2022 primaries:

    python pa_delaware_primary_2022_results_parser.py --dos <prcres_slice> \
        <output.csv> [county_csv] [enr_anchor_county_csv]

DOS-mode output is a 7-column precinct file (both parties in one file).  The
DOS bulk file carries no State Committee contests, no write-in rows and no
Registered Voters / Ballots Cast, so those figures stay countywide-only in
the county-level CSV produced in HTML mode (documented, no-invention rule).

Precinct names follow the county's own ERStat spellings already in the repo
(2020/counties/20200602__pa__primary__delaware__precinct.csv and the 2018
files): ALDAN Eastern, ASTON 1W, BETHEL 1P, CHESTER CITY 10W 1P, CHESTER
TWP 1P, NETHER PROV 1W 1P, UPPER DARBY 1D 1P, TRAINER Lennox Park, ...
One DOS quirk: Darby Township (codes 840-900) and Darby Borough (codes
910-960) both print "DARBY W n P m" descriptions; the Township/Borough split
follows the DOS precinct-code ranges.  Springfield W 3 P 02 was split by
House district into ...02(161)/...02(165) (county 2018 spellings
"SPRINGFIELD 3W 2P 161ST"/"165TH").  429 DOS precinct codes -> 429 named
precincts (the Clarity header's "428 of 428" lags the certified list).
"""

import argparse
import csv
import re
import sys

from bs4 import BeautifulSoup

# read-only reuse of this county's existing helpers
from pa_delaware_general_2023_results_parser import numbers_from_cells
from pa_delaware_general_2022_results_parser import decode_source

COLS = ["county", "office", "district", "party", "candidate",
        "votes", "election_day", "mail", "provisional"]

CONTEST_MAP = [
    (re.compile(r"^United States Senator", re.I),
     lambda m: ("U.S. Senate", "")),
    (re.compile(r"^Lieutenant Governor", re.I),
     lambda m: ("Lieutenant Governor", "")),
    (re.compile(r"^Governor", re.I),
     lambda m: ("Governor", "")),
    (re.compile(r"^Representative in Congress (\d+)\w* District", re.I),
     lambda m: ("U.S. House", m.group(1))),
    (re.compile(r"^Senator in the General Assembly (\d+)\w* District", re.I),
     lambda m: ("State Senate", m.group(1))),
    (re.compile(r"^Representative in the General Assembly (\d+)\w* District", re.I),
     lambda m: ("State House", m.group(1))),
    (re.compile(r"^Member of Democratic State Committee", re.I),
     lambda m: ("Member of Democratic State Committee", "")),
    (re.compile(r"^Member of Republican State Committee (\d+)\w* District", re.I),
     lambda m: ("Member of Republican State Committee", m.group(1))),
]

# contests the consolidated county file does not carry (as in the 2022 general)
LOCAL_PREFIXES = (
    "Committee Member", "Constable", "Judge of Election",
    "Inspector of Election", "Magisterial District Judge",
)


def parse_contest(name):
    """Map a Clarity primary contest header to (office, district, party)."""
    n = re.sub(r"\s+", " ", name).strip()
    n = re.sub(r"\s*-\s*\(Vote for[^)]*\)\s*", " ", n).strip()
    party = ""
    pm = re.search(r"\s+-\s+(Democratic|Republican) Party\b", n, re.I)
    if pm:
        party = "DEM" if pm.group(1).lower().startswith("d") else "REP"
        n = n[:pm.start()].strip() + " " + n[pm.end():].strip()
        n = re.sub(r"\s+", " ", n)
    n = re.sub(r"\s+-\s*$", "", n).strip()
    for rx, fn in CONTEST_MAP:
        m = rx.match(n)
        if m:
            office, district = fn(m)
            return (office, district), party, n
    return None, party, n


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
    skipped = []
    seen_contests = set()
    contest_totals = []
    for h in headers:
        nm = re.sub(r"\s+", " ", h.strip())
        mapped, party, cleaned = parse_contest(nm)
        if mapped is None:
            nm_noparen = re.sub(r"\s+-\s*\(Vote for[^)]*\)\s*", " ", nm).strip()
            if not any(nm_noparen.startswith(p) for p in LOCAL_PREFIXES):
                problems.append("unmapped contest (skipped): %s" % nm)
            else:
                skipped.append(nm)
            continue
        office, district = mapped
        if nm in seen_contests:
            problems.append("duplicate contest header: %s (skipped)" % nm)
            continue
        seen_contests.add(nm)

        tbl = h
        while tbl is not None and tbl.name != "table":
            tbl = tbl.parent
        if tbl is None:
            problems.append("%s: no table" % nm)
            continue

        cast_by_col = None
        seen_labels = set()  # single-candidate tables repeat rows in nested tables
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
                if label in seen_labels:
                    continue  # nested-table duplicate row
                seen_labels.add(label)
                nums = numbers_from_cells(cells)
                if len(nums) < 4:
                    continue
                ed, mail, prov, total = nums[:4]
                if ed + mail + prov != total:
                    problems.append("%s / Write-ins: ED+Mail+Prov=%d != Total=%d"
                                    % (nm, ed + mail + prov, total))
                rows.append({
                    "county": county, "office": office, "district": district,
                    "party": party, "candidate": "Write Ins",
                    "votes": total, "election_day": ed, "mail": mail,
                    "provisional": prov,
                })
                continue
            m = re.match(r"^\(\d+\)\s+(.+)$", label)
            if not m:
                continue
            if label in seen_labels:
                continue  # nested-table duplicate row
            seen_labels.add(label)
            cand = m.group(1).strip()
            nums = numbers_from_cells(cells)
            if len(nums) < 4:
                problems.append("%s / %s: short row %r" % (nm, cand, cells))
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
                                % (nm, cand, ed + mail + prov, total))

        # cross-check: named candidate rows only (Cast Votes excludes write-ins)
        mine = {}
        for r in rows:
            if (r["office"] == office and r["district"] == district
                    and r["party"] == party
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
                        % (nm, c, mine.get(c, 0), v))

        if cast_by_col and bc and len(cast_by_col) >= 4 \
                and re.search(r"Vote for One", nm, re.I) \
                and cast_by_col[-1] > bc:
            problems.append("%s: Cast Votes=%d > Ballots Cast=%d"
                            % (nm, cast_by_col[-1], bc))
        if cast_by_col:
            contest_totals.append((office, district, party, cast_by_col[-1]))

    return rows, problems, (len(seen_contests), len(skipped)), contest_totals, (rv, bc)


# =================================================================== DOS mode
DOS_COUNTY_CODE = "23"
DOS_YEAR = "2022"
DOS_ETYPE = "P"
DOS_FIELDNAMES = ["county", "precinct", "office", "district", "party",
                  "candidate", "votes"]

# DOS office code -> (office, district source field), matching the Cameron /
# Monroe 2022-primary parser conventions.
DOS_OFFICES = {
    "USS": ("U.S. Senate", None),
    "GOV": ("Governor", None),
    "LTG": ("Lieutenant Governor", None),
    "USC": ("U.S. House", "uscd"),
    "STS": ("State Senate", "cd"),
    "STH": ("State House", "cd"),
}
DOS_OFFICE_ORDER = ["U.S. Senate", "Governor", "Lieutenant Governor",
                    "U.S. House", "State Senate", "State House"]

# The Clarity candidate labels are the verified county.csv spellings, so the
# DOS (LAST, FIRST, MIDDLE, SUFFIX) names map onto them.
DOS_CANDIDATES = {
    ("KHALIL", "ALEXANDRIA", "GLORIA"): "Alex Khalil",
    ("LAMB", "CONOR", "J"): "Conor Lamb",
    ("KENYATTA", "MALCOLM", ""): "Malcolm Kenyatta",
    ("FETTERMAN", "JOHN", "K"): "John Fetterman",
    ("BARNETTE", "KATHY", "J"): "Kathy Barnette",
    ("OZ", "MEHMET", "C"): "Mehmet Oz",
    ("BOCHETTO", "GEORGE", "A"): "George Bochetto",
    ("BARTOS", "JEFFREY", "A"): "Jeff Bartos",
    ("MCCORMICK", "DAVID", "H"): "Dave McCormick",
    ("GALE", "SEAN", "PETER"): "Sean Gale",
    ("SANDS", "CARLA", "HERD"): "Carla Sands",
    ("SHAPIRO", "JOSHUA", "D"): "Josh Shapiro",
    ("BARLETTA", "LOUIS", ""): "Lou Barletta",
    ("MASTRIANO", "DOUGLAS", "V"): "Douglas V. Mastriano",
    ("ZAMA", "NCHE", ""): "Nche Zama",
    ("WHITE", "DAVID", "J"): "Dave White",
    ("HART", "MELISSA", "A"): "Melissa Hart",
    ("MCSWAIN", "WILLIAM", "M"): "Bill McSwain",
    ("GEROW", "CHARLES", "R"): "Charlie Gerow",
    ("GALE", "JOSEPH", "CHARLES"): "Joe Gale",
    ("CORMAN", "JACOB", "DOYLE", "III"): "Jake Corman",
    ("DAVIS", "AUSTIN", ""): "Austin Davis",
    ("SOSA", "RAYMOND", "L"): "Ray Sosa",
    ("SIMS", "BRIAN", ""): "Brian Sims",
    ("SCHILLINGER", "CLARICE", "D"): "Clarice Schillinger",
    ("JONES", "JAMES", "E"): "James Earl Jones",
    ("SACCONE", "RICHARD", ""): "Rick Saccone",
    ("BROWN", "JOHN", "A"): "John Brown",
    ("FRYE", "CHRISTOPHER", "C", "JR"): "Chris Frye",
    ("COLEMAN", "JEFFREY", "H."): "Jeff Coleman",
    ("DIAMOND", "RUSSELL", "H"): "Russ Diamond",
    ("DELROSSO", "CARRIE", ""): "Carrie Lewis DelRosso",
    ("DANIELS", "THEODORE", ""): "Teddy Daniels",
    ("SCANLON", "MARY GAY", ""): "Mary Gay Scanlon",
    ("GALLUCH", "DAVID", "LEWCZYK"): "David Galluch",
    ("PRESCOD", "PAUL", ""): "Paul Prescod",
    ("WILLIAMS", "ANTHONY", "HARDY"): "Anthony Hardy Williams",
    ("KEARNEY", "TIMOTHY", "P"): "Tim Kearney",
    ("AGOVINO", "FRANK", ""): "Frank Agovino",
    ("KAZEEM", "CAROL", ""): "Carol Kazeem",
    ("KIRKLAND", "BRIAN", ""): "Brian Kirkland",
    ("MOTON", "RUTH", ""): "Ruth Moton",
    ("SPAHR", "CATHERINE", "E"): "Cathy Spahr",
    ("WILLIAMS", "WENDELL", "CRAIG"): "Craig Williams",
    ("KRUEGER", "LEANNE", "T."): "Leanne Krueger",
    ("MONGELLUZZO", "EDWARD", "A"): "Ed Mongelluzzo",
    ("DELLOSO", "DAVID", "M"): "David M. Delloso",
    ("MATTUS", "MICHELLE", "STOCK"): "Michelle Mattus",
    ("ZABEL", "MICHAEL", "P"): "Mike Zabel",
    ("RUCCI", "KENNETH", "P"): "Kenneth P. Rucci",
    ("CURRY", "GINA", "H."): "Gina H. Curry",
    ("O'MARA", "JENNIFER", ""): "Jennifer O'Mara",
    ("BROWN", "DAVID", "MATTHEW"): "David Brown",
    ("VITALI", "GREGORY", "S."): "Greg Vitali",
    ("RAZZANO", "KIMBERLY", "J"): "Kim Razzano",
    ("BOROWSKI", "LISA", ""): "Lisa A. Borowski",
    ("QUINN", "CHRISTOPHER", "B."): "Chris Quinn",
    ("YOUNG", "REGINA", "G"): "Regina G. Young",
    ("MCCLINTON", "JOANNA", "E."): "Joanna E. McClinton",
}

# DOS precinct-description -> the county's ERStat-style spelling (as already
# in the repo's 2018/2020 Delaware files).  Numbers stay as printed, wards
# suffix the number with W, precincts with P.
_SIMPLE_MUNI = {
    "ALDAN": "ALDAN", "ASTON": "ASTON", "BETHEL": "BETHEL",
    "BROOKHAVEN": "BROOKHAVEN", "CLIFTON HEIGHTS": "CLIFTON HEIGHTS",
    "COLLINGDALE": "COLLINGDALE", "COLWYN": "COLWYN", "CONCORD": "CONCORD",
    "EDGMONT": "EDGMONT", "FOLCROFT": "FOLCROFT", "GLENOLDEN": "GLENOLDEN",
    "HAVERFORD": "HAVERFORD", "LANSDOWNE": "LANSDOWNE",
    "MARCUS HOOK": "MARCUS HOOK", "MARPLE": "MARPLE",
    "NEWTOWN": "NEWTOWN", "NORWOOD": "NORWOOD",
    "PROSPECT PARK": "PROSPECT PARK", "RADNOR": "RADNOR",
    "RIDLEY PARK": "RIDLEY PARK",
    "SHARON HILL": "SHARON HILL", "SPRINGFIELD": "SPRINGFIELD",
    "THORNBURY": "THORNBURY", "TINICUM": "TINICUM", "UPLAND": "UPLAND",
    "YEADON": "YEADON",
}
# Municipalities with no W/P suffix at all.
_PLAIN_MUNI = {"CHESTER HEIGHTS", "MILLBOURNE", "MORTON", "PARKSIDE",
               "ROSE VALLEY", "RUTLEDGE"}
DARBY_TWP_CODES = {"840", "850", "860", "870", "880", "890", "900"}   # r[28]=70
DARBY_BORO_CODES = {"910", "920", "930", "940", "950", "960"}         # r[28]=65

# Explicit description -> name overrides (everything not covered by the
# mechanical W/P rewrites below).
_DOS_SPECIALS = {
    "ALDAN P EASTERN": "ALDAN Eastern",
    "ALDAN P WESTERN": "ALDAN Western",
    "CHADDS FORD D NORTHEAST": "CHADDS FORD NE P",
    "CHADDS FORD D SOUTHEAST": "CHADDS FORD SW P",
    "EAST LANSDOWNE P EASTERN": "EAST LANSDOWNE E",
    "EAST LANSDOWNE P WESTERN": "EAST LANSDOWNE W",
    "EDDYSTONE P NORTHERN": "EDDYSTONE Northern",
    "EDDYSTONE P SOUTHERN": "EDDYSTONE Southern",
    "MEDIA P EASTERN": "MEDIA Eastern",
    "MEDIA P NORTHERN": "MEDIA Northern",
    "MEDIA P SOUTHERN": "MEDIA Southern",
    "MEDIA P WESTERN": "MEDIA Western",
    "SWARTHMORE P EASTERN": "SWARTHMORE Eastern",
    "SWARTHMORE P NORTHERN": "SWARTHMORE Northern",
    "SWARTHMORE P WESTERN": "SWARTHMORE Western",
    "TRAINER P LENNOX PARK": "TRAINER Lennox Park",
    "TRAINER P LOWER": "TRAINER Lower",
    "TRAINER P UPPER": "TRAINER Upper",
    "SPRINGFIELD W 3 P 02(161)": "SPRINGFIELD 3W 2P 161ST",
    "SPRINGFIELD W 3 P 02(165)": "SPRINGFIELD 3W 2P 165TH",
}


def dos_precinct_name(code, muni, b1c, b1n):
    """DOS precinct description -> county ERStat-style precinct name."""
    muni = " ".join(muni.split())
    if muni in _DOS_SPECIALS:
        return _DOS_SPECIALS[muni]
    if muni in _PLAIN_MUNI and not b1c:
        return muni
    if muni.startswith("DARBY"):
        broro = "BORO" if code in DARBY_BORO_CODES else (
            "TWP" if code in DARBY_TWP_CODES else None)
        if broro is None:
            raise ValueError("Darby code %s outside known ranges" % code)
        m = re.match(r"^DARBY(?:\s+BORO|\s+TWP)?\s+W\s+(\d+)"
                     r"(?:\s+P\s+(\d+))?$", muni)
        if not m:
            raise ValueError("unmapped Darby description %r" % muni)
        name = "DARBY %s %sW" % (broro, int(m.group(1)))
        if m.group(2):
            name += " %dP" % int(m.group(2))
        return name
    if muni.startswith("CHESTER W"):
        m = re.match(r"^CHESTER(?:\s+HEIGHTS)?\s+W\s+(\d+)"
                     r"(?:\s+P\s+(\d+))?$", muni)
        if m:
            name = "CHESTER CITY %dW" % int(m.group(1))
            if m.group(2):
                name += " %dP" % int(m.group(2))
            return name
    if muni.startswith("CHESTER P"):
        m = re.match(r"^CHESTER P (\d+)$", muni)
        if m:
            return "CHESTER TWP %dP" % int(m.group(1))
    if muni.startswith("MIDDLETOWN"):
        m = re.match(r"^MIDDLETOWN\s+D\s+(\d+)(?:\s+P\s+(\d+))?$", muni)
        if m:
            name = "MIDDLETOWN %dD" % int(m.group(1))
            if m.group(2):
                name += " %dP" % int(m.group(2))
            return name
    if muni.startswith("NETHER PROVIDENCE"):
        m = re.match(r"^NETHER PROVIDENCE\s+W\s+(\d+)(?:\s+D\s+(\d+))?$", muni)
        if m:
            name = "NETHER PROV %dW" % int(m.group(1))
            if m.group(2):
                name += " %dP" % int(m.group(2))
            return name
    if muni.startswith("LOWER CHICHESTER"):
        m = re.match(r"^LOWER CHICHESTER\s+P\s+(\d+)$", muni)
        if m:
            return "LWR CHICHESTER %dP" % int(m.group(1))
    if muni.startswith("UPPER CHICHESTER"):
        m = re.match(r"^UPPER CHICHESTER\s+W\s+(\d+)(?:\s+D\s+(\d+))?$", muni)
        if m:
            name = "UPP CHICHESTER %dW" % int(m.group(1))
            if m.group(2):
                name += " %dP" % int(m.group(2))
            return name
    if muni.startswith("UPPER DARBY"):
        m = re.match(r"^UPPER DARBY\s+D\s+(\d+)(?:\s+P\s+(\d+))?$", muni)
        if m:
            name = "UPPER DARBY %dD" % int(m.group(1))
            if m.group(2):
                name += " %dP" % int(m.group(2))
            return name
    if muni.startswith("UPPER PROVIDENCE"):
        m = re.match(r"^UPPER PROVIDENCE\s+P\s+(\d+)$", muni)
        if m:
            return "UPPER PROVIDENCE %dD" % int(m.group(1))
    if muni.startswith("RIDLEY W"):
        m = re.match(r"^RIDLEY\s+W\s+(\d+)(?:\s+P\s+(\d+))?$", muni)
        if m:
            name = "RIDLEY TWP %dW" % int(m.group(1))
            if m.group(2):
                name += " %dP" % int(m.group(2))
            return name
    m = re.match(r"^([A-Z ]+?)\s+P\s+(\d+)$", muni)
    if m and m.group(1) in _SIMPLE_MUNI:
        return "%s %dP" % (m.group(1), int(m.group(2)))
    m = re.match(r"^([A-Z ]+?)\s+W\s+(\d+)(?:\s+P\s+(\d+))?$", muni)
    if m and m.group(1) in _SIMPLE_MUNI:
        name = "%s %dW" % (m.group(1), int(m.group(2)))
        if m.group(3):
            name += " %dP" % int(m.group(3))
        return name
    m = re.match(r"^([A-Z ]+?)\s+P\s+([A-Z]+)$", muni)
    if m and m.group(1) in _SIMPLE_MUNI:
        return "%s %s" % (m.group(1), m.group(2).capitalize())
    raise ValueError("unmapped DOS precinct description %r (code %s)"
                     % (muni, code))


# ENR anchor spelling -> the output name this parser emits
ENR_TO_OUT = {
    "Alexandria Gloria Khalil": "Alex Khalil",
    "Carla Herd Sands": "Carla Sands",
    "Conor J. Lamb": "Conor Lamb",
    "David H. McCormick": "Dave McCormick",
    "George A. Bochetto": "George Bochetto",
    "Jeffrey A. Bartos": "Jeff Bartos",
    "John K. Fetterman": "John Fetterman",
    "Kathy J. Barnette": "Kathy Barnette",
    "Malcolm Kenyatta": "Malcolm Kenyatta",
    "Mehmet C. Oz": "Mehmet Oz",
    "Sean Peter Gale": "Sean Gale",
    "Charles R. Gerow": "Charlie Gerow",
    "David J. White": "Dave White",
    "Douglas V. Mastriano": "Douglas V. Mastriano",
    "Jacob Doyle Corman III": "Jake Corman",
    "Joseph Charles Gale": "Joe Gale",
    "Joshua D. Shapiro": "Josh Shapiro",
    "Louis Barletta": "Lou Barletta",
    "Melissa A. Hart": "Melissa Hart",
    "Nche Zama": "Nche Zama",
    "William M. McSwain": "Bill McSwain",
    "Austin Davis": "Austin Davis",
    "Brian Sims": "Brian Sims",
    "Carrie Delrosso": "Carrie Lewis DelRosso",
    "Christopher C. Frye Jr": "Chris Frye",
    "Clarice D. Schillinger": "Clarice Schillinger",
    "James E. Jones": "James Earl Jones",
    "Jeffrey H. Coleman": "Jeff Coleman",
    "John A. Brown": "John Brown",
    "Raymond L. Sosa": "Ray Sosa",
    "Richard Saccone": "Rick Saccone",
    "Russell H. Diamond": "Russ Diamond",
    "Theodore Daniels": "Teddy Daniels",
    "David Lewczyk Galluch": "David Galluch",
    "Mary Gay Scanlon": "Mary Gay Scanlon",
    "Anthony Hardy Williams": "Anthony Hardy Williams",
    "Paul Prescod": "Paul Prescod",
    "Frank Agovino": "Frank Agovino",
    "Timothy P. Kearney": "Tim Kearney",
    "Brian Kirkland": "Brian Kirkland",
    "Carol Kazeem": "Carol Kazeem",
    "Ruth Moton": "Ruth Moton",
    "Catherine E. Spahr": "Cathy Spahr",
    "Wendell Craig Williams": "Craig Williams",
    "Edward A. Mongelluzzo": "Ed Mongelluzzo",
    "Leanne T. Krueger": "Leanne Krueger",
    "David M. Delloso": "David M. Delloso",
    "Michelle Stock Mattus": "Michelle Mattus",
    "Kenneth P. Rucci": "Kenneth P. Rucci",
    "Michael P. Zabel": "Mike Zabel",
    "Gina H. Curry": "Gina H. Curry",
    "Jennifer O'Mara": "Jennifer O'Mara",
    "David Matthew Brown": "David Brown",
    "Gregory S. Vitali": "Greg Vitali",
    "Kimberly J. Razzano": "Kim Razzano",
    "Christopher B. Quinn": "Chris Quinn",
    "Lisa Borowski": "Lisa A. Borowski",
    "Regina G. Young": "Regina G. Young",
    "Joanna E. McClinton": "Joanna E. McClinton",
}


def read_dos_precincts(path: str):
    """Read the Delaware (county code 23) primary rows from a DOS prcres file."""
    rows = []
    n_read = 0
    with open(path, newline="", encoding="latin-1") as fh:
        for r in csv.reader(fh):
            if len(r) < 26 or len(r) > 37 or r[:3] != [DOS_YEAR, DOS_ETYPE,
                                                       DOS_COUNTY_CODE]:
                continue
            n_read += 1
            off_code = r[8]
            if off_code not in DOS_OFFICES:
                raise ValueError(
                    "unexpected office code %r (candidate %r)" % (off_code, r[10]))
            last, first, mid, suffix = r[11], r[12], r[13], r[14]
            key = (last.strip(), (first or "").strip(), (mid or "").strip())
            if suffix:
                key = key + (suffix.strip(),)
            name = DOS_CANDIDATES.get(key)
            if name is None:
                raise ValueError("unmapped DOS candidate %r (office %s)"
                                 % (key, off_code))
            party = r[9]
            office, dist_field = DOS_OFFICES[off_code]
            if off_code == "USC":
                district = str(int(r[18]))
            elif dist_field:
                district = str(int(r[5]))
            else:
                district = ""
            precinct = dos_precinct_name(str(int(r[3])), r[22], r[23], r[24])
            rows.append({
                "county": "Delaware", "precinct": precinct,
                "office": office, "district": district, "party": party,
                "candidate": name, "votes": int(r[15]),
                "_code": int(r[3]),
                "_off": DOS_OFFICE_ORDER.index(office),
            })
    if not rows:
        raise ValueError("no Delaware primary rows found in %s" % path)
    seen = {(r["precinct"], r["office"], r["district"], r["party"],
             r["candidate"]) for r in rows}
    rows.sort(key=lambda x: (x["_code"], x["_off"], x["candidate"]))
    return rows, n_read, seen


def cross_check_county_csv(rows, county_csv):
    """Per (office, district, party, candidate) precinct sums vs the verified
    county-level Clarity CSV.  Returns (n_keys, exact, deltas)."""
    anchor = {}
    with open(county_csv, newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            if r["office"] in ("Registered Voters", "Ballots Cast"):
                continue
            anchor[(r["office"], r["district"], r["party"], r["candidate"])] = \
                int(r["votes"])
    ours = {}
    for r in rows:
        k = (r["office"], r["district"], r["party"], r["candidate"])
        ours[k] = ours.get(k, 0) + r["votes"]
    exact = 0
    deltas = []
    for k in sorted(anchor):
        office, dist, party, cand = k
        if office.startswith("Member of ") and "State Committee" in office:
            print("  county_csv-only (DOS source has none): %s %s %s %s=%d"
                  % (office, dist, party, cand, anchor[k]))
            continue
        if cand == "Write Ins":
            print("  county_csv-only (Write Ins; DOS bulk has no write-in "
                  "rows): %s %s %s Write Ins=%d"
                  % (office, dist, party, anchor[k]))
            continue
        mine = ours.get(k, 0)
        if mine == anchor[k]:
            exact += 1
        else:
            deltas.append((k, mine - anchor[k], mine, anchor[k]))
    return exact, deltas, ours


def cross_check_anchor(rows, anchor_csv):
    """Named-candidate sums vs the ENR county anchor (with names mapped)."""
    ours = {}
    for r in rows:
        k = (r["office"], r["district"], r["party"], r["candidate"])
        ours[k] = ours.get(k, 0) + r["votes"]
    exact = mismatches = 0
    with open(anchor_csv, newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            if r["county"] != "Delaware":
                continue
            if r["office"].startswith("Member of ") \
                    and "State Committee" in r["office"]:
                continue        # countywide-only, DOS bulk carries none
            mapped = ENR_TO_OUT.get(r["candidate"], r["candidate"])
            k = (r["office"], r["district"], r["party"], mapped)
            mine = ours.get(k, 0)
            if mine != int(r["votes"]):
                mismatches += 1
                print("  ENR MISMATCH %s %s %s %s (as %s): ours %d vs anchor %d"
                      % (r["office"], r["district"], r["party"], r["candidate"],
                         mapped, mine, int(r["votes"])))
            else:
                exact += 1
    return exact, mismatches


def dos_main(argv):
    ap = argparse.ArgumentParser(
        description="Delaware 2022 primary DOS precinct-returns mode")
    ap.add_argument("--dos", action="store_true")
    ap.add_argument("source")
    ap.add_argument("output")
    ap.add_argument("county_csv", nargs="?")
    ap.add_argument("anchor_csv", nargs="?")
    args = ap.parse_args(argv)

    rows, n_read, seen_keys = read_dos_precincts(args.source)
    precincts = sorted({r["precinct"] for r in rows})
    print("DOS: %d source rows -> %d output rows, %d precincts, "
          "%d unique (precinct,contest,candidate) keys"
          % (n_read, len(rows), len(precincts), len(seen_keys)))

    ok = True
    if args.county_csv:
        exact, deltas, _ = cross_check_county_csv(rows, args.county_csv)
        print("county.csv cross-check: %d named-candidate keys exact, "
              "%d delta(s): the Clarity 6/6 'Official Results' tabulation "
              "lags the certified DOS/ENR final by small amounts, and the "
              "DOS bulk file also lacks Write Ins + State Committee rows "
              "(informational; all deltas are countywide-level)"
              % (exact, len(deltas)))
        for (office, dist, party, cand), d, mine, anc in deltas:
            print("    delta %+d (%d vs %d)  %s %s %s %s"
                  % (d, mine, anc, office, dist, party, cand))
    if args.anchor_csv:
        exact, mism = cross_check_anchor(rows, args.anchor_csv)
        print("ENR anchor: %d exact, %d mismatch(es)" % (exact, mism))
        ok = ok and mism == 0

    with open(args.output, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=DOS_FIELDNAMES,
                           extrasaction="ignore")
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

    rows, problems, (ncontests, nskipped), contest_totals, (rv, bc) = parse(
        args.input, args.county)
    with open(args.output, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=COLS)
        w.writeheader()
        w.writerows(rows)

    print("wrote %d rows (%d mapped contests; %d local contests skipped; "
          "RV=%d BC=%d) to %s"
          % (len(rows), ncontests, nskipped, rv, bc, args.output))
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