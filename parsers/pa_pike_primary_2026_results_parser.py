#!/usr/bin/env python3
r"""Pike County, PA 2026 Primary — precinct + countywide results parser.

Vendor family: the per-precinct source is the county's tabular
"Results per Precinct" report (Dominion, same family as Pike's 2022/2024
parsers), and the countywide source is the Dominion "Election Summary
Report" (ESR2 family, cf. Pike 2023 `pa_2023_summary_common.parse_esr2`).
Both PDFs carry a text layer, so no OCR and no DOS fallback were needed.

Both reports are HTML-to-PDF tables where a long cell wraps part of the
cell's text BELOW the row's numbers line ("BLOOMING" / numbers /
"GROVE TWP" is one precinct row; on the ESR2 side the gender tail word
of "Cheryl Ward-Simons - Female" lands below the values line).  A naïve
line parser loses such continuations.  Both halves therefore use the
same text-layout state machine over `pdftotext -layout` output:

* pure numeric tokens (`[\d,]+`, so "#1" stays a name word) mark
  data/value lines;
* every data row carries exactly one number per contest column, in
  column order (column x-positions verified in the coordinate probes
  work2026p/pike/probe.out / probe2.out), so values map to candidates
  by count, left to right;
* remaining furniture-free lines are wrapped-name fragments: the first
  fragment line after a data row is that row's name continuation, later
  ones before the next data row are the next row's prefix;
* candidate labels on both sides are validated against CONTEST_MANIFEST
  (per-precinct column layout) and against the official report's own
  row labels, so a regression prints an error instead of emitting
  mis-keyed votes.

Per-precinct format:
    D - GOVERNOR (Vote for 1)              <- contest header: ballot party
    Precinct  Josh Shapiro - DEM Write-in  <- candidate column header
    BLOOMING GROVE TWP                 371                  0
    Total                            3,471                 30

Data notes (documented source facts, not parsing losses):
  * Registered Voters / Ballots Cast: the per-precinct report carries no
    RV/BC rows -> none emitted, and no election-day/mail/provisional
    breakdown -> plain 7-column output header.
  * Write-ins: one combined per-precinct "Write-in" column (named +
    unresolved write-ins combined), emitted as "Write-In Totals"
    (Berks 2026 precedent).  The report ALSO prints individual named
    write-in columns (Doug Mastriano, Barbara Lee, ...) that are zero
    per precinct everywhere they occur; they are emitted as printed
    with the contest party.  Their county totals live inside the
    combined column, NOT in the named column: Governor REP write-in
    column = 115 = official 32 Doug Mastriano + 83 unresolved, so
    per-precinct named write-in sums (0) are expected to differ from
    official named write-in totals (32) — resolution happened after
    the per-precinct report.  (Cross-checked per contest in the
    verification run; see work2026p/pike/NOTES.md.)
  * Committee contests (per-township Committeeperson races and the
    countywide "County Committeeperson at Large") are reported by both
    sources; the per-precinct CSV keeps them (Berks 2026 precedent),
    the countywide CSV omits them (the other 2026 county CSVs carry
    statewide offices only).
  * Precinct names are kept exactly as printed, matching Pike's own
    committed 2024 files ("BLOOMING GROVE TWP", "DELAWARE TWP #1").

Countywide CSV (from "Pike County 2026 Primary Official Results.pdf",
Dominion ESR2): contest headers "D - OFFICE (Vote for N)", candidate
rows "Name PARTY ED MAIL PROVISIONAL Total", and below each contest's
"Total Votes" row a write-in detail block of named "NAME WRITE-IN" rows
plus one "Unresolved Write-In" row.  Statewide offices only (Governor,
Lieutenant Governor, U.S. House, State Senate, State House, Member of
D/R State Committee), per the other 2026 county CSVs.  Named write-in
rows keep their printed names with the contest party; the unresolved
remainder is emitted as "Write-In Unresolved" (Carbon 2024 precedent).
ESR2 "Total Votes" means named candidates + named write-ins, with
unresolved write-ins excluded (R Governor: Total 4,745 = Garrity 4,713
+ Mastriano 32; write-in block 32 + 83 unresolved = 115).  No
Registered Voters / Ballots Cast rows (the source's Elector Group
table is party-level turnout only).

Usage:
    python parsers/pa_pike_primary_2026_results_parser.py <precinct_pdf> <precinct_csv>
    python parsers/pa_pike_primary_2026_results_parser.py --county <official_pdf> <county_csv>
"""

