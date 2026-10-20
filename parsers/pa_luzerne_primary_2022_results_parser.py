#!/usr/bin/env python3
"""
Parse Luzerne County PA 2022 Primary Election precinct results.

Sources:
  * "Luzerne County Precinct Detail 2022 Primary.xls" (SOE Software
    SpreadsheetML export; per-sheet contest layout, vote-type columns
    Election Day / Provisional / Mail in / Absentee / Total read from the
    label row via the shared soe_xml_precinct helpers).
  * "Luzerne County Election Summary Report 2022 Primary.pdf" (optional
    third argument: the county's certified summary, pdftotext required).
    It tells ballot candidates apart from write-in candidates: ballot
    candidates are listed with a DEM/REP/NOP party token, while write-in
    entries -- this export lists recorded write-in candidates by name
    ("Renee Faust", "SCATTERED", "SCATTERED 2", ...) -- are tagged
    WRITE-IN.  Without the summary, only "SCATTERED*" blocks are treated
    as write-ins.

Primary-specific behavior, layered on the shared engine in
soe_xml_precinct.py (whose helpers are reused; the engine is not modified):
  * every contest title carries a party tag ("(DEM)"/"(REP)"/"(NOP)") --
    the contest's ballot party is propagated onto every candidate row,
    including write-in rows;
  * office/district canonicalization comes from the shared
    normalize_office ("U.S. Senate", State House/Senate districts from the
    title); "Member of the Democratic/Republican State Committee" is
    mapped to the anchor's "Member of Democratic State Committee" /
    "Member of Republican State Committee" with an empty district,
    matching the 2022-primary Lancaster/Allegheny precedent;
  * named write-in blocks are kept as rows of their own with the contest
    party (Allegheny 2022 precedent); "SCATTERED*" blocks (possibly one
    per vote-for slot) are summed into a single "Write Ins" row per
    precinct, party included;
  * "(NOP)" question contests (Pittston charter amendments, Wright Twp
    proposition) keep party empty ("Yes"/"No" rows), matching the
    general-election convention.

Usage:
  python pa_luzerne_primary_2022_results_parser.py <input.xls> <output.csv> \
      [summary.pdf-or-.txt]
"""

import csv
import re
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from soe_xml_precinct import (  # noqa: E402
    FIELDNAMES,
    VOTE_GROUP_LABELS,
    SovcXmlConfig,
    _rows,
    is_summary_row,
    normalize_candidate,
    normalize_office,
)

COUNTY = "Luzerne"

CONFIG = SovcXmlConfig(county=COUNTY)

STATE_COMMITTEE_OFFICES = {
    "member of the democratic state committee": "Member of Democratic State Committee",
    "member of the republican state committee": "Member of Republican State Committee",
    "member of democratic state committee": "Member of Democratic State Committee",
    "member of republican state committee": "Member of Republican State Committee",
}

VOTE_IDX = {"votes": 0, "election_day": 1, "mail": 2, "provisional": 3}

SS = "{urn:schemas-microsoft-com:office:spreadsheet}"
XML_NS = {"s": "urn:schemas-microsoft-com:office:spreadsheet"}

PAGE_FOOTER = re.compile(r"^Page: \d+ of \d+")
BARE_PARTY_LINE = re.compile(r"^\s*(?:DEM|REP|NOP)\s*$")
PRECINCTS_REPORTED = re.compile(r"^\s*Precincts Reported:")


def alnum_key(name):
    """Case/punctuation/spacing-insensitive key for name matching."""
    return re.sub(r"[^a-z0-9]+", "", str(name).lower())


def contest_party(title):
    """Ballot party from the contest title's party tag."""
    m = re.search(r"\((DEM|REP)\)", str(title), re.I)
    if m:
        return m.group(1).upper()
    # "(NOP)" or no tag: no ballot party
    return ""


def strip_tags(title):
    """Remove the trailing party tag and (Vote For n) suffix from a title."""
    t = re.sub(r"\s+", " ", str(title)).strip()
    while True:
        n = re.sub(r"\(\s*Vote\s*[Ff]or\s*\d+\s*\)\s*$", "", t).strip()
        n = re.sub(r"\((?:DEM|REP|NOP)\)\s*$", "", n, flags=re.I).strip()
        n = re.sub(r"\s+", " ", n).strip()
        if n == t:
            return t
        t = n


