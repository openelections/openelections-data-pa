#!/usr/bin/env python3
"""Lawrence County, PA — May 16, 2023 Municipal Primary results.

Two Electionware sources, auto-detected from the file header line:

- County summary ("Summary Results Report" with the STATISTICS block and
  per-contest "Total Votes Cast"): parsed through the shared Electionware
  ESR engine ``pa_2023_summary_common.parse_esr``.  The primary contest
  headers carry the party ("DEM JUSTICE OF THE SUPREME COURT"), which
  ``parse_esr``'s STATISTICS-block scanner would swallow (its break regex
  expects the office word first), so a preprocessor rewrites each header
  to "<office> ~<PARTY>" and ``map_contest`` turns that into
  (office, district, party).  The party marker stays in the office slot
  until after the parse so the per-contest totals reconciliation can tell
  the DEM and REP sections of the same office apart; the marker is then
  stripped and the party written to the party column of each candidate
  row (Write-ins rows keep party empty, per the primary convention).
  Per-contest "Precincts Reporting 75 of 75" furniture lines are dropped
  (the engine would otherwise emit them as a bogus "Precincts" row).
- Precinct summary ("Summary Results Report ... PRECINCT SUMMARY", 948
  pages, 75 precincts): parsed with the same pdftotext engine as the
  county's 2023 general parser (``electionware_txt``), after a
  preprocessor injects each contest's party code onto its candidate and
  aggregate rows (primary candidate rows carry no party token; the party
  lives on the "DEM "/"REP " header).  Write-ins/Overvotes/Undervotes
  rows keep the contest party in the precinct file so the DEM and REP
  sections of the same office stay distinct, as in the county's 2020
  primary file.

Office/district mapping mirrors ``pa_lawrence_general_2023_results_parser.py``
and the county's 2023 general file (short municipality districts, term
suffixes in the office name, "MAYOR NEW CASTLE" kept as office
"Mayor New Castle", ALL-CAPS candidate names, Blackhawk Area school
district spelling).  Differences forced by the primary source: Township
Supervisor headers print no term length, so those offices stay
"Township Supervisor" without a "(N Year)" suffix, and there are no
retention questions or ballot questions in a municipal primary.

Usage:
    python parsers/pa_lawrence_primary_2023_results_parser.py \
        <input.txt|input.pdf> <output.csv>
"""

import csv
import os
import re
import subprocess
import sys
import tempfile
from collections import defaultdict
from pathlib import Path

from electionware_precinct_np import title_case
from electionware_txt import FIELDNAMES as PRECINCT_FIELDNAMES
from electionware_txt import TxtConfig, parse_txt
from pa_2023_summary_common import Rows, parse_esr, text_from_pdf

COUNTY_FIELDNAMES = ["county", "office", "district", "party", "candidate",
                     "votes", "election_day", "mail", "provisional"]

PROBLEMS = []

# ---------------------------------------------------------------------------
# Office / district mapping (mirrors pa_lawrence_general_2023_results_parser).
# ---------------------------------------------------------------------------

EXACT_OFFICES = {
    "JUSTICE OF THE SUPREME COURT": ("Justice of the Supreme Court", ""),
    "JUDGE OF THE SUPERIOR COURT": ("Judge of the Superior Court", ""),
    "JUDGE OF THE COMMONWEALTH COURT": ("Judge of the Commonwealth Court", ""),
    "COUNTY COMMISSIONER": ("County Commissioner", ""),
    "COUNTY CONTROLLER": ("County Controller", ""),
    "COUNTY CORONER": ("Coroner", ""),
    "COUNTY TREASURER": ("Treasurer", ""),
    "PROTHONOTARY AND CLERK OF COURTS": ("Prothonotary and Clerk of Courts", ""),
    # "MAYOR NEW CASTLE" has no TOWNSHIP/BOROUGH/CITY kind token; the
    # county's 2023 general file keeps it as office "Mayor New Castle"
    # with an empty district.
    "MAYOR NEW CASTLE": ("Mayor New Castle", ""),
}

# "MAGISTERIAL DISTRICT JUDGE DISTRICT 53-3-1"
MDJ_RE = re.compile(r"^MAGISTERIAL DISTRICT JUDGE DISTRICT\s+(\d[\d-]*)\s*$")

# Kind-first local offices:
#   "TOWNSHIP AUDITOR VOTE 1 TERM 6 HICKORY"
#   "BOROUGH COUNCIL VOTE 3 TERM4 WAMPUM"     (no space after TERM)
#   "BOROUGH TAX COLLECTER ENON VALLEY"       (source typo COLLECTER)
#   "CITY COUNCIL VOTE 1 TERM2 NEW CASTLE"
LOCAL_RE = re.compile(
    r"^(TOWNSHIP|BOROUGH|CITY)\s+"
    r"(SUPERVISOR|AUDITOR|TAX COLLECTER|TAX COLLECTOR|COUNCIL|MAYOR)"
    r"(?:\s+VOTE\s*(\d+))?(?:\s+TERM\s*(\d+))?\s+([A-Z0-9 .]+?)\s*$")

