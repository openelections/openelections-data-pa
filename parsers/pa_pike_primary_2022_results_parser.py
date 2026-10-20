#!/usr/bin/env python3
"""Pike County, PA 2022 Primary — precinct-level results parser.

Source situation (documented):

1. The county's own document is the archived HTML sheet
   "Pike PA 2022 General Primary Official Results by Precinct (archived
   HTML).html" (an Excel-to-HTML export of a www2.pikepa.org per-precinct
   sheet; the only 2022-primary source the county published).  It IS a
   full per-precinct table (18 of 18 precincts; one "Name - Party | Total
   | precinct..." row per candidate) and carries a banner note that the
   June statewide recount results for the Republican U.S. Senator "have
   been updated and are reflected in the vote totals reported".

   However its candidate COUNTY totals are still slightly BELOW the
   certified figures in the ENR-API county anchor
   2022/20220517__pa__primary__county.csv for EVERY contest (U.S. Senate
   Oz 2756 vs certified 2764, Fetterman 2567 vs 2591, Lamb 581 vs 589;
   Governor Shapiro 3568 vs 3604; ...), a uniform late-canvass shortfall.
   The sheet therefore predates the certified final despite the recount
   note; the parser prints the full per-candidate delta report.

2. The PA Department of State certified precinct-returns bulk file for
   the 2022 Primary (ElectionReturns_2022_Primary_PrecinctReturns.txt;
   the same official fallback the 2022-general Pike parser
   pa_pike_general_2022_results_parser.py uses; Pike = county code 52;
   764 rows = 18 precincts x 33 statewide + 3 U.S. House + 2 State Senate
   + [5 HD-139 / 3 HD-189] State House candidate rows — complete, no
   precinct x candidate row missing) sums to the ENR anchor EXACTLY, per
   candidate, for all six contests it carries (USS, GOV, LTG, USC, STS,
   STH), including the post-recount certified GOP U.S. Senate figures
   (Oz 2764 / McCormick 1669 - no June-recount amendment file exists for
   Pike; the certified bulk file IS the post-recount record).  All six
   contests are therefore emitted FROM THE DOS BULK FILE.

3. The contest groups the DOS bulk file does NOT carry — Member of
   Democratic / Republican State Committee, countywide "County
   Committeeperson at Large", the per-precinct Democratic Committeeman /
   Committeewoman + Republican County Committee races (the sheet's "Boro
   and Township Results" section) and the per-ballot turnout figures
   (Total / Total Democratic / Total Republican Voter Turnout) — come
   FROM THE HTML SHEET, with per-candidate sums verified against the
   sheet's own printed county "Total Vote" column.

Consequences (documented, not parsing losses):
  * No write-in rows for the six DOS contests (DOS carries no write-in
    counts; the sheet prints none; the anchor excludes write-ins).
  * No Registered Voters rows (the sheet prints turnout, not
    registration) and no election-day / mail / provisional breakdown in
    any source -> 7-column output.
  * State Committee per-precinct numbers exist ONLY in the pre-final
    sheet, so their sums lag the anchor for the three named candidates
    (Hellman 3449 vs 3484, M. Bukaj 4510 vs 4524, M. Tonkin 4338 vs
    4351).  Emitted as printed; the residual is reported.
  * Turnout rows are emitted as "Ballots Cast" - party "" (total) plus
    party DEM / REP ballot rows (Berks 2022-primary convention); their
    sums match the sheet's printed county totals exactly.
  * The at-large contest title ("COUNTY COMMITTEEPERSON AT LARGE - Vote
    for not more than FIVE") prints no party label, but every named
    candidate in it is a Republican ("Chris Brighton - R" / "Tom Foran
    - R"), so its rows - including its three named write-in rows
    (Raymond Tonkin / Dawn Bukaj / Antonio Perito - WI) - carry party
    REP per the write-ins-carry-contest-party rule.
  * Committee-block candidates are assigned to their contest by row
    structure (a block row's entries run left to right across the three
    contests, so index-0 cells are the Committeeman list, non-index-0
    entries fill Committeewoman then Committeeperson) and by the
    "- D / - R" suffix.  A lone "- WI" entry with no other entry to its
    left or right belongs to the Republican Committeeperson list - the
    assignment confirmed for every such row against the printed sheet
    (e.g. Milford Twp's Gaughan/Luhrs/Olsens are write-in finishers in
    the vote-for-FIVE Committeeperson list after Stroyan; Westfall's
    Dorit Smith / Adelle Roban complete the same 5-slot list).  Blocks
    are NOT on the ENR anchor; they are checked internally only, and
    each block's candidate count is checked against its "Vote for" cap
    (warnings only; the data is emitted as printed).
  * Known source anomaly: Lehman Twp # 2 prints 7 named Democratic
    Committeewoman candidates in a "Vote for no more than SIX" contest -
    emitted as printed; the block check reports the warning.

Names follow the HTML sheet minus the " - Party" suffix, except
"Joseph W Adams", emitted per the county's 2022-general convention as
"Joseph W. Adams"; Governor joint tickets reduce to the governor, the
convention used across the 2022 county files.

Milford Boro vs Milford Twp: DOS carries precinct name "MILFORD" for
both codes 90 and 100; disambiguated exactly as in the 2022 general
parser (90 = Milford Boro, 100 = Milford Twp, per the sheet's column
order and 2018/2020 continuity).  Block heading names ("Delaware Twp
# 1", "Matamoras Boro #1", ...) are normalized to the same 18-precinct
list so committee rows join the rest of the file.

Usage:
    uv run python parsers/pa_pike_primary_2022_results_parser.py \\
        <dos_prcres_txt> <pike_html> <out_precinct_csv> [anchor_county_csv]
    Add --verify-only (anywhere) to run all checks against the anchor and
    print, writing nothing.
"""