def contest_office(title):
    """Contest title -> (office, district)."""
    party = contest_party(title)
    # nonpartisan questions: keep the title as printed ("City of ...",
    # not the shared normalize_office's "City Of")
    if re.search(r"\(NOP\)", str(title), re.I):
        return strip_tags(title), ""
    office, district, _vote_for = normalize_office(strip_tags(title), CONFIG)
    key = office.lower()
    if key in STATE_COMMITTEE_OFFICES:
        return STATE_COMMITTEE_OFFICES[key], ""
    return office, district


# --------------------------------------------------------------------------
# Summary-report (PDF) parsing: contest titles -> ballot candidate names
# --------------------------------------------------------------------------

BALLOT_ROW = re.compile(
    r"^(?P<name>\S.*?)\s{2,}(?P<pty>DEM|REP|NOP)\s+"
    r"(?P<n1>[\d,]+)\s+(?P<n2>[\d,]+)\s+(?P<n3>[\d,]+)\s+(?P<n4>[\d,]+)\s*$"
)
BALLOT_ROW_NAMELESS = re.compile(
    r"^\s*(?P<pty>DEM|REP|NOP)\s+(?P<n1>[\d,]+)\s+(?P<n2>[\d,]+)\s+"
    r"(?P<n3>[\d,]+)\s+(?P<n4>[\d,]+)\s*$"
)
BARE_NUMBER = re.compile(r"^\s*[\d,]+\s*$")


def _txt_from_pdf(pdf_or_txt):
    if str(pdf_or_txt).lower().endswith(".pdf"):
        tmp = tempfile.NamedTemporaryFile(suffix=".txt", delete=False)
        tmp.close()
        subprocess.run(["pdftotext", "-layout", str(pdf_or_txt), tmp.name],
                       check=True, capture_output=True)
        return tmp.name
    return str(pdf_or_txt)


def _contest_title_blocks(lines):
    """Contest title blocks of the summary report -> [(start_idx, title)].

    A contest title starts at any line carrying a "(DEM)"/"(REP)"/"(NOP)"
    tag (candidate/detail/label rows never do) and runs until the
    accumulated title ends with ")", since the printed titles wrap
    ("...(Vote for" / "for 1)").
    """
    titles = []
    i, n = 0, len(lines)
    while i < n:
        s = lines[i].strip()
        if (s and not PAGE_FOOTER.match(s)
                and not s.lower().startswith(("candidate", "times cast",
                                              "total votes", "write-in",
                                              "precincts reported"))
                and re.search(r"\((?:DEM|REP|NOP)\)", s, re.I)):
            # a title whose first printed line carries the tag ("Ward 20
            # (DEM) (Vote"); when the tag is only on the second printed
            # line, the office name sits on the line(s) above
            k = i - 1
            head = []
            while k >= 0:
                t = lines[k].strip()
                if (not t or PAGE_FOOTER.match(t) or BARE_PARTY_LINE.match(t)
                        or t.lower().startswith(
                            ("candidate", "times cast", "total votes",
                             "write-in", "precincts reported", "scattered"))
                        or "write-in" in t.lower()
                        or "election day" in t.lower()
                        or "mail-in" in t.lower()):
                    break
                head.append(t)
                k -= 1
            parts = list(reversed(head)) + [s]
            j = i + 1
            while j < n and not parts[-1].endswith(")"):
                t = lines[j].strip()
                if t and not PAGE_FOOTER.match(t):
                    parts.append(t)
                j += 1
            titles.append((k + 1, " ".join(parts)))
            i = j
        else:
            i += 1
    return titles


def _candidate_contests(lines, titles):
    """Map each 'candidate row' line index to (ckey, title) of the last
    contest title starting at or before it."""
    bounds = [start for start, _t in titles] + [len(lines)]
    def which(i):
        for j in range(len(bounds) - 1):
            if bounds[j] <= i < bounds[j + 1]:
                return titles[j][1]
        return None
    return which


def summary_key(title):
    """Composite lookup key for a contest title: office part + party tag."""
    return alnum_key(strip_tags(title)) + "|" + contest_party(title).lower()


