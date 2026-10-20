#!/usr/bin/env python3
"""Pike County, PA 2022 General — precinct-level results parser.

Source situation (documented): the county's certified document
"~/code/openelections-sources-pa/2022/general/Pike PA 2022 General Election
Summary Report.pdf" is a Dominion *county-level* Election Summary Report
(4 pages; 18 of 18 precincts reported).  It contains NO precinct-level
vote rows — Pike posted no per-precinct report for the 11/2022 general
(verified: the county's own "Past Election Results" page lists only the
Summary Report for 2022; the wayback capture of that page from 2023-03-31
shows the same single document; the www2.pikepa.org/election/*.htm
per-precinct sheets were discontinued after the 2021 general and the
December 2022 wayback capture of 2022general.htm is still the primary).

Precinct-level figures therefore come from the PA Department of State
official certified precinct returns bulk file (same fallback the Cameron
2022 general parser uses):
  https://www.pa.gov/content/dam/copapwp-pagov/en/dos/resources/
      voting-and-elections/bulk-data/ElectionReturns_2022_General_PrecinctReturns.txt
(ASCII comma-delimited, quoted fields; Pike = county code 52; 288 rows =
18 precincts x candidate rows).  Pike's 18 precinct codes (10..140) match
the county's own precinct list in the 2022 primary HTML sheet and the
precinct names in the repo's 2018/2020 Pike files.

Consequences (inherent to the DOS bulk source, not parsing losses):
  * No election-day / mail / provisional breakdown -> 7-column header,
    no per-precinct Registered Voters / Ballots Cast rows, no write-ins
    (the county summary PDF's county-level Unresolved Write-Ins — U.S.
    Senate 43, Governor 14, U.S. House 8: 111, SD-20: 46, HD-139: 1,120,
    HD-189: 29 — are not assignable; the two ballot questions have zero
    write-ins and are captured below).

Ballot questions (county summary PDF only; the DOS bulk file has none):
  * "Blooming Grove Ambulance Tax Question" was voted only in Blooming
    Grove ("Precincts Reported: 1 of 1", 4,140 registered) -> emitted for
    the Blooming Grove precinct (Yes 1,479 / No 866 / Write Ins 0), an
    exact assignment.
  * "Matamoras Boro Fire Protection Tax Question" covers Matamoras Boro
    1 and 2 ("2 of 2"); the per-precinct split of Yes 566 / No 320 is
    unknown -> NOT emitted per the no-invention rule.

The optional county summary input (same ESR2 format as the 2023 engine
pa_2023_summary_common.parse_esr2, but that engine does not handle this
PDF's wrapped candidate names, e.g. "Douglas V. Mastriano/Carrie" /
party / number / "Lewis Delrosso") produces the county-level CSV
(6-column, plus county-level Registered Voters / Ballots Cast rows and
the per-contest Unresolved Write-In rows).  When the summary is given,
its printed totals are the internal-verification anchor for the
precinct-file sums; the ENR-API-derived county anchor
2022/20221108__pa__general__county.csv matches 18/18 candidate rows.

Names follow the county summary PDF; Governor joint tickets are reduced
to the governor (convention used across the 2022 county files).

Milford Boro vs Milford Twp: both DOS rows carry precinct name "MILFORD"
(codes 90 and 100).  Disambiguated as 90 = Milford Boro, 100 = Milford
Twp per (a) the column order of the county's own 2022 primary sheet
(Milford Boro precedes Milford Twp; codes ascend 90, 100) and (b) vote
magnitudes consistent across 2018, 2020 and 2022 (Twp larger).

Usage:
    uv run python parsers/pa_pike_general_2022_results_parser.py \
        <dos_prcres_txt> <out_precinct_csv> [summary.pdf|txt <out_county_csv>]
    Add --verify (anywhere) to run all checks and write nothing.
"""

import csv
import os
import re
import sys

COUNTY_NAME = "Pike"
COUNTY_CODE = "52"

HEADER = ["county", "precinct", "office", "district", "party", "candidate",
          "votes"]
COUNTY_HEADER = ["county", "office", "district", "party", "candidate", "votes"]
OFFICE_ORDER = ["U.S. Senate", "Governor", "U.S. House", "State Senate",
                "State House", "Blooming Grove Ambulance Tax Question",
                "Matamoras Boro Fire Protection Tax Question"]