import csv
import os
import re
import sys

from bs4 import BeautifulSoup

COUNTY_NAME = "Pike"
COUNTY_CODE = "52"
OUT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ANCHOR_DEFAULT = os.path.join(OUT_ROOT, "2022",
                              "20220517__pa__primary__county.csv")

HEADER = ["county", "precinct", "office", "district", "party", "candidate",
          "votes"]

# 18 precincts, sheet column order; same names as the 2022 general parser.
PRECINCTS = ["Blooming Grove", "Delaware 1", "Delaware 2", "Dingman 1",
             "Dingman 2", "Greene", "Lackawaxen 1", "Lackawaxen 2",
             "Lehman 1", "Lehman 2", "Matamoras Boro 1", "Matamoras Boro 2",
             "Milford Boro", "Milford Twp", "Palmyra", "Porter", "Shohola",
             "Westfall"]
PRECINCT_INDEX = {name: i for i, name in enumerate(PRECINCTS)}

# Office listing order inside a precinct.  Per-precinct committee offices
# are "Democratic Committeeman <Precinct>", etc.
OFFICES_BASE = ["U.S. Senate", "Governor", "Lieutenant Governor",
                "U.S. House", "State Senate", "State House",
                "Member of Democratic State Committee",
                "Member of Republican State Committee",
                "County Committeeperson at Large"]
OFFICE_RANK = dict((o, i) for i, o in enumerate(OFFICES_BASE))
OFFICE_RANK["Democratic Committeeman_"] = 10
OFFICE_RANK["Democratic Committeewoman_"] = 11
OFFICE_RANK["Member of Republican County Committee_"] = 12
OFFICE_RANK["Ballots Cast"] = 13

BLOCK_OFFICE = {"MEN": "Democratic Committeeman",
                "WOMAN": "Democratic Committeewoman",
                "PERSON": "Member of Republican County Committee"}
BLOCK_PARTY = {"MEN": "DEM", "WOMAN": "DEM", "PERSON": "REP"}

TURNOUT_PARTY = {"Total Voter Turnout": "",
                 "Total Democratic Turnout": "DEM",
                 "Total Republican Turnout": "REP"}

# sheet name -> emitted name (2022-general repo convention)
CAND_CANON = {"Joseph W Adams": "Joseph W. Adams"}


def office_rank(office):
    if office in OFFICE_RANK:
        return OFFICE_RANK[office]
    if office.startswith("Democratic Committeeman "):
        return OFFICE_RANK["Democratic Committeeman_"]
    if office.startswith("Democratic Committeewoman "):
        return OFFICE_RANK["Democratic Committeewoman_"]
    if office.startswith("Member of Republican County Committee "):
        return OFFICE_RANK["Member of Republican County Committee_"]
    raise KeyError("no ranking for office %r" % office)


# --------------------------------------------------------------------------
# county HTML sheet
# --------------------------------------------------------------------------

def read_table(path):
    """Rows of (text, colspan) cells, whitespace-normalized."""
    with open(path, "rb") as fh:
        data = fh.read()
    try:
        html = data.decode("utf-8")
    except UnicodeDecodeError:
        html = data.decode("latin-1")
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table")
    grid = []
    for tr in table.find_all("tr"):
        cells = [(re.sub(r"\s+", " ", c.get_text(" ", strip=True)),
                  int(c.get("colspan") or 1))
                 for c in tr.find_all(["th", "td"])]
        if cells:
            grid.append(cells)
    return grid