def parse_summary(path):
    """Summary report -> {contest key: set(alnum ballot candidate names)}."""
    with open(_txt_from_pdf(path), encoding="utf-8", errors="replace") as fh:
        lines = fh.read().splitlines()
    titles = _contest_title_blocks(lines)
    if not titles:
        return {}
    which = _candidate_contests(lines, titles)
    ballots = {summary_key(t): set() for _start, t in titles}
    for i, line in enumerate(lines):
        if which(i) is None:
            continue
        m = BALLOT_ROW.match(line)
        if m:
            name = m.group("name")
        elif BALLOT_ROW_NAMELESS.match(line):
            # wrapped long name: party + numbers line; the name sits on the
            # line above and may continue on the line(s) below
            parts = []
            j = i - 1
            s = lines[j].strip() if j >= 0 else ""
            if s and not BARE_NUMBER.match(s) and not BARE_PARTY_LINE.match(s):
                parts.append(s)
            j = i + 1
            # the wrapped name tail is a bare word line (no digits, no
            # labels); anything with digits belongs to the next table row
            while j < len(lines):
                s = lines[j].strip()
                if not s or re.search(r"\d", s) or BARE_PARTY_LINE.match(s) \
                        or BALLOT_ROW.search(s) or BALLOT_ROW_NAMELESS.search(s):
                    break
                parts.append(s)
                j += 1
            name = " ".join(parts)
        else:
            continue
        name = re.sub(r"-$", "", name.strip()).strip()
        ckey = summary_key(which(i))
        if name and ckey:
            ballots.setdefault(ckey, set()).add(alnum_key(name))
    return ballots


# --------------------------------------------------------------------------
# Main parse
# --------------------------------------------------------------------------

