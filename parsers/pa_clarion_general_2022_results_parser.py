#!/usr/bin/env python3
"""Clarion County 2022 general — precinct-level results from the official
Dominion "Election Summary Report / OFFICIAL RESULTS PRECINCT REPORT"
(openelections-sources-pa 2022/general "Clarion PA 2022MunicipalResults1.pdf",
320 scanned pages, one ~8-page section per precinct, 40 precincts).

The PDF has no text layer (SAVIN scan), so the real input is the PaddleOCR-VL
hosted-API extraction (work2023p/ocr_api.py, chunked uploads), same
"===== PAGE N =====" markdown + single-line HTML <table> format as the 2023
Clarion sources.  Each precinct section: a cover page
("Summary for: All Contests, NN.NN - <Precinct>, ..."; "Registered Voters:
<ballots> of <rv> (pct)"; "Ballots Cast: n") followed by contest blocks —
"## <Office> (Vote for N)", a Times Cast table row, candidate rows
(name, party, Total), an aggregate "Write-in" row, "Total Votes", then a
named WRITE-IN detail block (individuals + "Scattered") that is skipped.

Each contest reports Total-only columns (no election-day/mail/provisional
breakdown), so the output uses the 7-column header.

Statewide/legislative candidate names are canonicalized by (office, party) —
the slates are fixed across all 40 precincts and OCR repeatedly chops leading
letters ("Ink Gerhardt", "Jenn GT Thompson", "/rite-in").  Local-office
candidate names are kept as printed; suspicious OCR names are flagged.

Usage: pa_clarion_general_2022_results_parser.py <input.txt> <output.csv>
"""

import csv
import html
import re
import sys

FIELDNAMES = ["county", "precinct", "office", "district", "party",
              "candidate", "votes"]

COUNTY = "Clarion"

# Fixed candidate slates for the four countywide/legislative contests, keyed
# by (canonical office, party).  Values are the names as printed in the
# source (verified against the page images); OCR-mangled variants map here.
SLATES = {
    ("U.S. Senate", "DEM"): "John Fetterman",
    ("U.S. Senate", "REP"): "Mehmet Oz",
    ("U.S. Senate", "LIB"): "Erik Gerhardt",
    ("U.S. Senate", "GRN"): "Richard L. Weiss",
    ("U.S. Senate", "KEY"): "Daniel Wassmer",
    ("Governor", "DEM"): "Josh Shapiro / Austin Davis",
    ("Governor", "REP"): "Douglas V. Mastriano / Carrie Lewis DelRosso",
    ("Governor", "LIB"): "Matt Hackenburg / Tim McMaster",
    ("Governor", "GRN"): "Christina DiGiulio / Michael Bagdes-Canning",
    ("Governor", "KEY"): "Joe Soloski / Nicole Shultz",
    ("U.S. House", "DEM"): "Mike Molesevich",
    ("U.S. House", "REP"): "Glenn GT Thompson",
    ("State House", "REP"): "Donna Oberlander",
}

PARTIES = {"DEM", "REP", "LIB", "GRN", "KEY", "CON", "IND", "NOP", "FWD",
           "CST", "PRO", "SUS", "LBR", "WF", "SSP"}

# OCR repairs verified against the printed source PDF page images.
#
# 1. Vote cells shifted up one row (candidate's number lost, following rows
#    each showing the previous row's value).  Values below were read from the
#    page images:
#    - Callensburg Borough Governor (source PDF page 27): Shapiro 21,
#      Mastriano 35, Hackenburg 0, DiGiulio 0, Soloski 0, Write-in 0, Total 56.
#    - Clarion Borough Second U.S. Senate (source PDF page 42): Fetterman 123,
#      Oz 100, Gerhardt 2, Weiss 2, Wassmer 2, Write-in 0, Total 229.
REPAIRS = {
    ("Callensburg Borough", "Governor", "", "DEM"): 21,
    ("Callensburg Borough", "Governor", "", "REP"): 35,
    ("Callensburg Borough", "Governor", "", "LIB"): 0,
    ("Clarion Borough Second", "U.S. Senate", "", "DEM"): 123,
    ("Clarion Borough Second", "U.S. Senate", "", "REP"): 100,
    ("Clarion Borough Second", "U.S. Senate", "", "LIB"): 2,
    # Shippenville Borough Governor (source PDF page 275): Shapiro 80,
    # Mastriano 108, Hackenburg 1, DiGiulio 1, Soloski 2, Write-in 0,
    # Total 192.  OCR split Shapiro's row into two DEM rows (80 + a stray
    # 108 that belongs to Mastriano) and dropped the REP value.
    ("Shippenville Borough", "Governor", "", "DEM"): 80,
    ("Shippenville Borough", "Governor", "", "REP"): 108,
}