CONTEST_TITLES = [
    ("UNITED STATES SENATOR", ("U.S. Senate", "", "")),
    ("LIEUTENANT GOVERNOR", ("Lieutenant Governor", "", "")),
    ("GOVERNOR", ("Governor", "", "")),
    ("REPRESENTATIVE IN CONGRESS", ("U.S. House", "{n}", "")),
    ("SENATOR IN THE GENERAL ASSEMBLY", ("State Senate", "{n}", "")),
    ("REPRESENTATIVE IN GENERAL ASSEMBLY", ("State House", "{n}", "")),
    ("REPRESENTATIVE IN THE GENERAL ASSEMBLY", ("State House", "{n}", "")),
    ("DEMOCRATIC STATE COMMITTEE",
     ("Member of Democratic State Committee", COUNTY_CODE, "DEM")),
    ("REPUBLICAN STATE COMMITTEE",
     ("Member of Republican State Committee", COUNTY_CODE, "REP")),
    ("COMMITTEEPERSON AT LARGE", ("County Committeeperson at Large", "",
                                  "REP")),
    ("TOTAL VOTER TURNOUT", ("Turnout", "", "")),
]


def split_main_title(title):
    """Contest title -> (office, district, contest-party) or None."""
    t = title.upper()
    for head, spec in CONTEST_TITLES:
        if head in t:
            m = re.search(r"(\d+)(?:ST|ND|RD|TH)", t)
            district = spec[1].format(n=m.group(1)) if m else spec[1]
            return (spec[0], district, spec[2])
    return None


SUFFIX_PARTY = {"WI": None, "D": "DEM", "R": "REP"}


def split_candidate(raw):
    """'Name - D' / 'Name - Male - R' / 'Name - WI' -> (name, party|None).

    party None marks a write-in row; callers supply the contest's own
    party so write-ins carry it (repo rule for primaries).
    """
    name = raw.strip()
    m = re.search(r"\s*-\s*(Male|Female)\s*-\s*R$", name, re.I)
    if m:
        party = "REP"
    else:
        m = re.search(r"\s*-\s*(WI|D|R)$", name)
        if not m:
            raise ValueError("candidate without party suffix: %r" % raw)
        party = SUFFIX_PARTY[m.group(1)]
    name = name[: m.start()].strip()
    name = re.sub(r"\s+(Male|Female)$", "", name, flags=re.I).strip()
    if not name:
        raise ValueError("empty candidate name in %r" % raw)
    return name, party


BLOCK_TITLE = re.compile(
    r"COUNTY\s+COMMITTEE(MEN|WOMAN|PERSON)\b.*?"
    r"VOTE FOR\s+(?:NO MORE THAN |NOT MORE THAN )?\s*"
    r"(ONE|TWO|THREE|FOUR|FIVE|SIX|SEVEN|EIGHT|\d+)", re.I)
WORDS = {"ONE": "1", "TWO": "2", "THREE": "3", "FOUR": "4", "FIVE": "5",
         "SIX": "6", "SEVEN": "7", "EIGHT": "8"}


def parse_block_title(text):
    t = " ".join(text.split())
    m = BLOCK_TITLE.search(t)
    if not m:
        raise ValueError("unparsed committee header: %r" % text)
    kind = m.group(1)
    n = m.group(2).upper()
    return (BLOCK_OFFICE[kind], BLOCK_PARTY[kind], int(WORDS.get(n, n)))


def block_precinct_name(raw):
    """'Delaware Twp # 1' -> 'Delaware 1'; 'Palmyra Twp' -> 'Palmyra';
    'Milford Boro' / 'Milford Twp' unchanged."""
    t = " ".join(raw.split())
    m = re.match(r"^(.*?)\s+(TWP|BORO)\b\.?\s*#?\s*(\d+)?\s*$", t, re.I)
    if m:
        base, num = m.group(1).strip(), m.group(3)
        kind = m.group(2).upper()
        cands = []
        if num:
            suffix = " Boro" if kind == "BORO" else ""
            cands.append("%s%s %d" % (base, suffix, int(num)))
        else:
            if kind == "BORO":
                cands += [base + " Boro", base]
            else:
                cands += [base, t]
        for key in cands:
            if key in PRECINCT_INDEX:
                return key
    if t in PRECINCT_INDEX:
        return t
    raise ValueError("block precinct heading %r is not one of the 18 "
                     "precincts" % raw)


