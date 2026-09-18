#!/usr/bin/env python3
"""Clarion County 2023 municipal primary — county-level results from the
official "Clarion County Official County-Wide Combined Report 2023 Primary.pdf"
(Dominion "Election Summary Report / Closed Primary", 100 pages, scanned).

The source is countywide-only (one aggregate table per contest; no per-precinct
sections), so this parser produces the county-level CSV only.

The scanned PDF has no text layer, so the real input is the PaddleOCR-VL
extraction (work2023p/txt/Clarion__Combined_Report_2023_Primary__ocr.txt):
contest headers appear as markdown/div lines
("Justice of the Supreme Court (DEM) (Vote for 1) DEM") and each contest's
numbers live in a single-line HTML <table> with candidate rows, a
"Total Votes" row and a WRITE-IN detail block ("Scattered", named write-ins,
"Unresolved Write-In").

OCR noise handled: chopped leading letters on names ("at Dugan", "II Beck"),
garbled header fragments ("Region I DEM)", "(Vote or 2)", "ownship ..."),
lowercase "total Votes".  Undervotes and Overvotes rows do not appear in this
report format.  Named write-in details (and "Scattered") are folded into a
single "Write-ins" row per contest, matching the county's 2023 general file.

Primary rules: the contest header carries the party — every candidate row and
the aggregate Write-ins row carry the contest party (party-empty write-in rows
would collide across the DEM/REP sections of the same office); metadata rows
(Registered Voters / Ballots Cast) stay party-empty.

Usage: pa_clarion_primary_2023_results_parser.py <input.txt|pdf> <output.csv>
"""

import csv
import html
import re
import subprocess
import sys
import tempfile

FIELDNAMES = ["county", "office", "district", "party", "candidate",
              "votes", "election_day", "mail", "provisional"]

COUNTY = "Clarion"