OFFICES = {  # DOS office code -> (office, which district field)
    "USS": ("U.S. Senate", None),
    "GOV": ("Governor", None),
    "USC": ("U.S. House", "cand_district"),
    "STS": ("State Senate", "cand_district"),
    "STH": ("State House", "cand_district"),
}

# DOS candidate (Last, First, Middle) -> printed name (county summary PDF /
# names used across the 2022 general county files).
NAME_FIX = {
    ("FETTERMAN", "JOHN", "K"): "John Fetterman",
    ("OZ", "MEHMET", "C"): "Mehmet Oz",
    ("GERHARDT", "ERIK", ""): "Erik Gerhardt",
    ("WEISS", "RICHARD", "L"): "Richard L. Weiss",
    ("WASSMER", "DANIEL", ""): "Daniel Wassmer",
    ("SHAPIRO", "JOSHUA", "D"): "Josh Shapiro",
    ("MASTRIANO", "DOUGLAS", "V"): "Douglas V. Mastriano",
    ("HACKENBURG", "JONATHAN", "MATTHEW"): "Matt Hackenburg",
    ("DIGIULIO", "CHRISTINA", "PK"): "Christina DiGiulio",
    ("SOLOSKI", "JOSEPH", "P"): "Joe Soloski",
    ("CARTWRIGHT", "MATTHEW", "A"): "Matt Cartwright",
    ("BOGNET", "JAMES", "ROCCO"): "Jim Bognet",
    ("BAKER", "JACLYN", "E"): "Jackie Baker",
    ("BAKER", "ELISABETH", "J."): "Lisa Baker",
    ("ROSENFELD", "MEGHAN", ""): "Meg Rosenfeld",
    ("ADAMS", "JOSEPH", "W"): "Joseph W. Adams",
    ("PROBST", "TARAH", ""): "Tarah Probst",
    ("ERTLE", "STEPHEN", "WALTER"): "Steve Ertle",
}

# DOS precinct code -> precinct name (matches the county's own 18-precinct
# list and the repo's 2018/2020 Pike files).
PRECINCT_NAMES = {
    "10": "Blooming Grove",
    "20": "Delaware 1",
    "22": "Delaware 2",
    "25": "Dingman 1",
    "35": "Dingman 2",
    "40": "Greene",
    "50": "Lackawaxen 1",
    "52": "Lackawaxen 2",
    "55": "Lehman 1",
    "65": "Lehman 2",
    "70": "Matamoras Boro 1",
    "80": "Matamoras Boro 2",
    "90": "Milford Boro",
    "100": "Milford Twp",
    "110": "Palmyra",
    "120": "Porter",
    "130": "Shohola",
    "140": "Westfall",
}

# Ballot questions from the county summary PDF.  The second has an unknown
# per-precinct split (2 precincts) and is never emitted into the precinct
# file; it lives only in the county-level CSV.
QUESTIONS = [
    ("Blooming Grove Ambulance Tax Question", [("Yes", 1479), ("No", 866),
     ("Write Ins", 0)], ["10"]),
    ("Matamoras Boro Fire Protection Tax Question", [("Yes", 566), ("No", 320),
     ("Write Ins", 0)], False),
]

# County-level totals printed in the certified county summary PDF (Dominion
# Election Summary Report, printed 1/18/2023, all 18 precincts reported).
# Unresolved write-ins are printed separately and are NOT in the DOS file.
PDF_TOTALS = {
    "U.S. Senate": {
        "John Fetterman": 9821,
        "Mehmet Oz": 14792,
        "Erik Gerhardt": 293,
        "Richard L. Weiss": 148,
        "Daniel Wassmer": 142,
    },
    "Governor": {
        "Josh Shapiro": 10339,
        "Douglas V. Mastriano": 14371,
        "Matt Hackenburg": 210,
        "Christina DiGiulio": 128,
        "Joe Soloski": 85,
    },
    "U.S. House": {"Matt Cartwright": 10030, "Jim Bognet": 14844},
    "State Senate": {"Jackie Baker": 9040, "Lisa Baker": 16025},
    "State House": {"Meg Rosenfeld|139": 6525, "Joseph W. Adams|139": 10749,
                    "Tarah Probst|189": 2989, "Steve Ertle|189": 3529},
    "Blooming Grove Ambulance Tax Question": {
        "Yes": 1479, "No": 866, "Write Ins": 0},
}


# --------------------------------------------------------------------------
# county summary (Dominion ESR2) parsing — county-level rows + write-ins
# --------------------------------------------------------------------------