def find_units(texts):
    """(name cell idx, name, votes) triples in a committee-block row.

    Each candidate's votes cell directly follows its name cell, either
    immediately or across empty fillers (the sheet's column separators
    guarantee a numeric cell is never a name's left neighbor).
    """
    units, k = [], 0
    while k < len(texts):
        if texts[k] and not re.fullmatch(r"[\d,]+", texts[k]):
            j = k + 1
            while j < len(texts) and not texts[j]:
                j += 1
            if j < len(texts) and re.fullmatch(r"[\d,]+", texts[j]):
                units.append((k, texts[k], int(texts[j].replace(",", ""))))
                k = j
                continue
        k += 1
    return units


def emit_block(units, groups, precinct, counts, warnings):
    """Assign each unit to its contest group; return (group, name, votes).

    Rules (validated against the printed sheet):
      * a candidate in the row's first cell belongs to the Committeeman
        list (group 0);
      * left-to-right order: non-index-0 entries fill Committeewoman then
        Committeeperson, subject to suffixes ("- D" can only be groups
        1/2, "- R" only group 3);
      * a "- WI" entry with another entry to its right in the same row
        sits in the Committeewoman list; a lone "- WI" entry sits in the
        Committeeperson list, which is where every lone "- WI" row in
        this sheet is printed.
    """
    cands = [[idx, split_candidate(name)[0], split_candidate(name)[1],
              votes] for idx, name, votes in units]
    rows = []
    for seq, (idx, cname, cparty, votes) in enumerate(cands):
        later = any(c[0] != 0 for c in cands[seq + 1:])
        if idx == 0:
            g = 0
        elif cparty == "REP":
            g = 2
        elif cparty == "DEM":
            g = 1
        else:                                      # - WI
            g = 1 if later else 2
        if rows and g < rows[-1][0]:
            warnings.append("%s: %s: contest order forced (%s -> %s)" %
                            (precinct, cname, rows[-1][0] + 1, g + 1))
            g = rows[-1][0]
        if g == 0 and cparty == "REP":
            raise ValueError("%s: R candidate %s in Committeeman list"
                             % (precinct, cname))
        if g == 2 and cparty == "DEM":
            raise ValueError("%s: D candidate %s in Committeeperson list"
                             % (precinct, cname))
        counts[g] = counts.get(g, 0) + 1
        if counts[g] > groups[g][2]:
            warnings.append("%s: %s list now %d candidates (vote-for %d, "
                            "as printed)" %
                            (precinct, groups[g][0], counts[g],
                             groups[g][2]))
        rows.append((g, cname, votes))
    return rows