# OCR-repaired candidate names.  Keys are the lowercased garbled OCR text;
# every fix was verified against the printed source PDF (page noted) or
# against the same candidate's clean appearance elsewhere in this report
# (cross-filed other party / write-in detail) or in the county's 2023
# general file (parsers/pa_clarion_general_2023_results_parser.py output).
NAME_FIX = {
    # statewide judicial slates (known 2023 candidates)
    "at dugan": "Pat Dugan",                     # Superior Court DEM
    "imika lane": "Timika Lane",                 # Superior Court DEM
    "ii beck": "Jill Beck",                      # Superior Court DEM
    "l beck": "Jill Beck",
    "ryan neft": "Bryan Neft",                   # Commonwealth Court DEM
    "arry f smail jr": "Harry F. Smail Jr.",     # Superior Court REP (p10)
    "larry f smail jr": "Harry F. Smail Jr.",
    # verified against the printed PDF pages
    "raxton white": "Braxton White",             # County Commissioner (p3)
    "osh prince": "Josh Prince",                 # Commonwealth Court (p10)
    "irke wise": "Kirke Wise",                   # County Commissioner (p10)
    "brew welsh": "Drew Welsh",                  # District Attorney (p4/11)
    "caryn montana": "Karyn Montana",            # County Auditor (p12)
    "ebekah weckerly": "Rebekah Weckerly",       # County Auditor (p12)
    "amy winger": "Amy Winger",                  # County Auditor (p12)
    "misty ditz": "Misty Ditz",                  # County Auditor (p12)
    "olene weaver frampton": "Jolene Weaver Frampton",  # Treasurer (p13)
    "roger swartfager": None,                    # printed as-is (p18)
    "tick forbes": "Rick Forbes",                # Magisterial DJ (p39)
    "melissa d pierce": None,                    # printed as-is (p40)
    "orandon a thompson": "Brandon A Thompson",  # Coroner (p40)
    "inda ann runyan": "Linda Ann Runyan",       # Coroner (p40)
    "ennis king": "Dennis King",                 # Clarion Boro 4yr (p29)
    "arbara mortimer": "Barbara Mortimer",       # East Brady 2yr (p29/31)
    "patrick g aaron": "Patrick G Aaron",        # Clarion Twp supervisor (p27)
    "ames jim averill": "James Jim Averill",     # Clarion Boro 2yr (p26)
    "chach garbarino": "Zach Garbarino",         # Clarion Boro 4yr (p26)
    "oy mccluskey": "Joy McCluskey",             # East Brady 2yr (p31)
    "rich spessard": "Erich Spessard",           # Knox Boro (general file)
    "odd macbeth": "Todd MacBeth",               # Clarion Boro 2yr (p27 WI)
    "lavid l lewis": "David L Lewis",            # Knox Twp (general file)
    "heron miles": "Theron Miles",               # Limestone Twp (general)
    "have estadt": "Dave Estadt",                # Millcreek Twp (general)
    "teven greenawalt": "Steven Greenawalt",     # Highland Twp (p63)
    "lavid corte": "David Corte",                # Farmington Twp (p81)
    "mary jean slaughter": "Mary Jean Slaugenhoup",  # Perry Twp (p60)
    "mark beichner": "Mark Beichner",            # Toby Twp write-in (p83)
    "eric barnett": None,                        # printed as-is (p71)
    "karina libecco": None,                      # printed as-is (p70)
    "pamela curry": None,                        # printed as-is (p70)
    "roger crick": None,                         # printed as-is (p70)
    "paul r woodburne": None,                    # printed as-is (p71)
    "games beary": "James Beary",                # Keystone SD (REP clean)
    "ric weiser": "Eric Weiser",                 # Keystone SD (REP clean)
    "gen swartfager": "Ben Swartfager",          # Keystone SD (REP clean)
    "cott b daum": "Scott B Daum",               # Keystone SD (DEM clean)
    "athy vanish": "Cathy Vanish",               # Keystone SD (write-in)
    "arrett l carulli": "Garrett L Carulli",     # AC Valley (REP clean)
    "david w eggleton sr": "David W Eggleton Sr",  # AC Valley (general)
    "christopher mogus": "Christopher Mogus",    # AC Valley (general)
    "jenny kelly": "Denny Kelly",                # Karns City (p91/92)
    "penny kelly": "Denny Kelly",                # Karns City (p91/92)
    "irenda ealey": "Brenda Ealey",              # Karns City (p91/92)
    "renda ealey": "Brenda Ealey",
    "ara hackwelder": "Tara Hackwelder",         # Karns City (p91/92)
    "sara hackwelder": "Tara Hackwelder",
    "rames r morris": "James R Morris",          # Milcreek Twp auditor (p61)
    "erry sweeney": "Terry Sweeney",             # Union SD (p100)
    "ressa smith": "Tressa Smith",               # Union SD (p100)
    "helly atzeni": "Shelly Atzeni",             # Union SD (p100)
    "effrey a kriebel": "Jeffrey A Kriebel",     # Union SD (p100)
    "ricia hepler": "Tricia Hepler",             # Union SD (p100)
}
NAME_FIX = {k: v for k, v in NAME_FIX.items() if v is not None}

STATEWIDE = {
    "justice of the supreme court": "Justice of the Supreme Court",
    "judge of the superior court": "Judge of the Superior Court",
    "judge of the commonwealth court": "Judge of the Commonwealth Court",
    "county commissioners": "County Commissioner",
    "county auditors": "County Auditor",
    "county treasurer": "County Treasurer",
    "district attorney": "District Attorney",
    "prothonotary": "Prothonotary",
    "register/recorder": "Register/Recorder",
}

LOCAL_RE = re.compile(
    r"^(Township Supervisor|Township Auditor|Township Tax Collector|"
    r"Borough Council|Borough Auditor|Borough Mayor|Borough Tax Collector|"
    r"Tax Collector|School Director)"
    r"\s*\(?(\d)\s*Year Term\)?\s+(.+)$", re.IGNORECASE)

VOTE_FOR_RE = re.compile(r"\(?\s*Vote\s+(?:for|or)\s*(\d+)\)?", re.I)
PARTY_PAREN_RE = re.compile(r"\((DEM|REP)\)")
PARTY_TRAIL_RE = re.compile(r"\(?(DEM|REP)\)?\s*$")
WRITEIN_CELL_RE = re.compile(r"\(?\s*write-?in\s*\)?\s*$", re.I)