# 2. "Representative in the General Assembly - 62nd District" (Elk Township,
#    source PDF page 83) and "- 6 and District" (Strattanville Borough) are
#    OCR misreads of "63rd District"; Clarion County has no 62nd-district
#    precincts and every other State House header reads 63rd.
DISTRICT_FIX = {"62": "63", "6": "63"}

VOTEFOR_RE = re.compile(r"\(?\s*vote\s+for\s+(\d+)\)?", re.I)
SUMMARY_FOR_RE = re.compile(
    r"summary for:?\s*all contests,\s*(\d+(?:\.\d+)?)\s*[-–]?\s*(.+?),"
    r"\s*all tabulators", re.I)
RV_RE = re.compile(r"(?:r|g|p)?egistered voters:?\s*([\d,]+)\s*of\s*([\d,]+)",
                   re.I)
BC_RE = re.compile(r"(?:b|g|a)?allots cast:?\s*([\d,]+)", re.I)
# Aggregate "Write-in" row inside a candidate block: OCR mangles the label
# ("/ write-in", "rite-in", "Vrite-in"), but the party cell stays empty.
WRITEIN_NAME_RE = re.compile(r"[a-z]", re.I)


def is_writein_label(cell):
    s = re.sub(r"[^a-z]", "", cell.lower())
    return bool(s) and len(s) <= 10 and ("rite" in s or "writ" in s)


# Candidate identification for contests whose "##" header OCR dropped:
# fixed slates -> canonical office.
CANDIDATE_OFFICE = [
    (("fetterman", "mehmet", "gerhardt", "weiss", "wassmer"), "U.S. Senate"),
    (("shapiro", "mastriano", "hackenburg", "soloski"), "Governor"),
    (("molesevich", "thompson"), "U.S. House"),
    (("oberlander",), "State House"),
]


def infer_office(names):
    """Office for a header-less contest from its candidate names."""
    joined = " ".join(n.lower() for n in names)
    for keys, office in CANDIDATE_OFFICE:
        if any(k in joined for k in keys):
            return office
    return None


def map_office(header):
    """Contest header (Vote-for stripped) -> (office, district)."""
    h = re.sub(r"\s+", " ", html.unescape(header)).strip()
    h = re.sub(r"\(?\s*vote\s+for\s+\d+\)?", " ", h, flags=re.I)
    h = re.sub(r"\s+", " ", h).strip(" -–")

    m = re.match(r"^United States Senator$", h, re.I)
    if m:
        return "U.S. Senate", ""
    if re.match(r"^Governor( and Lieutenant Governor)?$", h, re.I):
        return "Governor", ""
    m = re.match(r"^Representative in Congress\s*[-–]\s*(\d+)", h, re.I)
    if m:
        return "U.S. House", m.group(1)
    m = re.match(r"^(?:United States )?Senator in the General Assembly"
                 r"\s*[-–]\s*(\d+)", h, re.I)
    if m:
        return "State Senate", m.group(1)
    m = re.match(r"^Representative in the General Assembly\s*[-–]\s*(\d+)",
                 h, re.I)
    if m:
        dist = m.group(1)
        dist = DISTRICT_FIX.get(dist, dist)
        return "State House", dist
    return h, ""