def parse_html(path):
    """Parse the county sheet.

    Returns (rows, printed, sums, cand_seq, warnings, blocks_info):
      rows        output rows for the sheet-only contest groups (state
                  committee, at-large, committee blocks, turnout);
      printed     (office, district) -> name -> printed county total;
      sums        same -> per-precinct sums as parsed (internal check:
                  each equals the printed total);
      cand_seq    {((office, district), party, name): printed seq};
      warnings    block warnings;
      blocks_info precinct -> [(office, party, vote-for)].
    """
    rows = []
    printed, sums, cand_seq = {}, {}, {}
    warnings = []
    blocks_info = {}
    mode = "main"
    contest = None
    names = []
    block_precinct = None
    block_groups = None
    block_counts = None

    for cells in read_table(path):
        texts = [c[0] for c in cells]
        spans = [c[1] for c in cells]
        if not any(texts):
            continue

        if mode == "main":
            if texts[0].upper().startswith("BORO AND TOWNSHIP"):
                mode, contest = "blocks", None
                continue
            if spans[0] >= 2 and texts[0]:
                title = split_main_title(texts[0])
                if title:
                    contest, names = title, []
                    continue
            if len(texts) > 1 and texts[1] == "Total Vote":
                names = list(texts[3:])
                while names and not names[-1]:
                    names.pop()
                continue
            if len(texts) > 1 and texts[0] and \
                    re.fullmatch(r"[\d,]+", texts[1]):
                total = int(texts[1].replace(",", ""))
                is_turnout = contest[0] == "Turnout"
                key = ("Turnout", texts[0]) if is_turnout else \
                    (contest[0], contest[1])
                if is_turnout:
                    name, party = "", TURNOUT_PARTY[texts[0]]
                else:
                    name, cparty = split_candidate(texts[0])
                    name = CAND_CANON.get(name, name)
                    party = contest[2] or cparty
                printed.setdefault(key, {})[name] = total
                sums.setdefault(key, {}).setdefault(name, 0)
                cand_seq.setdefault((key, party, name), len(cand_seq))
                for j, val in enumerate(texts[3:]):
                    if j >= len(names) or not val:
                        continue
                    precinct = names[j]
                    votes = int(val.replace(",", ""))
                    sums[key][name] += votes
                    if is_turnout:
                        rows.append([COUNTY_NAME, precinct, "Ballots Cast",
                                     "", party, "", str(votes)])
                    elif contest[0] not in DOS_OFFICE.values():
                        rows.append([COUNTY_NAME, precinct, contest[0],
                                     contest[1], party, name, str(votes)])
                continue
            continue

        # ---- block mode ("Boro and Township Results") ----
        if len(cells) == 1 and texts[0]:
            block_precinct = block_precinct_name(texts[0])
            block_groups, block_counts = None, None
            continue
        if texts[0].upper().startswith("COUNTY"):
            block_groups = [
                parse_block_title(" ".join(t.split()))
                for t, span in zip(texts, spans)
                if span >= 3 and t.upper().startswith("COUNTY")]
            if len(block_groups) != 3:
                raise ValueError("%s: %d committee groups (want 3)"
                                 % (block_precinct, len(block_groups)))
            block_counts = {}
            blocks_info[block_precinct] = block_groups
            continue
        for g, cname, votes in emit_block(find_units(texts), block_groups,
                                          block_precinct, block_counts,
                                          warnings):
            group_office, group_party, _ = block_groups[g]
            rows.append([COUNTY_NAME, block_precinct,
                         "%s %s" % (group_office, block_precinct),
                         "", group_party, cname, str(votes)])

    return rows, printed, sums, cand_seq, warnings, blocks_info


# --------------------------------------------------------------------------
# DOS certified precinct returns (bulk file)
# --------------------------------------------------------------------------

DOS_OFFICE = {"USS": "U.S. Senate", "GOV": "Governor",
              "LTG": "Lieutenant Governor", "USC": "U.S. House",
              "STS": "State Senate", "STH": "State House"}

# DOS (Last, First, Middle) -> printed county name.
NAME_FIX = {
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
    ("CORMAN", "JACOB", "DOYLE"): "Jake Corman",
    ("DAVIS", "AUSTIN", ""): "Austin Davis",
    ("SOSA", "RAYMOND", "L"): "Ray Sosa",
    ("SIMS", "BRIAN", ""): "Brian Sims",
    ("SCHILLINGER", "CLARICE", "D"): "Clarice Schillinger",
    ("JONES", "JAMES", "E"): "James Earl Jones",
    ("SACCONE", "RICHARD", ""): "Rick Saccone",
    ("BROWN", "JOHN", "A"): "John Brown",
    ("FRYE", "CHRISTOPHER", "C"): "Chris Frye",
    ("COLEMAN", "JEFFREY", "H."): "Jeff Coleman",
    ("DIAMOND", "RUSSELL", "H"): "Russ Diamond",
    ("DELROSSO", "CARRIE", ""): "Carrie Lewis Delrosso",
    ("DANIELS", "THEODORE", ""): "Teddy Daniels",
    ("CARTWRIGHT", "MATTHEW", "A"): "Matt Cartwright",
    ("BOGNET", "JAMES", "ROCCO"): "Jim Bognet",
    ("MARSICANO", "MICHAEL", "PAUL"): "Mike Marsicano",
    ("BAKER", "JACLYN", "E"): "Jackie Baker",
    ("BAKER", "ELISABETH", "J."): "Lisa Baker",
    ("KEEGAN", "MARIAN", ""): "Marian Keegan",
    ("ROSENFELD", "MEGHAN", ""): "Meg Rosenfeld",
    ("PROBST", "TARAH", ""): "Tarah Probst",
    ("ADAMS", "JOSEPH", "W"): "Joseph W. Adams",
    ("BALU", "ELEFTERIE", "G"): "Theo Balu",
    ("BEIERLE", "ROBERT", ""): "Robert Beierle",
    ("ERTLE", "STEPHEN", "WALTER"): "Steve Ertle",
    ("PETRIZZO", "JOHN", ""): "John Petrizzo",
}