CONTEST_MAP = {
    "UNITED STATES SENATOR": ("U.S. Senate", ""),
    "GOVERNOR & LT GOVERNOR": ("Governor", ""),
    "REPRESENTATIVE IN CONGRESS DIST 8": ("U.S. House", "8"),
    "SENATOR IN THE GENERAL ASSEMBLY DIST 20": ("State Senate", "20"),
    "REPRESENTATIVE IN THE GENERAL ASSEMBLY DIST 139": ("State House", "139"),
    "REPRESENTATIVE IN THE GENERAL ASSEMBLY DIST 189": ("State House", "189"),
    "BLOOMING GROVE AMBULANCE TAX QUESTION":
        ("Blooming Grove Ambulance Tax Question", ""),
    "MATAMORAS BORO FIRE PROTECTION TAX QUESTION":
        ("Matamoras Boro Fire Protection Tax Question", ""),
}

PARTIES = {"DEM", "REP", "LIB", "GRN", "KEY", "CON", "IND", "NOP", "LBR"}

PARTY_ONLY_LINE = re.compile(r"^\s+([A-Z]{2,4})\s+([\d,]+)\s*$")
CAND_ROW = re.compile(
    r"^(?P<name>[A-Za-z].*?)\s+(?:(?P<party>[A-Z]{2,4})\s+)?(?P<votes>[\d,]+)\s*$")

NAME_MAP = {
    "JOSH SHAPIRO/AUSTIN DAVIS": "Josh Shapiro",
    "DOUGLAS V. MASTRIANO/CARRIE LEWIS DELROSSO": "Douglas V. Mastriano",
    "MATT HACKENBURG/TIM MCMASTER": "Matt Hackenburg",
    "CHRISTINA DIGIULIO/MICHAEL BAGDES-CANNING": "Christina DiGiulio",
    "JOE SOLOSKI/NICOLE SHULTZ": "Joe Soloski",
}


def fix_name(raw):
    raw = re.sub(r"\s+", " ", raw.strip())
    up = raw.upper()
    if up in NAME_MAP:
        return NAME_MAP[up]
    # joint ticket without map entry: drop the "/second name" half
    if "/" in raw:
        return raw.split("/")[0].strip()
    return raw


def parse_summary(path):
    lines = _read_layout_lines(path)
    rows = []
    printed = {}
    current = None
    registered = ballots = None
    for raw in lines:
        s = raw.strip()
        if not s or s.startswith("Page:"):
            continue
        m = re.match(r"^(.*?\S)\s*\(vote for (\d+)\)$", s, re.I)
        if m:
            key = re.sub(r"\s+", " ", m.group(1)).upper()
            if key in CONTEST_MAP:
                current = CONTEST_MAP[key]
            else:
                raise ValueError("unmapped summary contest header: " + key)
            continue
        if re.match(r"^Registered Voters:", s):
            m = re.search(r"of\s+([\d,]+)", s)
            if m:
                registered = int(m.group(1).replace(",", ""))
            continue
        if re.match(r"^Ballots Cast:", s):
            m = re.search(r"([\d,]+)", s)
            if m:
                ballots = int(m.group(1).replace(",", ""))
            continue
        if current is None or s == "Total" or re.match(
                r"^(precincts reported|times cast|candidate\b|"
                r"elector group|counting group)", s, re.I):
            continue
        if re.match(r"^Total Votes", s, re.I):
            m = re.search(r"([\d,]+)\s*$", s)
            if m:
                printed.setdefault(current, {})["total"] = \
                    int(m.group(1).replace(",", ""))
            continue
        if re.search(r"Write-In", s, re.I):
            m = re.search(r"([\d,]+)\s*$", s)
            if m:
                printed.setdefault(current, {})["writeins"] = \
                    int(m.group(1).replace(",", ""))
                rows.append([COUNTY_NAME, current[0], current[1], "",
                             "Write Ins", int(m.group(1).replace(",", ""))])
            continue
        if re.match(r"^(YES|NO)\b", s):
            m = re.search(r"([\d,]+)\s*$", s)
            if m:
                rows.append([COUNTY_NAME, current[0], current[1], "",
                             s.split()[0].capitalize(),
                             int(m.group(1).replace(",", ""))])
            continue
        m = CAND_ROW.match(s)
        if m:
            party = m.group("party") if m.group("party") in PARTIES else ""
            name = fix_name(m.group("name"))
            if m.group("party") and not party:
                name = fix_name(m.group("name") + " " + m.group("party"))
            if name:
                rows.append([COUNTY_NAME, current[0], current[1], party, name,
                             int(m.group("votes").replace(",", ""))])
            continue
        raise ValueError("unparsed summary line: %r" % raw)
    meta = {}
    if registered is not None:
        meta["Registered Voters"] = registered
    if ballots is not None:
        meta["Ballots Cast"] = ballots
    return rows, meta, printed