import csv
import os
import re
import subprocess
import sys
import tempfile

COUNTY = "Pike"

CONTEST_RE = re.compile(r"^([DR]) - (.+?) \(Vote for (\d+)\)$")
NUM_RE = re.compile(r"[\d,]+")
GENDER_WORDS = {"MALE", "FEMALE", "NON-BINARY"}
PARTYTOKS = ("DEM", "REP", "WRITE-IN")
FURNITURE_RE = re.compile(
    r"^(Election Summary Report|Closed Primary|Pike County|"
    r"OFFICIAL RESULTS|Elector Group|Counting Group|Candidate|Cards Cast)"
    r"(\b|\s)", re.I)

# manifest: raw contest title -> printed candidate columns in order
# ("WRITE" marks the combined write-in column)
CONTEST_MANIFEST = {
    "GOVERNOR": [
        ("D", ["Josh Shapiro", "WRITE"]),
        ("R", ["Stacy Garrity", "WRITE", "Doug Mastriano"]),
    ],
    "LIEUTENANT GOVERNOR": [
        ("D", ["Austin Davis", "WRITE"]),
        ("R", ["John Ventre", "Jason Richey", "WRITE"]),
    ],
    "REPRESENTATIVE IN CONGRESS DIST 8": [
        ("D", ["Paige Cognetti", "WRITE"]),
        ("R", ["Rob Bresnahan, Jr.", "WRITE"]),
    ],
    "SENATOR IN THE GENERAL ASSEMBLY DIST 20": [
        ("D", ["Jackie Baker", "WRITE"]),
        ("R", ["Tyler Meyers", "Lisa Baker", "WRITE"]),
    ],
    "REPRESENTATIVE IN THE GENERAL ASSEMBLY DIST 139": [
        ("D", ["Dominique Azzollini", "WRITE"]),
        ("R", ["Jeff Olsommer", "WRITE"]),
    ],
    "REPRESENTATIVE IN THE GENERAL ASSEMBLY DIST 189": [
        ("D", ["Tarah Probst", "WRITE"]),
        ("R", ["Danica Hartenfels", "WRITE"]),
    ],
    "MEMBER DEMOCRATIC STATE COMMITTEE": [
        ("D", ["P.J. Jahn", "WRITE"]),
    ],
    "MEMBER REPUBLICAN STATE COMMITTEE": [
        ("R", ["Matthew Contreras", "Ray Tonkin", "Linda Touhsant",
               "Gina Mancato", "WRITE"]),
    ],
    "COUNTY COMMITTEEPERSON AT LARGE": [
        ("R", ["Ray Tonkin", "Rick Apgar", "Thomas Foran", "Gail Sebring",
               "Chris Strong", "Gina Mancato", "Chris Brighton",
               "Carl Will", "Lisa McAteer", "Matthew Contreras", "WRITE"]),
    ],
    "BLOOMING GROVE TWP COMMITTEEPERSON": [
        ("D", ["Robert E. Gemmel", "Roberta Skibber", "Sally K. Schwartz",
               "Ronni Terr", "Paul E. Bloomberg", "Cheryl Ward-Simons",
               "WRITE"]),
        ("R", ["Chris Reilly", "Raymond Larkin", "Terrie Reilly",
               "Will Clauss", "Danielle Morey", "Tim Morey", "George Kise",
               "Nancy Little", "Brian M. Vennie", "Mark E. Moulton",
               "WRITE"]),
    ],
    "DELAWARE TWP 1 COMMITTEEPERSON": [
        ("D", ["Carole J. Shipman", "WRITE", "Barbara Lee",
               "Dominica DeMario"]),
    ],
    "DELAWARE TWP 2 COMMITTEEPERSON": [
        ("D", ["Martha L. Raimon", "Cheryl R. Glenn", "WRITE"]),
    ],
    "DELAWARE TWP COMMITTEEPERSON": [
        ("R", ["Edward Hammond", "Gina Mancato", "Debbie Schueler",
               "Mike Bukaj", "Dawn Bukaj", "Harold Schueler",
               "Aaron Feingold", "Dennis Lee", "WRITE"]),
    ],
    "DINGMAN TWP 1 COMMITTEEPERSON": [
        ("D", ["WRITE", "Robert McCallum", "Dominique Azzolini"]),
    ],
    "DINGMAN TWP 2 COMMITTEEPERSON": [
        ("D", ["John Hellman", "Judy Leary-Wagner", "Jerry Weinstock",
               "Carol Witschel", "WRITE"]),
    ],
    "DINGMAN TWP COMMITTEEPERSON": [
        ("R", ["Annastasia Theodoropoulos", "Sharon Schroeder",
               "Yvonne Merrill", "Edward D. Nikles", "Jeanne Zingaro",
               "Lisa McAteer", "Bob Roche", "Theresa Brown", "WRITE"]),
    ],
    "GREENE TWP COMMITTEEPERSON": [
        ("D", ["Nancy L. Simon", "Donald Simon", "WRITE"]),
        ("R", ["Edward Geisler", "Debra K. Gunnip", "Michael Peifer",
               "Brian McKeon", "Carolyn Gjoka", "Barbara J. Gilpin",
               "Cecilia Luchese", "John Gilpin", "Kris Gjoka", "WRITE"]),
    ],
    "LACKAWAXEN TWP 1 COMMITTEEPERSON": [("D", ["WRITE"])],
    "LACKAWAXEN TWP 2 COMMITTEEPERSON": [
        ("D", ["Kenny Christianson", "WRITE"]),
    ],
    "LACKAWAXEN TWP COMMITTEEPERSON": [
        ("R", ["Linda Doering", "Jeffrey Shook", "Kathleen Hummel",
               "Mike Mancino", "Kraig Obermiller", "Kelly Obermiller",
               "Richard Carter", "WRITE"]),
    ],
    "LEHMAN TWP 1 COMMITTEEPERSON": [
        ("D", ["John FitzPatrick", "Lisa R. Miller", "Elena Eckert",
               "WRITE", "Celia Fitzpatrick", "Virgil Scudder"]),
    ],
    "LEHMAN TWP 2 COMMITTEEPERSON": [
        ("D", ["Christa Caceres", "WRITE", "Ann Marie Tekmen"]),
    ],
    "LEHMAN TWP COMMITTEEPERSON": [
        ("R", ["Robert Bateman", "Cheryl Bateman", "Dale Bastian",
               "Debra Bastian", "WRITE"]),
    ],
    "MATAMORAS BORO COMMITTEEPERSON": [
        ("D", ["WRITE"]),
        ("R", ["Jonathan Maney", "Lynn Homer", "Cory Homer",
               "Robert Bob Homer", "WRITE", "Tara Paradiso"]),
    ],
    "MILFORD BORO COMMITTEEPERSON": [
        ("D", ["Candace Mahala", "Benjamin Mahala", "Tamara Lee Chant",
               "WRITE"]),
        ("R", ["Lisa Emery", "George R. Lutfy", "Edward C. Loeschorn",
               "Joseph P. Dooley", "Matthew M. Osterberg", "WRITE"]),
    ],
    "MILFORD TWP COMMITTEEPERSON": [
        ("D", ["Elizabeth Kuemerle", "Joseph Kuemerle", "WRITE",
               "Sue Ann Vogelsberg"]),
        ("R", ["Dave Helms", "Matthew Contreras", "Kevin T. Stroyan",
               "Doug Luhrs", "Carl Will", "Penney Luhrs", "WRITE"]),
    ],
    "PALMYRA TWP COMMITTEEPERSON": [
        ("D", ["Daniel J. Bartley", "Patrice I. Pluto", "WRITE"]),
        ("R", ["Craig Ehrhardt", "Pat Lennon", "Ronald R. Schmalzle",
               "Jerome J. Lucchese", "Chris Strong", "Garrett Tonkin",
               "Peg Geisel", "Aidan X. Lucchese", "Michelle Tonkin",
               "Mary M. Lucchese", "WRITE"]),
    ],
    "PORTER TWP COMMITTEEPERSON": [
        ("D", ["WRITE"]),
        ("R", ["WRITE", "Robert O'Neill", "Gail Ann O'Neill",
               "Thomas Roew", "Kathleen Roew"]),
    ],
    "SHOHOLA TWP COMMITTEEPERSON": [
        ("D", ["Bell Hodges-Smith", "Cordell Bowman", "Andrea A. Paskin",
               "WRITE"]),
        ("R", ["Tauni Ruvolis", "Keith W. Raser", "Christopher Decker",
               "Jessica Adler Decker", "Keith Decker", "Cherie Graber",
               "George Fluhr", "WRITE"]),
    ],
    "WESTFALL TWP COMMITTEEPERSON": [
        ("D", ["Adriane Wendell", "Michael Trenner", "Richard J. Ryan",
               "Maleyne M. Syracuse", "WRITE"]),
        ("R", ["Linda Touhsant", "Bette Eagan", "Debi Fischer",
               "Dorit Smith", "Paul C. Fischer", "WRITE"]),
    ],
}