def clean_name(name):
    name = html.unescape(name)
    name = re.sub(r"\s+", " ", name).strip(" .,:;")
    return name


def cells_from_row(row_html):
    return [clean_name(re.sub(r"<[^>]+>", "", c)).strip()
            for c in re.findall(r"<td[^>]*>(.*?)</td>", row_html, re.S)]


def first_int(cells, start):
    for c in cells[start:]:
        c = c.replace(",", "").strip()
        if re.fullmatch(r"\d+", c):
            return int(c)
    return None


def is_times_cast(cells):
    joined = " ".join(cells).lower()
    if "times cast" in joined or "times cast" in cells[0].lower():
        return True
    return bool(re.search(r"\d[\d,]*\s*/\s*\d[\d,]*", joined)) \
        and not any(re.fullmatch(r"\d[\d,]*", c.replace(",", "").strip())
                    for c in cells)


def parse(text, problems):
    """One walk over the OCR text.  Returns (rows, precincts, contests,
    recon) where recon maps (precinct, office, district) ->
    (row_sum, total_votes)."""
    rows = []
    precincts = []          # ordered unique precinct names
    contests = []
    recon = {}

    office_district = {}    # office -> district learned from named contests

    precinct = None
    contest = None          # (office|None, district)
    cand_sum = 0
    total_votes = None
    cur_rows = []           # buffered (party, name, votes) for this contest

    rv = None
    bc = None

    def finish_contest():
        nonlocal cand_sum, total_votes, contest, cur_rows
        if contest is not None and precinct is not None and cur_rows:
            office, dist = contest
            names = [n for p, n, v in cur_rows if p and p != "WRITE"
                     and n != "Write Ins"]
            if office is None:
                office = infer_office(names)
                if office is None:
                    problems.append("unnamed contest with unidentifiable "
                                    "candidates in %s: %r"
                                    % (precinct, names))
                else:
                    dist = office_district.get(office, "")
                    if dist == "":
                        problems.append("no district known for %s (%s)"
                                        % (office, precinct))
            elif office not in office_district and dist != "":
                office_district[office] = dist
            # canonicalize slate names also for header-dropped blocks
            out = []
            wi_seen = False
            for party, name, votes in cur_rows:
                if name == "Write Ins":
                    # OCR noise can produce a stray aggregate row; a contest
                    # has exactly one Write Ins row
                    if wi_seen:
                        for prev in out:
                            if prev[5] == "Write Ins":
                                prev[6] += votes
                                break
                        continue
                    wi_seen = True
                if party and name != "Write Ins" \
                        and (office, party) in SLATES:
                    name = SLATES[(office, party)]
                out.append([COUNTY, precinct, office, dist, party, name,
                            votes])
            rows.extend(out)
            key = (precinct, office, dist)
            recon[key] = (cand_sum, total_votes)
            if total_votes is None:
                problems.append("no Total Votes row: %s | %s | %s"
                                % (precinct, office, dist))
            elif cand_sum != total_votes:
                problems.append(
                    "TOTALS MISMATCH %s | %s | %s: rows %d != Total Votes %d"
                    % (precinct, office, dist, cand_sum, total_votes))
        contest = None
        cand_sum = 0
        total_votes = None
        cur_rows = []

    def start_contest(header_text):
        nonlocal contest
        finish_contest()
        office, dist = map_office(header_text)
        contest = (office, dist)
        contests.append(contest)

    pending = []  # non-header lines that may be a wrapped "##" header

    def try_pending_header():
        if not pending:
            return
        joined = " ".join(pending).strip()
        pending.clear()
        if VOTEFOR_RE.search(joined) and len(joined) < 160 \
                and not re.search(r"summary for|times cast", joined, re.I):
            start_contest(joined)

    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("====="):
            continue

        # ---- contest header (markdown "## ...") --------------------------
        if line.startswith("##"):
            try_pending_header()
            h = re.sub(r"^#+\s*", "", line).strip()
            if VOTEFOR_RE.search(h):
                start_contest(h)
            else:
                # wrapped header fragment; buffered until "(Vote for N)"
                pending.append(h)
            continue

        # ---- plain text lines ---------------------------------------------
        if "<table" not in line:
            plain = re.sub(r"</?div[^>]*>", " ", line).strip()
            m = SUMMARY_FOR_RE.search(plain)
            if m:
                finish_contest()
                num, name = m.group(1), m.group(2)
                name = re.sub(r"\s+", " ", name).strip()
                precinct = name
                precincts.append(precinct)
                contest = None
                continue
            if precinct is not None:
                m = RV_RE.search(plain)
                if m:
                    rv = int(m.group(2).replace(",", ""))  # the "of" number
                    rows.append([COUNTY, precinct, "Registered Voters", "",
                                 "", "", rv])
                    rv = None
                    continue
                m = BC_RE.search(plain)
                if m:
                    bc = int(m.group(1).replace(",", ""))
                    rows.append([COUNTY, precinct, "Ballots Cast", "",
                                 "", "", bc])
                    bc = None
                    continue
            if VOTEFOR_RE.search(plain) and len(plain) < 160:
                pending.append(plain)
                try_pending_header()
            continue

        # ---- HTML table rows ------------------------------------------------
        if contest is None:
            continue
        for row_html in re.findall(r"<tr>(.*?)</tr>", line, re.S):
            cells = cells_from_row(row_html)
            if not cells or len(cells) < 2:
                continue
            first = cells[0]
            low = first.lower()
            joined = " ".join(cells).lower()

            if is_times_cast(cells):
                if total_votes is not None and cur_rows:
                    # previous contest block ended (Total Votes seen) but its
                    # "##" header successor was dropped by OCR: this table is
                    # a new, header-less contest
                    finish_contest()
                    contest = (None, "")
                    contests.append(contest)
                continue
            if "candidate" in low and "party" in joined:
                if total_votes is not None and cur_rows:
                    finish_contest()
                    contest = (None, "")
                    contests.append(contest)
                continue
            if "total vot" in joined:
                n = first_int(cells, 1)
                total_votes = n
                if n is None:
                    problems.append("Total Votes unreadable: %s | %s | %s"
                                    % (precinct, contest[0], contest[1]))
                continue
            # bare separator row between the two blocks
            if not any(re.search(r"\d", c) for c in cells):
                if joined.strip() in ("total", ""):
                    continue

            party_cell = cells[1].upper().strip(" .,:;")
            nums = [c.replace(",", "").strip() for c in cells[1:]
                    if re.fullmatch(r"\d[\d,]*", c.replace(",", "").strip())]

            # bare "WRITE-IN" first cell with only a value left = detail-row
            # fragment (name dropped); the aggregate row always has 3+ cells
            is_bare_writein = first.upper().rstrip(".,") in \
                ("WRITE-IN", "WRITE-1N", "WRITEIN")
            if is_bare_writein and len(cells) <= 2:
                continue
            # aggregate "Write-in" row inside the candidate block (party cell
            # empty, or the vote value OCR-slid into the party column)
            if is_writein_label(first) and nums \
                    and party_cell not in PARTIES \
                    and not party_cell.startswith("WRITE"):
                cand_sum += int(nums[0])
                cur_rows.append(("", "Write Ins", int(nums[0])))
                continue
            # named write-in detail rows (party cell WRITE-IN ...)
            if party_cell.startswith("WRITE") or is_bare_writein:
                continue
            if party_cell in PARTIES and nums:
                name = first
                if contest[0] is None or (contest[0], party_cell) \
                        not in SLATES:
                    if len(name) < 3 or name.islower():
                        problems.append(
                            "suspicious candidate name %r in %s | %s | %s"
                            % (name, precinct, contest[0], contest[1]))
                cand_sum += int(nums[0])
                cur_rows.append((party_cell, name, int(nums[0])))
                continue
            if party_cell in PARTIES:
                # candidate row whose vote cell OCR lost or replaced
                # ("John Fetterman DEM Total"); repaired from the page image
                problems.append(
                    "CANDIDATE VALUE UNREADABLE %s | %s | %s | %s: %r"
                    % (precinct, contest[0], contest[1], party_cell, cells))
                continue
            if nums and not is_writein_label(first):
                # candidate row whose party cell OCR dropped: recover the
                # party from the fixed slate by exact name match
                party = ""
                if contest[0] is not None:
                    for (office, p), canon in SLATES.items():
                        if office == contest[0] \
                                and re.sub(r"\s+", " ", first).lower() \
                                == canon.lower():
                            party = p
                            break
                if party:
                    cand_sum += int(nums[0])
                    cur_rows.append((party, first, int(nums[0])))
                    continue
                problems.append("unparsed numeric row in %s | %s | %s: %r"
                                % (precinct, contest[0], contest[1], cells))
            elif nums:
                problems.append("unparsed numeric row in %s | %s | %s: %r"
                                % (precinct, contest[0], contest[1], cells))

    finish_contest()
    return rows, precincts, contests, recon