# DOS precinct code -> precinct name (2022-general parser's list).
DOS_PRECINCTS = {
    "10": "Blooming Grove", "20": "Delaware 1", "22": "Delaware 2",
    "25": "Dingman 1", "35": "Dingman 2", "40": "Greene",
    "50": "Lackawaxen 1", "52": "Lackawaxen 2", "55": "Lehman 1",
    "65": "Lehman 2", "70": "Matamoras Boro 1", "80": "Matamoras Boro 2",
    "90": "Milford Boro", "100": "Milford Twp", "110": "Palmyra",
    "120": "Porter", "130": "Shohola", "140": "Westfall",
}


def parse_dos(path):
    """Named-candidate precinct rows for the six DOS-carried contests."""
    out = []
    with open(path, newline="", encoding="latin-1") as fh:
        for rec in csv.reader(fh):
            if len(rec) < 21 or rec[0] != "2022" or rec[1] != "P" or \
                    rec[2] != COUNTY_CODE:
                continue
            office = DOS_OFFICE[rec[8]]
            precinct = DOS_PRECINCTS.get(rec[3])
            if precinct is None:
                raise ValueError("unmapped Pike DOS precinct code %r"
                                 % rec[3])
            if rec[9] not in ("DEM", "REP"):
                raise ValueError("unexpected party %r (office %s)"
                                 % (rec[9], office))
            name_key = (rec[11].upper(), rec[12].upper(), rec[13].upper())
            name = NAME_FIX.get(name_key)
            if name is None:
                raise ValueError("unmapped Pike DOS candidate %r" %
                                 (name_key,))
            if office == "U.S. House":
                district = str(int(rec[18]))
            elif office == "State Senate":
                district = str(int(rec[19]))
            elif office == "State House":
                district = str(int(rec[20]))
            else:
                district = ""
            out.append([COUNTY_NAME, precinct, office, district, rec[9],
                        name, rec[15]])
    return out


# --------------------------------------------------------------------------
# verification
# --------------------------------------------------------------------------

# emitted name -> ENR anchor (formal) name
VARIANT = {
    "Alex Khalil": "Alexandria Gloria Khalil",
    "Conor Lamb": "Conor J. Lamb",
    "John Fetterman": "John K. Fetterman",
    "Kathy Barnette": "Kathy J. Barnette",
    "Mehmet Oz": "Mehmet C. Oz",
    "George Bochetto": "George A. Bochetto",
    "Jeff Bartos": "Jeffrey A. Bartos",
    "Dave McCormick": "David H. McCormick",
    "Sean Gale": "Sean Peter Gale",
    "Carla Sands": "Carla Herd Sands",
    "Josh Shapiro": "Joshua D. Shapiro",
    "Lou Barletta": "Louis Barletta",
    "Dave White": "David J. White",
    "Melissa Hart": "Melissa A. Hart",
    "Bill McSwain": "William M. McSwain",
    "Charlie Gerow": "Charles R. Gerow",
    "Joe Gale": "Joseph Charles Gale",
    "Jake Corman": "Jacob Doyle Corman III",
    "Ray Sosa": "Raymond L. Sosa",
    "Clarice Schillinger": "Clarice D. Schillinger",
    "James Earl Jones": "James E. Jones",
    "Rick Saccone": "Richard Saccone",
    "John Brown": "John A. Brown",
    "Chris Frye": "Christopher C. Frye Jr",
    "Jeff Coleman": "Jeffrey H. Coleman",
    "Russ Diamond": "Russell H. Diamond",
    "Carrie Lewis Delrosso": "Carrie Delrosso",
    "Teddy Daniels": "Theodore Daniels",
    "Matt Cartwright": "Matthew A. Cartwright",
    "Jim Bognet": "James Rocco Bognet",
    "Mike Marsicano": "Michael Paul Marsicano",
    "Jackie Baker": "Jaclyn E. Baker",
    "Lisa Baker": "Elisabeth J. Baker",
    "Meg Rosenfeld": "Meghan Rosenfeld",
    "Theo Balu": "Elefterie G. Balu",
    "Steve Ertle": "Stephen Walter Ertle",
    "Mike Bukaj": "Michael A. Bukaj",
    "Michelle Tonkin": "Michelle R. Tonkin",
}
ANCHOR_OFFICES = set(DOS_OFFICE.values()) | {
    "Member of Democratic State Committee",
    "Member of Republican State Committee"}