OFFICE_MAP = [
    ("REPRESENTATIVE IN CONGRESS", "U.S. House"),
    ("SENATOR IN THE GENERAL ASSEMBLY", "State Senate"),
    ("REPRESENTATIVE IN THE GENERAL ASSEMBLY", "State House"),
    ("LIEUTENANT GOVERNOR", "Lieutenant Governor"),
    ("GOVERNOR", "Governor"),
    ("MEMBER DEMOCRATIC STATE COMMITTEE",
     "Member of Democratic State Committee"),
    ("MEMBER REPUBLICAN STATE COMMITTEE",
     "Member of Republican State Committee"),
]


def clean_label_text(text):
    """Strip gender tokens, separator dashes and party tokens."""
    out = []
    for tok in text.split():
        u = tok.upper().strip(",.-")
        if u in GENDER_WORDS or u in ("DEM", "REP") or tok == "-":
            continue
        out.append(tok)
    return " ".join(" ".join(out).split())


def place_name(raw_title):
    """Committee block title -> 'Delaware Twp 1 Committeeperson'."""
    place = re.sub(r"\s+", " ", raw_title.strip()).upper()
    words = []
    for w in place.split():
        if w in ("TWP", "TOWNSHIP"):
            words.append("Twp")
        elif w == "BORO":
            words.append("Boro")
        else:
            words.append(w.capitalize())
    return " ".join(words)