def expand_district(dist, notes):
    """Twp/Boro -> Township/Borough, matching the county's 2023 general file."""
    d = re.sub(r"\s+", " ", dist).strip()
    d = re.sub(r"\bTwp\b\.?", "Township", d)
    d = re.sub(r"\bBoro\b\.?", "Borough", d)
    d = d.replace("St Petersburg", "St. Petersburg")
    if "Region TV" in d:  # OCR for "Region IV" (a Region V contest also exists)
        d = d.replace("Region TV", "Region IV")
        notes.append("district OCR fix: 'Region TV' -> 'Region IV' "
                     "(%s)" % d)
    return d


def map_contest(header, notes):
    """Contest header -> (office, district, party). Party comes from the header."""
    h = re.sub(r"\s+", " ", header.strip())
    m = PARTY_PAREN_RE.search(h) or PARTY_TRAIL_RE.search(h)
    party = m.group(1) if m else ""
    h = PARTY_PAREN_RE.sub(" ", h)
    h = VOTE_FOR_RE.sub(" ", h)
    # wrapped headers can end with a stray party token after the vote-for
    # group ("... Region I DEM)") — strip every trailing party token
    while True:
        h2 = PARTY_TRAIL_RE.sub(" ", h)
        if h2 == h:
            break
        h = h2
    h = re.sub(r"\(Vote\s*$", " ", h)
    h = re.sub(r"\(\s*$", " ", h)
    h = re.sub(r"\s+", " ", h).strip()
    # chopped leading token ("ownship Supervisor ...")
    h = re.sub(r"^(wnship|ownship)\b", "Township", h, flags=re.I)

    fixed = STATEWIDE.get(h.lower())
    if fixed:
        return fixed, "", party

    m = LOCAL_RE.match(h)
    if m:
        base, term, rest = m.group(1), m.group(2), m.group(3)
        office = "%s (%s Year)" % (base, term)
        # bare "Tax Collector ... <Boro/Borough>" header -> Borough Tax Collector
        if base == "Tax Collector" and re.search(r"Boro(?:ugh)?$", rest):
            office = "Borough Tax Collector (%s Year)" % term
            notes.append("header 'Tax Collector' normalized to '%s' "
                         "(%s)" % (office, rest))
        return office, expand_district(rest, notes), party

    return h, "", party


def header_of(line):
    """Return the contest header text if the line is one, else None."""
    s = line.strip()
    if "<table" in s or not s:
        return None
    s = re.sub(r"^#+\s*", "", s)
    m = re.match(r"^<div[^>]*>(.*?)</div>$", s)
    if m:
        s = m.group(1)
    s = s.strip()
    if not s or s.startswith("Summary for"):
        return None
    if VOTE_FOR_RE.search(s) and re.search(r"\(?\s*(DEM|REP)\b", s):
        return s
    return None


def cells_from_row(row_html):
    return [html.unescape(re.sub(r"<[^>]+>", "", c)).strip()
            for c in re.findall(r"<td[^>]*>(.*?)</td>", row_html, re.S)]


def first_int(cells, start):
    """First integer cell at/after `start`; None if garbled, 'absent' if none."""
    for c in cells[start:]:
        c = c.replace(",", "").strip()
        if re.fullmatch(r"\d+", c):
            return int(c)
        if c and re.search(r"\d", c):
            return None
    return "absent"


TIMES_CAST_RE = re.compile(r"(\d[\d,]*)\s*/\s*(\d[\d,]*)")

# OCR page furniture that must never join a wrapped contest header
# ("recincts Reported" — OCR drops the leading P)
HEADER_JUNK = re.compile(
    r"(recincts reported|^age: ?\d+ of \d+|^page|election summary report|"
    r"closed primary|^clarion county|^may 16, 2023|summary for|"
    r"^registered voters|^(?:ballots|gallots) cast)", re.I)