def parse(path, summary_path=None):
    summary = parse_summary(summary_path) if summary_path else {}
    tree = ET.parse(str(path))
    sheets = {s.get(SS + "Name"): s
              for s in tree.getroot().findall(".//s:Worksheet", XML_NS)}
    rows = []
    unmatched_writein = []  # QA: named blocks not present in the summary

    # ---- Registered Voters sheet -------------------------------------
    # columns: Precinct | Registered Voters | Ballots Cast | Voter Turnout
    rv_grid = list(_rows(sheets.get("Registered Voters", ET.Element("x"))))
    rv_col, bc_col = 1, None
    if rv_grid:
        for ci, v in enumerate(rv_grid[0]):
            lab = str(v).strip().lower()
            if lab == "registered voters":
                rv_col = ci
            elif lab == "ballots cast":
                bc_col = ci
    registered = {}
    ballots_cast = {}
    for row in rv_grid[1:]:
        name = str(row[0]).strip() if row and row[0] else ""
        if not name or name == "None" or is_summary_row(name):
            continue
        for store, ci in ((registered, rv_col), (ballots_cast, bc_col)):
            if ci is not None and ci < len(row) and str(row[ci]).strip().isdigit():
                store[name] = int(str(row[ci]).strip())

    for precinct, count in sorted(registered.items()):
        rows.append({"county": COUNTY, "precinct": precinct,
                     "office": "Registered Voters", "district": "",
                     "party": "", "candidate": "", "votes": count,
                     "election_day": "", "mail": "", "provisional": ""})
    for precinct, count in sorted(ballots_cast.items()):
        rows.append({"county": COUNTY, "precinct": precinct,
                     "office": "Ballots Cast", "district": "",
                     "party": "", "candidate": "", "votes": count,
                     "election_day": "", "mail": "", "provisional": ""})

    # ---- Contest sheets -----------------------------------------------
    for sheet_name, sheet in sorted(
        ((n, s) for n, s in sheets.items()
         if n not in ("Table of Contents", "Registered Voters")),
        key=lambda kv: int(kv[0]) if kv[0].isdigit() else 10 ** 9,
    ):
        grid = list(_rows(sheet))
        if len(grid) < 3:
            continue
        title = str(grid[0][0]).strip() if grid[0] else ""
        if not title or title == "None":
            continue
        office, district = contest_office(title)
        party = contest_party(title)

        # candidate blocks from row 2, column labels from row 3
        blocks = []  # (start_col, candidate, party, is_scatter)
        for ci, v in enumerate(grid[1]):
            if v and str(v).strip() not in ("", "None"):
                cand, cparty = normalize_candidate(v, CONFIG)
                if not cand:
                    continue
                w = bool(re.search(r"write|scatter", v, re.I))
                blocks.append((ci, cand, cparty, w))

        col_group = {}   # col -> "election_day" | "mail" | "provisional" | "votes"
        col_cand = {}    # col -> (block_start_col, candidate, party)
        cur = None
        labels = grid[2]
        for ci in range(len(labels)):
            lab = (str(labels[ci]).strip().lower()
                   if ci < len(labels) and labels[ci] is not None else "")
            for bi, cand, cparty, w in blocks:
                if bi == ci:
                    cur = (bi, cand, cparty)
            if lab in VOTE_GROUP_LABELS and cur:
                col_group[ci] = VOTE_GROUP_LABELS[lab]
                col_cand[ci] = cur
            elif lab in ("total votes", "total votes cast"):
                if cur:
                    col_group[ci] = "votes"
                    col_cand[ci] = cur
            elif lab and lab not in ("precinct", "registered voters",
                                     "registered voters - total", "precinct name"):
                cur = None  # final all-candidates "Total" column

        if not col_group:
            continue

        # write-in classification per block
        scatter_block = {}
        kind = {}  # block start col -> "ballot" | "writein"
        summary_ballots = summary.get(summary_key(title))
        for bi, cand, _cparty, w in blocks:
            scatter_block[bi] = w
            if w or (cand or "").lower().startswith("scatter"):
                kind[bi] = "writein"
            elif summary_ballots is None or not party:
                kind[bi] = "ballot"  # no summary / nonpartisan question
            elif alnum_key(cand) in summary_ballots:
                kind[bi] = "ballot"
            else:
                kind[bi] = "writein"
                unmatched_writein.append((office, party, cand))

        # walk data rows
        for row in grid[3:]:
            if not row:
                continue
            name = str(row[0]).strip() if row[0] is not None else ""
            if is_summary_row(name) or not name or name == "None":
                continue
            agg = {}            # (candidate, party) -> [votes, ed, mail, prov]
            writein_keys = set()
            for ci, g in col_group.items():
                bi, cand, cparty = col_cand[ci]
                wkey = kind.get(bi) == "writein"
                if wkey:
                    writein_keys.add((cand, party))
                    if scatter_block.get(bi):
                        cand = "Write Ins"
                        # a scatter block's key is ('Scattered', ...) after
                        # title-casing; track the renamed key too so the
                        # zero-vote skip below matches
                        writein_keys.add(("Write Ins", party))
                    cparty = party
                elif not cparty:
                    cparty = party
                v = 0
                if ci < len(row) and row[ci] is not None:
                    s = str(row[ci]).strip().replace(",", "")
                    if s.isdigit():
                        v = int(s)
                # += : scattered blocks share the "Write Ins" key and must sum
                d = agg.setdefault((cand, cparty), [0, 0, 0, 0])
                if g == "votes":
                    d[0] += v
                else:
                    d[VOTE_IDX[g]] += v
            for (cand, cparty), (v, ed, mi, pr) in sorted(agg.items()):
                if (cand, cparty) in writein_keys and v == 0 and ed == 0 \
                        and mi == 0 and pr == 0:
                    continue  # drop zero write-in rows, keep zero ballot rows
                rows.append({
                    "county": COUNTY, "precinct": name,
                    "office": office, "district": district,
                    "party": cparty, "candidate": cand,
                    "votes": v, "election_day": ed,
                    "mail": mi, "provisional": pr,
                })
    return rows, unmatched_writein


if __name__ == "__main__":
    if len(sys.argv) not in (3, 4):
        sys.exit("Usage: pa_luzerne_primary_2022_results_parser.py "
                 "<input.xls> <output.csv> [summary.pdf|.txt]")
    rows, unmatched = parse(
        sys.argv[1], sys.argv[3] if len(sys.argv) == 4 else None)
    with open(sys.argv[2], "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDNAMES)
        w.writeheader()
        w.writerows(rows)
    print(f"Wrote {len(rows)} rows to {sys.argv[2]}")
    print(f"named blocks classified as write-ins: not in summary list "
          f"(should be 0): {len(unmatched)}")
    for office, _party, cand in unmatched[:20]:
        print(f"  {office!r}: {cand!r}")