def map_office(raw_title):
    """(office, district, is_statewide) from a contest title w/o D/R prefix."""
    u = re.sub(r"\s+", " ", raw_title.upper()).strip()
    u = u.replace("TOWNSHIP", "TWP")
    for key, office in OFFICE_MAP:
        if u.startswith(key):
            m = re.search(r"\bDIST\W*(\d+)", u)
            district = str(int(m.group(1))) if m else ""
            return office, district, True
    if u == "COUNTY COMMITTEEPERSON AT LARGE":
        return "County Committeeperson at Large", "", False
    m = re.match(r"^(.+?) COMMITTEEPERSON$", u)
    if m:
        return place_name(m.group(1)) + " Committeeperson", "", False
    raise RuntimeError("unmapped contest office %r" % raw_title)


def read_lines(path):
    """pdftotext -layout output for a PDF, plain lines for a .txt extract."""
    if path.lower().endswith(".pdf"):
        tmp = tempfile.NamedTemporaryFile(mode="w+", suffix=".txt",
                                          delete=False)
        tmp.close()
        subprocess.run(["pdftotext", "-layout", path, tmp.name], check=True,
                       capture_output=True)
        with open(tmp.name, encoding="utf-8") as fh:
            lines = fh.read().splitlines()
        os.unlink(tmp.name)
        return lines
    with open(path, encoding="utf-8") as fh:
        return fh.read().splitlines()


def clean_line(raw):
    """Whitespace-normalized non-furniture line, or None."""
    s = re.sub(r"\s+", " ", raw.strip())
    if not s:
        return None
    low = s.lower()
    if "results per precinct" in low or "file://" in s:
        return None
    if re.match(r"^\d/\d/\d{4}", s):
        return None
    if low.startswith("pike county pa 05"):
        return None
    return s