LOCAL_OFFICES = {
    ("TOWNSHIP", "SUPERVISOR"): "Township Supervisor",
    ("TOWNSHIP", "AUDITOR"): "Township Auditor",
    ("TOWNSHIP", "TAX COLLECTER"): "Tax Collector",
    ("TOWNSHIP", "TAX COLLECTOR"): "Tax Collector",
    ("BOROUGH", "COUNCIL"): "Borough Council",
    ("BOROUGH", "AUDITOR"): "Borough Auditor",
    ("BOROUGH", "MAYOR"): "Mayor",
    ("BOROUGH", "TAX COLLECTER"): "Tax Collector",
    ("BOROUGH", "TAX COLLECTOR"): "Tax Collector",
    ("CITY", "COUNCIL"): "City Council",
    ("CITY", "MAYOR"): "Mayor",
}

# "SCHOOL DIRECTOR <DISTRICT>" with optional VOTE N / TERM N infixes.
SCHOOL_RE = re.compile(
    r"^SCHOOL DIRECTOR(?:\s+VOTE\s*\d+)?(?:\s+TERM\s*(\d+))?\s+([A-Z0-9 .]+?)\s*$")
# Trailing "REGION N" (Blackhawk): the region moves into the office name.
SCHOOL_REGION_RE = re.compile(r"^(.+?)\s+REGION\s+(\d+)$")


def _with_term(office: str, years) -> str:
    return f"{office} ({years} Year)" if years else office


def normalize_office(line: str):
    """Map an all-caps contest header (party prefix already stripped by the
    caller when present) to (office, district), matching the county's 2023
    general file conventions."""
    line = re.sub(r"\s+", " ", line).strip()
    # The primary source spells the Blackhawk district "BLACK HAWK AREA";
    # the general/2025 files use "BLACKHAWK AREA".
    line = line.replace("BLACK HAWK AREA", "BLACKHAWK AREA")

    if line in EXACT_OFFICES:
        return EXACT_OFFICES[line]

    m = MDJ_RE.match(line)
    if m:
        return ("Magisterial District Judge", m.group(1))

    m = LOCAL_RE.match(line)
    if m:
        kind, office_tok, _vote, years, muni = m.groups()
        office = LOCAL_OFFICES[(kind, office_tok)]
        return (_with_term(office, years), title_case(muni.strip()))

    m = SCHOOL_RE.match(line)
    if m:
        years, rest = m.groups()
        rest = rest.strip()
        office = "School Director"
        district = title_case(rest)
        sm = SCHOOL_REGION_RE.match(rest)
        if sm:
            district = title_case(sm.group(1).strip())
            office += f" Region {sm.group(2)}"
        return (_with_term(office, years), district)

    PROBLEMS.append(f"unmapped contest header: {line!r}")
    return (line.title(), "")


def _split_party_header(header: str):
    """("DEM", "JUSTICE OF THE SUPREME COURT") from "DEM JUSTICE ..."."""
    m = re.match(r"^(DEM|REP)\s+(.+)$", header.strip())
    if m:
        return m.group(1), m.group(2).strip()
    return "", header.strip()


# ---------------------------------------------------------------------------
# Mode 1: county summary -> parse_esr.
# ---------------------------------------------------------------------------

VOTE_FOR_LINE = re.compile(r"^vote for \d+$", re.IGNORECASE)
PRECINCTS_REPORTING = re.compile(r"^Precincts Reporting\s+\d+", re.IGNORECASE)
MARKER = re.compile(r"~(DEM|REP)\s*$")


def preprocess_summary(text: str) -> str:
    """Strip the DEM/REP contest-header prefixes into a trailing ~PARTY
    marker and drop the per-contest "Precincts Reporting" furniture."""
    lines = text.split("\n")
    out = list(lines)
    prev_nonblank = None
    for i, raw in enumerate(lines):
        s = raw.strip()
        if not s:
            continue
        if PRECINCTS_REPORTING.match(s):
            out[i] = ""
            continue
        if VOTE_FOR_LINE.match(s):
            if prev_nonblank is not None:
                header = out[prev_nonblank].strip()
                party, rest = _split_party_header(header)
                if party:
                    out[prev_nonblank] = rest + " ~" + party
                else:
                    PROBLEMS.append(f"contest header without party: {header!r}")
        prev_nonblank = i
    return "\n".join(out)