def parse_tables(text, notes, problems):
    """Single walk over the OCR text.

    Returns (rows, contests, wi_sums, recon, times_cast, bc) where rows are
    candidate rows (Write-ins aggregated separately), recon maps
    (office, district, party) -> (rows_sum, source_total_votes) for the
    totals check.
    """
    rows = []
    contests = []
    wi_sums = {}
    recon = {}
    times_cast = {}
    bc = None

    contest = None
    cand_sum = 0
    total_votes = None
    pending = []  # buffered non-table lines (wrapped contest headers)

    def start_contest(header_text):
        nonlocal contest, cand_sum, total_votes
        finish_contest()
        c = map_contest(header_text, notes)
        if not c[2]:
            problems.append("contest header without party, skipped: %s"
                            % header_text)
            contest = None
            return
        contest = c
        contests.append(c)

    def finish_contest():
        nonlocal cand_sum, total_votes
        if contest is not None:
            key = (contest[0], contest[1], contest[2])
            row_sum = cand_sum + wi_sums.get(contest, 0)
            if total_votes is None:
                problems.append("no Total Votes row: %s | %s (%s)"
                                % (contest[0], contest[1], contest[2]))
                recon[key] = None
            elif row_sum != total_votes:
                problems.append(
                    "TOTALS MISMATCH %s | %s (%s): rows %d != Total Votes %d"
                    % (contest[0], contest[1], contest[2],
                       row_sum, total_votes))
                recon[key] = (row_sum, total_votes)
            else:
                recon[key] = (row_sum, total_votes)
        cand_sum = 0
        total_votes = None

    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("====="):
            continue

        hdr = header_of(line)
        if hdr is not None:
            pending.clear()
            start_contest(hdr)
            continue

        if "<table" not in line:
            plain = re.sub(r"</?div[^>]*>", "", line).strip()
            if contest is None and bc is None:
                m = re.match(r"(?:ballots|gallots) cast:\s*([\d,]+)",
                             plain.lower())
                if m:
                    bc = int(m.group(1).replace(",", ""))
            residue = HEADER_JUNK.sub(" ", plain)
            residue = re.sub(r":?\s*\d+ of \d+ \(\s*[\d.]+%\s*\)", " ",
                             residue)
            residue = residue.strip(" :;,.")
            if residue:
                pending.append(residue)
                if len(pending) > 4:
                    del pending[0]
            continue

        # a table arrived: buffered lines may be a wrapped contest header
        if pending:
            joined = " ".join(pending)
            pending.clear()
            jh = header_of(joined)
            if jh is not None:
                start_contest(jh)
            elif re.search(r"vote", joined, re.I) \
                    and re.search(r"[a-z]{4,}", joined, re.I):
                problems.append("non-header text before table ignored: %s"
                                % joined[:120])

        for row_html in re.findall(r"<tr>(.*?)</tr>", line, re.S):
            cells = cells_from_row(row_html)
            if not cells:
                continue
            lead = cells[0].lower()
            joined = " ".join(cells).lower()
            if "imes cast" in joined or "times cast" in joined:
                m = re.search(r"(\d[\d,]*)\s*/\s*(\d[\d,]*)", joined)
                if m:
                    times_cast.setdefault(contest[2],
                                          int(m.group(1).replace(",", "")))
                continue
            if any(re.fullmatch(r"\d[\d,]*\s*/\s*\d[\d,]*", c) for c in cells):
                # OCR-merged Times Cast row: label lost, values remain
                m = re.search(r"(\d[\d,]*)\s*/\s*(\d[\d,]*)", joined)
                if m:
                    times_cast.setdefault(contest[2],
                                          int(m.group(1).replace(",", "")))
                continue
            if "candidate" in lead and "party" in joined:
                continue
            if re.search(r"total vot", lead):
                n = first_int(cells, 1)
                if isinstance(n, int):
                    total_votes = n
                else:
                    problems.append("Total Votes unreadable: %s | %s"
                                    % (contest[0], contest[1]))
                continue
            if re.search(r"resolved write|averaged waiting", lead):
                # "Unresolved Write-In" (OCR: "...nresolved"/"Resolved"/
                # "Averaged Waiting") — always 0, not part of the aggregate
                continue
            if any(WRITEIN_CELL_RE.fullmatch(c) for c in cells[1:]):
                n = first_int(cells, 2)
                if not isinstance(n, int):
                    problems.append("write-in value unreadable (%s): %s | %s"
                                    % (re.sub(r"\s+", " ", cells[0]),
                                       contest[0], contest[1]))
                else:
                    wi_sums[contest] = wi_sums.get(contest, 0) + n
                continue
            party_cell = cells[1].upper().strip(".,") if len(cells) > 1 else ""
            if party_cell in ("DEM", "REP") and len(cells) > 2:
                n = first_int(cells, 2)
                name = re.sub(r"\s+", " ", cells[0])
                if not isinstance(n, int):
                    problems.append("candidate value unreadable (%s): %s | %s"
                                    % (name, contest[0], contest[1]))
                    continue
                name = NAME_FIX.get(name.lower(), name)
                rows.append([COUNTY, contest[0], contest[1], contest[2],
                             name, n, "", "", ""])
                cand_sum += n
            elif re.search(r"\d", joined) \
                    and "precincts reported" not in joined:
                problems.append("unparsed numeric row in %s | %s: %r"
                                % (contest[0], contest[1], cells))
    finish_contest()
    return rows, contests, wi_sums, recon, times_cast, bc