def numeric_idx(toks):
    return [i for i, t in enumerate(toks) if NUM_RE.fullmatch(t)]


def is_name_fragment(toks):
    """All-caps, party-free, digit-free line: wrapped name continuation."""
    for t in toks:
        if t in PARTYTOKS or t == "-":
            return False
        if NUM_RE.fullmatch(t):
            return False
        if any(c.islower() for c in t):
            return False
    return bool(toks)


def attach_fragment(name, frag):
    return (name + " " + frag).strip() if name else frag


# ------------------------------------------------------- per-precinct side

def split_blocks(lines):
    """[(raw_title, ballot_party, block_lines)] for each contest header."""
    blocks = []
    cur = None
    for raw in lines:
        s = clean_line(raw)
        if s is None:
            continue
        cm = CONTEST_RE.match(s)
        if cm:
            cur = (cm.group(2).strip(),
                   "DEM" if cm.group(1) == "D" else "REP", [])
            blocks.append(cur)
        elif cur is not None:
            cur[2].append(s)
    return blocks


def check_precinct_order(raw_title, names, ref_names):
    """names must be ref-order subsequence (subset elections report less)."""
    if names == ref_names:
        return
    keep = [n for n in ref_names if n in set(names)]
    if names != keep:
        raise RuntimeError(
            "contest %r precinct names/order %r not a subsequence of %r"
            % (raw_title, names, keep or ref_names))


def parse_precinct(path):
    ref_names = None
    contests = []
    for raw_title, ballot_party, block in split_blocks(read_lines(path)):
        key = re.sub(r"\s+", " ", raw_title.upper())
        entries = CONTEST_MANIFEST.get(key)
        if entries is None:
            raise RuntimeError("contest %r not in CONTEST_MANIFEST"
                               % raw_title)
        matching = [c for bp, c in entries if bp == ballot_party[0]]
        if len(matching) != 1:
            raise RuntimeError("no manifest entry for %r ballot %s"
                               % (raw_title, ballot_party))
        cands = matching[0]
        ncols = len(cands)
        office, district, is_statewide = map_office(raw_title)

        # single streaming pass; wrapped name cells are resolved greedily
        # against the reference precinct-name set once known (see docstring)
        rows = []
        totals = None
        pending = []

        def frag_line(f):
            nonlocal pending
            if ref_names is not None and rows and \
                    rows[-1]["precinct"] not in ref_names:
                cand = attach_fragment(rows[-1]["precinct"], f)
                if cand in ref_names or any(n.startswith(cand)
                                            for n in ref_names):
                    rows[-1]["precinct"] = cand
                    return
            pending.append(f)

        ref_set = None
        for s in block:
            toks = s.split()
            nidx = numeric_idx(toks)
            if not nidx:
                if is_name_fragment(toks):
                    frag_line(" ".join(toks))
                continue
            head_toks = toks[:nidx[0]]
            nums = [int(toks[i].replace(",", "")) for i in nidx]
            if toks[0].lower() == "total":
                if totals is not None:
                    raise RuntimeError(
                        "duplicate Total row in contest %r" % raw_title)
                totals = nums
                continue
            if len(nums) != ncols or nidx[-1] != len(toks) - 1:
                raise RuntimeError(
                    "data row %r: %d numeric token(s) vs %d columns in %r"
                    % (s, len(nums), ncols, raw_title))
            name = attach_fragment(" ".join(pending), " ".join(head_toks))
            pending = []
            rows.append({"precinct": name, "votes": nums})
        if totals is None:
            raise RuntimeError("no Total row in contest %r" % raw_title)
        if len(totals) != ncols:
            raise RuntimeError("Total row in %r: %d values vs %d columns"
                               % (raw_title, len(totals), ncols))
        if pending:
            raise RuntimeError(
                "dangling name fragment(s) %r in contest %r"
                % (pending, raw_title))
        names = [r["precinct"] for r in rows]
        if not all(names):
            raise RuntimeError("empty precinct name in contest %r"
                               % raw_title)
        if ref_names is None:
            if any(not r["precinct"].strip() for r in rows):
                raise RuntimeError("unusable reference contest %r"
                                   % raw_title)
            ref_names = names
        else:
            check_precinct_order(raw_title, names, ref_names)
        contests.append({
            "raw": raw_title, "ballot_party": ballot_party, "office": office,
            "district": district, "is_statewide": is_statewide,
            "candidates": cands, "rows": rows, "total": totals,
        })
    if len(contests) != sum(len(v) for v in CONTEST_MANIFEST.values()):
        raise RuntimeError(
            "found %d contest blocks, manifest expects %d"
            % (len(contests), sum(len(v) for v in CONTEST_MANIFEST.values())))
    return contests