def map_contest(header: str):
    """(office~PARTY, district, party) for a preprocessed contest header."""
    h = " ".join(header.split())
    m = re.search(r"~(DEM|REP)\s*$", h)
    party = ""
    if m:
        party = m.group(1)
        h = h[: m.start()].strip()
    office, district = normalize_office(h)
    if not party:
        return (office, district, "")
    return (f"{office}~{party}", district, party)


# Candidates the shared CANON_NAMES table would rewrite to mixed case;
# Lawrence's 2023 general file keeps ALL-CAPS candidate names, so restore
# the source spelling.
CANON_RESTORE = {
    "Carolyn Carluccio": "CAROLYN CARLUCCIO",
    "Jill Beck": "JILL BECK",
    "Timika Lane": "TIMIKA LANE",
    "Matt Wolf": "MATT WOLF",
    "Megan Martin": "MEGAN MARTIN",
    "Maria Battista": "MARIA BATTISTA",
    "Harry F. Smail Jr.": "HARRY F SMAIL JR",
}


def postprocess_summary_rows(R: Rows):
    """Strip the ~PARTY office marker; carry the party onto every row,
    including Write-ins — DEM and REP sections of the same office exist
    side by side in a primary, so a party-less Write-ins row would key
    identically across the two sections and fail the duplicate_entries
    data test (the other 2023-primary county files carry the party on
    their county-level Write-ins rows the same way)."""
    out = []
    for county, office, dist, party, cand, votes, ed, mi, pr in R.rows:
        if "~" in office:
            office, p = office.rsplit("~", 1)
            party = p
        cand = CANON_RESTORE.get(cand, cand)
        out.append([county, office, dist, party, cand, votes, ed, mi, pr])
    return out


def check_totals(R: Rows):
    """Per-contest reconciliation.  R.totals keys carry the ~PARTY marker,
    so the DEM and REP sections of the same office reconcile separately."""
    agg = defaultdict(int)
    for r in R.rows:
        key = (r[1], r[2])
        if r[4] in ("Overvotes", "Undervotes"):
            continue  # "Total Votes Cast" excludes over/under
        try:
            agg[key] += int(r[5])
        except (TypeError, ValueError):
            pass
    bad = []
    for key, tot in R.totals.items():
        if tot is None:
            continue
        k2 = (key[0], key[1])
        if agg.get(k2, 0) != tot:
            bad.append((key, agg.get(k2, 0), tot))
    return bad


def write_county_csv(rows, path: str) -> int:
    d = os.path.dirname(os.path.abspath(path))
    if d:
        os.makedirs(d, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(COUNTY_FIELDNAMES)
        w.writerows(rows)
    return len(rows)


def run_county(src: str, dst: str) -> int:
    if src.lower().endswith(".pdf"):
        src = text_from_pdf(src)
    with open(src, encoding="utf-8") as fh:
        text = fh.read()
    tf = tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False)
    tf.write(preprocess_summary(text))
    tf.close()
    try:
        R = parse_esr(tf.name, "Lawrence", map_contest)
    finally:
        os.unlink(tf.name)
    rows = postprocess_summary_rows(R)
    n = write_county_csv(rows, dst)
    print(f"wrote {n} rows -> {dst}")
    print(f"contests parsed: {len(R.contests)}")
    bad = check_totals(R)
    if bad:
        for (office, dist, _party), a, t in bad:
            print(f"WARNING {office} | {dist}: rows sum {a} != "
                  f"Total Votes Cast {t}")
    else:
        print("contest totals check: all contests reconcile")
    for p in PROBLEMS:
        print("PROBLEM:", p)
    return n


# ---------------------------------------------------------------------------
# Mode 2: precinct report -> electionware_txt engine.
# ---------------------------------------------------------------------------

SECTION_END = re.compile(r"^(contest totals|total votes cast)\b", re.IGNORECASE)
# Rows that must NOT carry the party token (the engine skips them, or the
# DEM/REP sections must not get aggregate rows for them):
# "Write-In: NAME" details, "Not Assigned", "No Candidate Filed".
SKIP_HEAD = re.compile(
    r"^(write-in:|not assigned|no candidate filed)", re.IGNORECASE)
PAGE_FURNITURE = re.compile(
    r"(summary results report|report generated|page \d+ of|precinct summary -"
    r"|2023 primary|official results|^may 16, 2023)", re.IGNORECASE)
TRAILING_INT = re.compile(r"\s\d[\d,]*\s*$")