def _read_layout_lines(path):
    if path.lower().endswith(".pdf"):
        import subprocess
        import tempfile
        out = tempfile.NamedTemporaryFile(mode="w", suffix=".txt",
                                          delete=False)
        out.close()
        subprocess.run(["pdftotext", "-layout", path, out.name], check=True,
                       capture_output=True, text=True)
        path = out.name
    with open(path, encoding="utf-8") as fh:
        lines = [l.rstrip() for l in fh]
    return _premerge_wrapped(lines)


def _premerge_wrapped(lines):
    """Join wrapped candidate rows  "Name Part1" / indented "PARTY  1,234" /
    "Part2"  into "Name Part1 Part2 PARTY 1,234"."""
    merged = []
    i, n = 0, len(lines)
    while i < n:
        m = PARTY_ONLY_LINE.match(lines[i])
        if not m or not merged:
            merged.append(lines[i])
            i += 1
            continue
        prev = merged[-1]
        if not prev.strip() or any(c.isdigit() for c in prev):
            merged.append(lines[i])
            i += 1
            continue
        merged.pop()
        tail = ""
        j = i + 1
        while j < n and not lines[j].strip():
            j += 1
        if j < n and lines[j] == lines[j].lstrip() and \
                not any(c.isdigit() for c in lines[j]):
            tail = lines[j].strip()
            i = j + 1
        merged.append("%s %s %s %d" % (prev.strip(), tail, m.group(1),
                                       int(m.group(2).replace(",", ""))))
    return merged


def check_summary(rows, printed):
    """Candidate-only "Total Votes" and separate "Unresolved Write-In"."""
    from collections import defaultdict
    agg = defaultdict(int)
    for r in rows:
        if r[1] in ("Registered Voters", "Ballots Cast"):
            continue
        agg[(r[1], r[2])] += int(r[5])
    problems = []
    for (office, district), info in sorted(printed.items()):
        want_c = info.get("total", 0)
        want_w = info.get("writeins", 0)
        got_w = agg.get((office, district), 0)
        got_c = got_w - want_w
        if got_c != want_c or got_w != want_c + want_w:
            problems.append("%s %s: candidates %d (want %d) + write-ins %d "
                            "(want %d)" % (office, district, got_c, want_c,
                                           got_w, want_c + want_w))
    return problems


# --------------------------------------------------------------------------
# DOS bulk-file parsing — precinct-level rows
# --------------------------------------------------------------------------

def county_rows_from_dos(path):
    out = []
    with open(path, newline="", encoding="latin-1") as fh:
        for row in csv.reader(fh):
            if len(row) < 26 or row[0] != "2022" or row[1] != "G":
                continue
            if row[2] != COUNTY_CODE:
                continue
            out.append(
                {
                    "precinct_code": row[3],
                    "office_code": row[8],
                    "party": row[9],
                    "cand_last": row[11],
                    "cand_first": row[12],
                    "cand_middle": row[13],
                    "votes": int(row[15]),
                    "cand_district": row[5],  # USC/STS/STH district number
                    "uscd": row[18],
                    "ssd": row[19],
                    "shd": row[20],
                }
            )
    return out


def cand_name(r):
    key = (r["cand_last"], r["cand_first"], r["cand_middle"])
    name = NAME_FIX.get(key)
    if name is None:
        raise ValueError("unmapped Pike candidate: %r" % (key,))
    return name


def to_output_rows(dos_rows):
    out = []
    for r in dos_rows:
        office, dfield = OFFICES[r["office_code"]]
        district = str(int(r[dfield])) if dfield else ""
        out.append(
            [COUNTY_NAME, PRECINCT_NAMES[r["precinct_code"]], office,
             district, r["party"], cand_name(r), str(r["votes"])])
    out.sort(key=lambda x: (PRECINCT_INDEX[x[1]], OFFICE_ORDER.index(x[2]),
                            x[3]))
    return out