def emit_precinct(contests, out_path):
    n = 0
    warnings = []
    with open(out_path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["county", "precinct", "office", "district", "party",
                    "candidate", "votes"])
        for c in contests:
            sums = [0] * len(c["candidates"])
            for r in c["rows"]:
                if len(r["votes"]) != len(c["candidates"]):
                    raise RuntimeError(
                        "row %r: %d values vs %d columns in %r"
                        % (r["precinct"], len(r["votes"]),
                           len(c["candidates"]), c["raw"]))
                for i, (cand, v) in enumerate(zip(c["candidates"],
                                                  r["votes"])):
                    name = "Write-In Totals" if cand == "WRITE" else cand
                    w.writerow([COUNTY, r["precinct"], c["office"],
                                c["district"], c["ballot_party"], name, v])
                    sums[i] += v
                    n += 1
            if sums != c["total"]:
                warnings.append("contest %r (%s): column sums %r != printed "
                                "Total %r" % (c["raw"], c["ballot_party"],
                                              sums, c["total"]))
    for w_ in warnings:
        print("WARNING:", w_)
    if not warnings:
        print("internal check: all %d contests reconcile column sums with "
              "printed Totals" % len(contests))
    return n


# ------------------------------------------------------------- county side


def split_party(name_tokens):
    """(name tokens, first DEM/REP/WRITE-IN token) of a report row's name."""
    row_party = None
    pre = []
    for t in name_tokens:
        if row_party is None and t in PARTYTOKS:
            row_party = t
        else:
            pre.append(t)
    return pre, row_party


def parse_county(path):
    """Official ESR2 -> per-contest dict: named rows, write-in rows, total."""
    contests = []
    cur = None
    pending = []               # fragments prefixing the next row
    last_row = [None]          # boxed: last named or write-in row

    for raw in read_lines(path):
        s = re.sub(r"\s+", " ", raw.strip())
        if not s:
            continue
        if re.match(r"^Page: \d+ of \d+", s) or re.match(r"^\d/\d/\d{4}", s):
            continue
        if re.match(r"^\d{1,2}:\d{2}:\d{2}", s):
            continue
        cm = CONTEST_RE.match(s)
        if cm:
            if pending:
                raise RuntimeError("dangling name fragment %r" % pending)
            office, district, is_statewide = map_office(cm.group(2).strip())
            cur = {"party": "DEM" if cm.group(1) == "D" else "REP",
                   "raw": cm.group(2).strip(), "office": office,
                   "district": district, "statewide": is_statewide,
                   "candidates": [], "total": None, "wir": []}
            contests.append(cur)
            continue
        if cur is None:
            continue
        if FURNITURE_RE.match(s):
            continue
        toks = s.split()
        if all(t.upper() in ("ELECTION", "MAIL", "PROVISIONAL", "DAY",
                             "TOTAL") for t in toks):
            continue
        if re.match(r"^(DEM|REP|WRITE-IN)$", s):
            continue
        if s.upper().startswith("TOTAL VOTES"):
            toks = s.split()
            nums = [int(t.replace(",", "")) for t in toks
                    if NUM_RE.fullmatch(t)]
            if len(nums) != 4:
                raise RuntimeError("Total Votes row %r" % s)
            cur["total"] = nums
            continue
        toks = s.split()
        nidx = numeric_idx(toks)
        if not nidx:
            # wrapped name fragment: continues a name that ends mid-dash
            if last_row[0] is not None and \
                    last_row[0]["name"].endswith("-"):
                last_row[0]["name"] = attach_fragment(
                    last_row[0]["name"], " ".join(toks))
                last_row[0]["suffix"] = True
            else:
                pending.append(s)
            continue
        if len(nidx) != 4 or nidx[-1] != len(toks) - 1:
            raise RuntimeError("unexpected numeric row: %r" % s)
        pre, row_party = split_party(toks[:nidx[0]])
        raw_name = attach_fragment(" ".join(pending), " ".join(pre))
        pending = []
        nums = [int(toks[i].replace(",", "")) for i in nidx]
        row = {"name": raw_name, "nums": nums, "suffix": None}
        if row_party == "WRITE-IN":
            cur["wir"].append(row)
        elif row_party in ("DEM", "REP"):
            if cur["total"] is not None:
                raise RuntimeError("named row after Total Votes: %r" % s)
            row["party"] = row_party
            cur["candidates"].append(row)
        else:
            # party-less row: only the Unresolved Write-In lines
            if not re.fullmatch(r"Unresolved Write-In", raw_name, re.I):
                raise RuntimeError("row without party token: %r" % s)
            cur["wir"].append(row)
        last_row[0] = row
    return contests