def run(src, out_path):
    with open(src, encoding="utf-8") as fh:
        text = fh.read()

    notes, problems = [], []
    rows, contests, wi_sums, recon, times_cast, bc = \
        parse_tables(text, notes, problems)

    # Aggregate Write-ins row per contest that reported a write-in block,
    # carrying the contest's party (primary rule).
    out_rows = [[COUNTY, c[0], c[1], c[2], "Write-ins", wi_sums[c], "", "", ""]
                for c in wi_sums]
    out_rows.extend(rows)
    if bc is not None:
        out_rows.append([COUNTY, "Ballots Cast", "", "", "", bc, "", "", ""])

    n_mismatch = sum(1 for p in problems if p.startswith("TOTALS MISMATCH"))
    print("contests parsed: %d" % len(contests))
    print("rows: %d (incl. %d Write-ins rows, 1 Ballots Cast)"
          % (len(out_rows), len(wi_sums)))
    print("ballots cast (source header): %s" % bc)
    for p in sorted(times_cast):
        print("party %s Times Cast (ballots cast): %s" % (p, times_cast[p]))
    if problems:
        print("\nproblems (%d):" % len(problems))
        for p in problems:
            print("  - %s" % p)
    else:
        print("contest totals check: all contests reconcile")

    with open(out_path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(FIELDNAMES)
        w.writerows(out_rows)
    print("wrote %d rows -> %s" % (len(out_rows), out_path))

    if notes:
        print("\nnormalization notes:")
        for n in notes:
            print("  - %s" % n)
    return 0 if n_mismatch == 0 else 1


def main(argv):
    if len(argv) < 3:
        sys.exit("Usage: %s <input.txt|pdf> <output.csv>" % argv[0])
    src, out_path = argv[1], argv[2]
    if src.lower().endswith(".pdf"):
        tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".txt",
                                          delete=False)
        tmp.close()
        subprocess.run(["pdftotext", "-layout", src, tmp.name], check=True,
                       capture_output=True, text=True)
        with open(tmp.name, encoding="utf-8") as fh:
            extracted = fh.read()
        if "<table" not in extracted and "Total Votes" not in extracted:
            sys.exit(
                "This Clarion source is a scanned PDF with no text layer; "
                "pdftotext cannot recover it.  Run this parser on the "
                "PaddleOCR-VL extraction "
                "(work2023p/txt/Clarion__Combined_Report_2023_Primary__ocr.txt) "
                "instead.")
        src = tmp.name
    return run(src, out_path)


if __name__ == "__main__":
    sys.exit(main(sys.argv))