PRECINCT_INDEX = {name: int(code) for code, name in PRECINCT_NAMES.items()}


def question_rows():
    return [[COUNTY_NAME, "Blooming Grove", name, "", "", cand, str(votes)]
            for name, cands, precincts in QUESTIONS if precincts
            for (cand, votes) in cands]


def check_dos(out_rows, dos_rows):
    """Precinct sums per candidate vs the county summary PDF totals, plus
    block completeness: (office, district, precinct) count."""
    cand_sums = {}
    blocks = set()
    for r in out_rows:
        key = (r[2], r[3], r[5])
        cand_sums[key] = cand_sums.get(key, 0) + int(r[6])
        blocks.add((r[2], r[3], r[1]))
    problems = []
    n = 0
    for office, cands in PDF_TOTALS.items():
        if office == QUESTIONS[0][0]:
            q = {r[5]: int(r[6]) for r in out_rows if r[2] == office}
            for cand, total in cands.items():
                n += 1
                if q.get(cand) != total:
                    problems.append("%s: %s sum %s != PDF total %s"
                                    % (office, cand, q.get(cand), total))
            continue
        if office == "State Senate":
            district = "20"
        else:
            district = "8" if office == "U.S. House" else ""
        for key, total in cands.items():
            n += 1
            if "|" in key:  # State House/Question: cand|district[|precinct]
                parts = key.split("|")
                cand, hd = parts[0], parts[1]
                mine = cand_sums.get((office, hd, cand), 0)
            else:
                cand = key
                mine = cand_sums.get((office, district, cand), 0)
            if mine != total:
                problems.append("%s: %s: precinct sum %d != PDF total %d"
                                % (office, cand if "|" not in key else key,
                                   mine, total))
    # 18 precincts x 4 statewide offices = 72; State House in all 18
    # (13 in HD-139 + 5 in HD-189); + 1 Blooming Grove question block
    n += 1
    want_blocks = 72 + 18 + 1
    if len(blocks) != want_blocks:
        problems.append("precinct/office blocks: %d (want %d)"
                        % (len(blocks), want_blocks))
    return n, problems


def write_csv(header, rows, dest):
    with open(dest, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(header)
        w.writerows(rows)


def main(argv):
    verify = "--verify" in argv
    args = [a for a in argv if a != "--verify"]
    if len(args) not in (2, 4):
        print(__doc__)
        return 2
    dos_txt, out_prec = args[0], args[1]
    if not os.path.exists(dos_txt):
        print("DOS bulk file not found: %s" % dos_txt, file=sys.stderr)
        return 1
    dos_rows = county_rows_from_dos(dos_txt)
    if not dos_rows:
        print("No Pike rows found in %s" % dos_txt, file=sys.stderr)
        return 1
    out = to_output_rows(dos_rows) + question_rows()
    out.sort(key=lambda r: (PRECINCT_INDEX[r[1]], OFFICE_ORDER.index(r[2]),
                            r[3]))
    n_dos, problems = check_dos(out, dos_rows)
    if verify or problems:
        print("verify: %d precinct-file candidate totals checked against the "
              "county summary PDF totals" % n_dos)
        for p in problems:
            print("  MISMATCH " + p)
        if problems:
            return 1
        print("verify: all precinct sums match the summary PDF; %d data rows, "
              "%d precincts" % (len(out), len({r[1] for r in out})))
    if verify:
        return 0
    if len(args) == 4:
        summary_src, out_county = args[2], args[3]
        county_rows, meta, printed = parse_summary(summary_src)
        s_problems = check_summary(county_rows, printed)
        n_s = len(printed)
        if verify or s_problems:
            print("verify: %d county-summary contests checked internally" % n_s)
            for p in s_problems:
                print("  MISMATCH " + p)
            if s_problems:
                return 1
        print("verify: county summary parses cleanly; %d contests, %d "
              "candidate+writein rows" % (n_s, len(county_rows)))
        for name, votes in meta.items():
            county_rows.append([COUNTY_NAME, name, "", "", "", str(votes)])
        write_csv(COUNTY_HEADER, county_rows, out_county)
        print("wrote %s (%d data rows)" % (out_county, len(county_rows)))
    write_csv(HEADER, out, out_prec)
    print("wrote %s (%d data rows, 7-col) from %s" % (out_prec, len(out),
                                                      dos_txt))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))