def main(argv):
    if len(argv) < 3:
        sys.exit("Usage: %s <input.txt> <output.csv>" % argv[0])
    src, out_path = argv[1], argv[2]
    with open(src, encoding="utf-8") as fh:
        text = fh.read()

    problems = []
    rows, precincts, contests, recon = parse(text, problems)

    # apply page-image-verified repairs; a repaired (precinct, office,
    # district, party) collapses to exactly one row with the repaired value
    repaired_keys = set(REPAIRS)
    seen_repair = {}
    kept = []
    for r in rows:
        key = (r[1], r[2], r[3], r[4])
        if key in repaired_keys:
            if key in seen_repair:
                print("repair collapsed duplicate: %s (dropped votes %s)"
                      % (key, r[6]))
                continue
            seen_repair[key] = True
            if r[6] != REPAIRS[key]:
                print("repair applied: %s -> votes %d (was %s)"
                      % (key, REPAIRS[key], r[6]))
                r[6] = REPAIRS[key]
        kept.append(r)
    rows = kept
    by_key = {(r[1], r[2], r[3], r[4]): r for r in kept
              if r[2] not in ("Registered Voters", "Ballots Cast")}
    for key, votes in REPAIRS.items():
        if key not in seen_repair:
            # row was dropped (unreadable value): rebuild from the repair
            precinct, office, dist, party = key
            r = [COUNTY, precinct, office, dist, party,
                 SLATES.get((office, party), ""), votes]
            rows.append(r)
            by_key[key] = r
            print("repair inserted: %s -> votes %d" % (key, votes))
    rows.sort(key=lambda r: (precincts.index(r[1]), r[2], r[3], r[4], r[5]))

    # re-check per-contest totals after repairs
    from collections import defaultdict
    agg = defaultdict(int)
    for r in rows:
        if r[2] not in ("Registered Voters", "Ballots Cast"):
            agg[(r[1], r[2], r[3])] += int(r[6])
    problems = [p for p in problems if not p.startswith("TOTALS MISMATCH")]
    for key, (row_sum, total_votes) in recon.items():
        if total_votes is not None and agg.get(key, 0) != total_votes:
            problems.append(
                "TOTALS MISMATCH %s | %s | %s: rows %d != Total Votes %d"
                % (key[0], key[1], key[2], agg.get(key, 0), total_votes))

    with open(out_path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(FIELDNAMES)
        w.writerows(rows)

    n_mismatch = sum(1 for p in problems if p.startswith("TOTALS MISMATCH"))
    n_unread = sum(1 for p in problems if "unreadable" in p)
    print("precincts parsed: %d" % len(precincts))
    print("contests parsed: %d (%d unique offices)"
          % (len(contests), len(set(contests))))
    print("rows written: %d" % len(rows))
    print("problems: %d (mismatches %d, unreadable %d)"
          % (len(problems), n_mismatch, n_unread))
    for p in problems[:80]:
        print("  - %s" % p)
    print("wrote %s" % out_path)
    return 0 if not problems else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))