def emit_county(contests, out_path):
    header = ["county", "office", "district", "party", "candidate", "votes",
              "election_day", "mail", "provisional"]
    out = []
    notes = []
    for c in contests:
        if not c["statewide"]:
            continue
        named_sum = 0
        wir_named = []
        unresolved = 0
        for r in c["candidates"]:
            votes, ed, mi, pr = r["nums"][3], r["nums"][0], r["nums"][1], \
                r["nums"][2]
            if ed + mi + pr != votes:
                raise RuntimeError("breakdown mismatch %r %r: %r"
                                   % (r["name"], c["raw"], r["nums"]))
            named_sum += votes
            out.append([COUNTY, c["office"], c["district"], r["party"],
                        clean_label_text(r["name"]), votes, ed, mi, pr])
        for r in c["wir"]:
            votes, ed, mi, pr = r["nums"][3], r["nums"][0], r["nums"][1], \
                r["nums"][2]
            if ed + mi + pr != votes:
                raise RuntimeError("breakdown mismatch %r %r: %r"
                                   % (r["name"], c["raw"], r["nums"]))
            if re.fullmatch(r"Unresolved Write-In", r["name"], re.I):
                unresolved += votes
                out.append([COUNTY, c["office"], c["district"], c["party"],
                            "Write-In Unresolved", votes, ed, mi, pr])
            else:
                wir_named.append((clean_label_text(r["name"]), votes, ed,
                                  mi, pr))
        for name, votes, ed, mi, pr in sorted(wir_named):
            out.append([COUNTY, c["office"], c["district"], c["party"],
                        name, votes, ed, mi, pr])
        wir_sum = sum(v for _, v, _, _, _ in wir_named) + unresolved
        if c["total"] is not None:
            tv = c["total"]
            if named_sum + sum(v for _, v, _, _, _ in wir_named) != tv[3]:
                notes.append(
                    "%s (%s): named %d + named write-ins %d != Total Votes %d"
                    % (c["raw"], c["party"], named_sum,
                       sum(v for _, v, _, _, _ in wir_named), tv[3]))
            if tv[0] + tv[1] + tv[2] != tv[3]:
                notes.append("%s (%s): Total Votes breakdown %r != %d"
                             % (c["raw"], c["party"], tv, tv[3]))
        print("  %-52s named %5d write-in detail %5d (unres %4d)"
              % (c["office"] or c["raw"], named_sum, wir_sum, unresolved))
    with open(out_path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(header)
        w.writerows(out)
    print("wrote %d rows -> %s" % (len(out), out_path))
    for note in notes:
        print("NOTE:", note)
    return out, contests, notes


def main(argv):
    if "--county" in argv:
        i = argv.index("--county")
        args = argv[i + 1:]
        if len(args) != 2:
            sys.exit("Usage: ... --county <official_pdf> <county_csv>")
        contests = parse_county(args[0])
        emit_county(contests, args[1])
        return
    if len(argv) == 3:
        contests = parse_precinct(argv[1])
        n = emit_precinct(contests, argv[2])
        print("wrote %d rows -> %s" % (n, argv[2]))
        return
    sys.exit("Usage:\n"
             "  parser <precinct_pdf> <precinct_csv>\n"
             "  parser --county <official_path> <county_csv>")


if __name__ == "__main__":
    main(sys.argv)