def preprocess_precinct(text: str) -> str:
    """Inject each contest's header party onto its candidate/aggregate rows.

    Candidate rows in a primary carry no party token; the party lives on
    the "DEM "/"REP " office-header line.  The engine derives each row's
    party from a leading party token, so the preprocessor prefixes every
    data row inside a contest section with the section's party — including
    the "Write-In Totals" / "Overvotes" / "Undervotes" aggregate rows: the
    engine emits those with an empty party unless the label itself carries
    the token, and the DEM and REP sections of the same office would
    otherwise produce duplicate (office, party="", candidate) keys (the
    county's 2020 primary file carries party on its Write-ins rows).
    "Write-In: NAME" detail rows, "Not Assigned" and "Contest Totals" are
    left alone so the engine skips them as in the general.
    """
    lines = text.split("\n")
    out = []
    in_contest = False
    party = ""
    prev_nonblank = -1
    for i, raw in enumerate(lines):
        s = raw.strip()
        if not s:
            out.append(raw)
            continue
        if VOTE_FOR_LINE.match(s):
            in_contest = True
            party = ""
            if prev_nonblank >= 0:
                hdr_party, _rest = _split_party_header(lines[prev_nonblank])
                if hdr_party:
                    party = hdr_party
                else:
                    PROBLEMS.append(
                        f"section header without party: {lines[prev_nonblank]!r}")
            out.append(raw)
            continue
        if SECTION_END.match(s):
            in_contest = False
            party = ""
            out.append(raw)
            prev_nonblank = -1
            continue
        prev_nonblank = i
        if not in_contest:
            out.append(raw)
            continue
        if PAGE_FURNITURE.search(s):
            out.append(raw)
            continue
        if SKIP_HEAD.match(s) or not TRAILING_INT.search(s):
            out.append(raw)
            continue
        if party:
            out.append(party + " " + s)
        else:
            PROBLEMS.append(f"candidate row with no party: {s!r}")
            out.append(raw)
    return "\n".join(out)


def fix_precinct(name: str) -> str:
    """The primary source prints "New Castle1-2" (missing space); the
    county's general/2025 files use "New Castle 1-2"."""
    if name == "New Castle1-2":
        return "New Castle 1-2"
    return name


def normalize_office_primary(line: str):
    party, rest = _split_party_header(line)
    return normalize_office(rest)


CONFIG = TxtConfig(
    county="Lawrence",
    normalize_office=normalize_office_primary,
    prettify_precinct=fix_precinct,
)


def pdf_to_text(pdf_path: str) -> str:
    tf = tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False)
    tf.close()
    subprocess.run(["pdftotext", "-layout", pdf_path, tf.name],
                   check=True, capture_output=True, text=True)
    return tf.name


def run_precinct(src: str, dst: str) -> int:
    if src.lower().endswith(".pdf"):
        src = pdf_to_text(src)
    with open(src, encoding="utf-8") as fh:
        text = fh.read()
    tf = tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False)
    tf.write(preprocess_precinct(text))
    tf.close()
    try:
        rows, precinct_count, warnings, unnamed = parse_txt(
            Path(tf.name), CONFIG)
    finally:
        os.unlink(tf.name)
    # The injected party token turns "Write-In Totals" into a party-tagged
    # row; re-label it to the "Write-ins" convention (party stays DEM/REP so
    # the DEM and REP sections of the same office stay distinct, as in the
    # county's 2020 primary file).
    for r in rows:
        if r["candidate"] == "Write-In Totals":
            r["candidate"] = "Write-ins"
    d = os.path.dirname(os.path.abspath(dst))
    if d:
        os.makedirs(d, exist_ok=True)
    with open(dst, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(PRECINCT_FIELDNAMES)
        for r in rows:
            w.writerow([r[c] for c in PRECINCT_FIELDNAMES])
    print(f"wrote {len(rows)} rows across {precinct_count} precincts -> {dst}")
    if unnamed:
        print(f"skipped {unnamed} unnamed Statistics segment(s)")
    print(f"{len(warnings)} engine warnings")
    for w in warnings[:20]:
        print("WARN:", w)
    for p in PROBLEMS:
        print("PROBLEM:", p)
    return len(rows)


# ---------------------------------------------------------------------------

def main(argv):
    if len(argv) != 3:
        sys.exit(f"Usage: {argv[0]} <input.txt|input.pdf> <output.csv>")
    src, dst = argv[1], argv[2]
    if src.lower().endswith(".pdf"):
        head = subprocess.run(["pdftotext", "-layout", src, "-"], check=True,
                              capture_output=True, text=True).stdout[:2000]
    else:
        with open(src, encoding="utf-8") as fh:
            head = fh.read(2000)
    PROBLEMS.clear()
    if "PRECINCT SUMMARY" in head:
        run_precinct(src, dst)
    else:
        run_county(src, dst)


if __name__ == "__main__":
    main(sys.argv)