def parse_anchor(path):
    """{ (office, district, party): {ENR formal candidate name: votes} }.

    The county anchor is a 6-column file:
    county,office,district,party,candidate,votes.
    """
    anchor = {}
    with open(path, newline="", encoding="latin-1") as fh:
        for rec in csv.reader(fh):
            if len(rec) < 6 or rec[0] != COUNTY_NAME or \
                    rec[1] not in ANCHOR_OFFICES:
                continue
            if rec[4] in ("", "Write Ins"):
                continue
            key = (rec[1], rec[2], rec[3])
            anchor.setdefault(key, {})[rec[4]] = int(rec[5])
    return anchor


def check_sheet(sums, printed):
    """Per contest & candidate: per-precinct sums == printed county total.

    Returns (n_checked, problems)."""
    n, problems = 0, []
    for key in sorted(printed):
        for name, total in sorted(printed[key].items()):
            n += 1
            mine = sums.get(key, {}).get(name, 0)
            if mine != total:
                problems.append("%s %s: per-precinct sum %s != printed "
                                "total %s" % (key[0] + ("" if key[0] ==
                                                       "Turnout" else
                                                       " d" + key[1]),
                                              name, mine, total))
    return n, problems


def check_turnout(rows):
    """Per precinct: DEM + REP turnout == total turnout row."""
    per = {}
    for r in rows:
        if r[2] == "Ballots Cast":
            per.setdefault(r[1], {})[r[4]] = int(r[6])
    problems = []
    for p, d in sorted(per.items()):
        if d.get("", 0) != d.get("DEM", 0) + d.get("REP", 0):
            problems.append("%s: turnout %s" % (p, d))
    return len(per), problems


def check_anchor_agree(anchor, out_rows):
    """Per candidate (VARIANT-mapped): ours vs anchor.

    Returns (n_matched, residual_notes, hard_problems) where residual
    notes are the expected pre-final-sheet State Committee lags.
    """
    ours = {}
    for r in out_rows:
        if r[2] not in ANCHOR_OFFICES or not r[5]:
            continue
        name = VARIANT.get(r[5], r[5])
        key = (r[2], r[3], r[4])
        ours.setdefault(key, {})
        ours[key][name] = ours[key].get(name, 0) + int(r[6])
    matched, residuals, problems = [], [], []
    for key in sorted(anchor):
        for name, total in sorted(anchor[key].items()):
            mine = ours.get(key, {}).get(name)
            if mine == total:
                matched.append((key, name, total))
            elif key[0].startswith("Member of") and mine is not None:
                residuals.append((key, name, total, mine))
            else:
                problems.append("%s %s [%s %s %s]: ours %s vs anchor %s" %
                                (name, "state committee" if
                                 key[0].startswith("Member of") else "",
                                 key[0], key[1], key[2], mine, total))
    return matched, residuals, problems


def sort_rows(dos_rows, html_rows, html_cand_seq):
    cand_seq = {}
    for r in dos_rows:
        k = (r[2], r[3], r[4], r[5])
        if k not in cand_seq:
            cand_seq[k] = len(cand_seq)
    # sheet-only offices: keep the sheet's printed order
    base = len(cand_seq)
    for ((office, _district), party, name), seq in \
            sorted(html_cand_seq.items(), key=lambda kv: kv[1]):
        k = (office, _district, party, name)
        if k not in cand_seq:
            cand_seq[k] = base + seq
    rows = dos_rows + html_rows
    rows.sort(key=lambda r: (PRECINCT_INDEX[r[1]],
                             office_rank(r[2]),
                             {"": 0, "DEM": 1, "REP": 2}.get(r[4], 9),
                             cand_seq.get((r[2], r[3], r[4], r[5]), 9999)))
    return rows


def write_csv(rows, dest):
    with open(dest, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(HEADER)
        w.writerows(rows)


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------

SHEET_MAIN_OFFICES = ["U.S. Senate", "Governor", "Lieutenant Governor",
                      "U.S. House", "State Senate", "State House"]


def main(argv):
    verify_only = "--verify-only" in argv
    args = [a for a in argv if a != "--verify-only"]
    if len(args) not in (3, 4):
        print(__doc__)
        return 2
    dos_txt, county_html, out_csv = args[0], args[1], args[2]
    anchor_path = args[3] if len(args) == 4 else ANCHOR_DEFAULT

    dos_rows = parse_dos(dos_txt)
    html_rows, printed, sums, cand_seq, warnings, blocks_info = \
        parse_html(county_html)

    # ---- 1. internal: sheet sums == printed totals for every contest ----
    n_sheet, problems = check_sheet(sums, printed)
    print("sheet internal check: %d printed candidate/turnout county "
          "totals == per-precinct sums" % n_sheet)
    for p in problems:
        print("  MISMATCH " + p)
    if problems:
        return 1

    # ---- 1b. certified (DOS) totals vs the pre-final sheet, per candidate
    dos_sums = {}
    for r in dos_rows:
        k = (r[2], r[3], r[4], r[5])
        dos_sums[k] = dos_sums.get(k, 0) + int(r[6])
    print("certified (DOS) vs sheet county totals, per candidate:")
    deltas = sorted((key[0], key[2], key[3], total,
                     total - sums.get((key[0], key[1]), {}).get(key[3], 0))
                    for key, total in dos_sums.items())
    for office, party, cand, total, delta in deltas:
        sheet = total - delta
        print("  %-22s %-3s %-24s DOS %6d  sheet %6d  delta %+d" %
              (office, party, cand, total, sheet, delta))

    # ---- 1c. turnout reconciliation ----
    n_p, tbad = check_turnout(html_rows)
    print("turnout: %d precincts checked; DEM+REP == total per precinct"
          % n_p)
    for p in tbad:
        print("  MISMATCH " + p)
    if tbad:
        return 1

    # ---- 1d. DOS block completeness ----
    have_blocks = {(r[1], r[2]) for r in dos_rows}
    want_blocks = {(p, o) for p in PRECINCTS
                   for o in DOS_OFFICE.values()}
    missing = sorted(want_blocks - have_blocks)
    print("DOS bulk: %d precinct candidate rows; %d of %d precinct-office "
          "blocks present" % (len(dos_rows), len(have_blocks & want_blocks),
                              len(want_blocks)))
    if missing:
        for m in missing:
            print("  MISSING %s" % str(m))
        return 1

    # candidate coverage (State House is district-limited, others 18/18)
    hd139 = {p for p in PRECINCTS if p in
             ("Blooming Grove", "Dingman 1", "Dingman 2", "Greene",
              "Lackawaxen 1", "Lackawaxen 2", "Matamoras Boro 1",
              "Matamoras Boro 2", "Milford Boro", "Milford Twp", "Palmyra",
              "Shohola", "Westfall")}
    hd189 = {"Delaware 1", "Delaware 2", "Lehman 1", "Lehman 2", "Porter"}
    coverage = {}
    for r in dos_rows:
        coverage.setdefault((r[2], r[3], r[4], r[5]), set()).add(r[1])
    for key, precincts in coverage.items():
        office, district = key[0], key[1]
        expected = set(PRECINCTS)
        if office == "State House":
            expected = hd139 if district == "139" else hd189
        if precincts != expected:
            print("  NOTE %s: not reported for %s (absent zero rows in "
                  "the bulk file)" % (key[3], sorted(expected -
                                                     precincts)))

    # ---- 1e. committee blocks ----
    print("committee blocks: %d of 18 precinct blocks parsed; %d block "
          "rows of warnings" % (len(blocks_info), len(warnings)))
    for w in warnings:
        print("  NOTE " + w)
    if len(blocks_info) != 18:
        print("  MISSING blocks: %s" %
              sorted(set(PRECINCTS) - set(blocks_info)))

    # ---- 2. anchor cross-check ----
    rows = sort_rows(dos_rows, html_rows, cand_seq)
    anchor = parse_anchor(anchor_path)
    matched, residuals, hard = check_anchor_agree(anchor, rows)
    print("anchor check: %d candidate rows compared against %s"
          % (len(matched) + len(residuals) + len(hard), anchor_path))
    for key, name, total in matched:
        if key[0] in ("Governor", "U.S. Senate"):
            print("  %s [%s %s] anchor %d == ours %d" %
                  (name, key[0], key[1], total, total))
    for key, name, total, mine in residuals:
        print("  STATE-COMMITTEE residual (pre-final sheet): %s[%s] %s: "
              "sum %d vs anchor %d (%+d)" % (key[0], key[1], name, mine,
                                             total, mine - total))
    for p in hard:
        print("  MISMATCH " + p)
    if hard:
        return 1

    if verify_only:
        print("verify-only: no files written.")
        return 0

    write_csv(rows, out_csv)
    counts = {}
    for r in rows:
        counts[r[2]] = counts.get(r[2], 0) + 1
    print("wrote %s (%d data rows, 7-col, %d precincts)" %
          (out_csv, len(rows), len({r[1] for r in rows})))
    for o, n in sorted(counts.items(), key=lambda kv: office_rank(kv[0])):
        print("  %-48s %d" % (o, n))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))