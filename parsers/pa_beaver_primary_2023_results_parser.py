#!/usr/bin/env python3
"""Parser for Beaver County, PA 2023 Municipal Primary (May 16, 2023).

Sources are Electionware text extracts:
- "Election Summary Results Report" (county summary)  -> county-level CSV
- "Precinct Summary Results Report" (per-precinct)    -> precinct CSV

The parser auto-detects which report the input file is (by its title line)
and writes the matching 9-column/10-column OpenElections CSV.

Primary-vs-general differences handled here:

* The party is printed on the CONTEST HEADER ("DEM JUSTICE OF THE SUPREME
  COURT" / "REP COUNTY COMMISSIONER"), not on the candidate rows.  The
  text is preprocessed: the DEM/REP prefix is stripped from each contest
  header and the header's party token is prefixed to that contest's named
  candidate rows, so the shared engines' row-level party handling
  (PARTY_RE) assigns each candidate its contest party.  Aggregate rows
  ("Write-In Totals", "Not Assigned", "Write-In: NAME", turnout) are never
  prefixed and keep an empty party, per repo convention.
* Per-party statistics rows ("Registered Voters - DEMOCRATIC", "Ballots
  Cast - DEMOCRATIC" ...) are skipped (not recorded, per repo convention);
  in the precinct report their values are printed on the line ABOVE the
  label, so both lines are dropped together.
* The per-contest footer "Precincts Reporting N of N" is dropped.
* School-district contests spanning into Lawrence County (Blackhawk SD,
  Ellwood City SD) carry two extra columns ("Lawrence County Votes",
  "Total Votes"); only the Beaver County TOTAL / Election Day / Mail /
  Provisional columns are recorded.
* Some municipal auditor/council seats exist as several term-length
  contests that the source labels with the SAME header text (e.g. three
  "DEM AUDITOR DARLINGTON BORO" sections, no "N YR" in the header).  All
  of them are write-in-only; same-key rows are summed into one row per
  (office, district, party, candidate) so no duplicate keys are written
  (same convention as electionware_txt_2023).
* Header typos fixed to the county's 2023 general spellings: "HOPEWELL
  SCHOOL DISTRICTION" -> "... DISTRICT", "RIVERSIDE SCHOOL DISTRCT" ->
  "... DISTRICT".

Office/district conventions mirror the county's 2023 general parser
(pa_beaver_general_2023_results_parser.py): office gets "(N Year)" when
the header carries a term; municipalities are expanded (TWP -> Township,
BORO -> Borough); "MEMBER OF COUNCIL" -> "Borough Council"; candidates
keep their printed ALL-CAPS names.

Usage:
    python parsers/pa_beaver_primary_2023_results_parser.py \
        "<input.txt|input.pdf>" "<output.csv>"
"""

import csv
import os
import re
import sys
import tempfile

from pa_2023_summary_common import (parse_esr, text_from_pdf,
                                    FIELDNAMES, write_csv as write_rows_csv)
from electionware_txt import TxtConfig, parse_input, FIELDNAMES as P_FIELDNAMES
from electionware_precinct_np import (expand_muni_flexible, prettify_all_caps_precinct,
                                      title_case)

COUNTY = "Beaver"

# ---------------------------------------------------------------------------
# Office normalization (mirrors the 2023 general parser).
# ---------------------------------------------------------------------------

EXACT_OFFICES = {
    "JUSTICE OF THE SUPREME COURT": ("Justice of the Supreme Court", ""),
    "JUDGE OF THE SUPERIOR COURT": ("Judge of the Superior Court", ""),
    "JUDGE OF THE COMMONWEALTH COURT": ("Judge of the Commonwealth Court", ""),
    "COUNTY COMMISSIONER": ("County Commissioner", ""),
    "COUNTY CONTROLLER": ("County Controller", ""),
    "COUNTY TREASURER": ("County Treasurer", ""),
    "CLERK OF COURTS": ("Clerk of Courts", ""),
    "CORONER": ("Coroner", ""),
    "DISTRICT ATTORNEY": ("District Attorney", ""),
    "PROTHONOTARY": ("Prothonotary", ""),
    "RECORDER OF DEEDS": ("Recorder of Deeds", ""),
    "REGISTER OF WILLS": ("Register of Wills", ""),
    "SHERIFF": ("Sheriff", ""),
    "CONTROLLER": ("Controller", ""),
}

MDJ_RE = re.compile(r"^MAGISTERIAL DISTRICT JUDGE\s+(\d[\d-]*)\s*$")

# Local offices, longest base first.  The 2023 primary headers carry the
# term ("MEMBER OF COUNCIL - 2 YR ALIQUIPPA") only where the general did;
# several offices appear without a term ("AUDITOR BRIGHTON", "SUPERVISOR
# CENTER", "MEMBER OF COUNCIL AMBRIDGE").
LOCAL_BASES = (
    "MEMBER OF COUNCIL",
    "TOWNSHIP COMMISSIONER",
    "TOWNSHIP SUPERVISOR",
    "CITY CONTROLLER",
    "CITY TREASURER",
    "SCHOOL DIRECTOR",
    "TAX COLLECTOR",
    "SUPERVISOR",
    "AUDITOR",
    "MAYOR",
)

LOCAL_RE = re.compile(
    r"^(" + "|".join(re.escape(b) for b in LOCAL_BASES) +
    r")\s*(?:-\s*(\d)\s*YR(?:\s+TERM)?)?\s+(.+)$"
)

LOCAL_OFFICE_NAMES = {
    "MEMBER OF COUNCIL": "Borough Council",
    "TOWNSHIP COMMISSIONER": "Township Commissioner",
    "TOWNSHIP SUPERVISOR": "Township Supervisor",
    "SCHOOL DIRECTOR": "School Director",
    "TAX COLLECTOR": "Tax Collector",
    "AUDITOR": "Auditor",
    "MAYOR": "Mayor",
}

# Header typos in the 2023 primary source, fixed to the county's 2023
# general spellings.
MUNI_FIXES = {
    "HOPEWELL SCHOOL DISTRICTION REGION 3": "HOPEWELL SCHOOL DISTRICT REGION 3",
    "RIVERSIDE SCHOOL DISTRCT": "RIVERSIDE SCHOOL DISTRICT",
}


def normalize_office(line: str):
    line = re.sub(r"\s+", " ", line).strip()
    if line in EXACT_OFFICES:
        return EXACT_OFFICES[line]

    m = MDJ_RE.match(line)
    if m:
        return ("Magisterial District Judge", m.group(1))

    m = LOCAL_RE.match(line)
    if m:
        office_raw, years, rest = m.group(1), m.group(2), m.group(3).strip()
        rest = MUNI_FIXES.get(rest, rest)
        office = LOCAL_OFFICE_NAMES.get(office_raw, title_case(office_raw))
        if years:
            office = f"{office} ({years} Year)"
        return (office, expand_muni_flexible(rest))

    return (title_case(line), "")


# ---------------------------------------------------------------------------
# Text preprocessing: header party -> candidate rows; drop footers and
# per-party statistics rows.
# ---------------------------------------------------------------------------

VOTE_FOR_LINE = re.compile(r"^Vote For \d+$")
HEADER_PARTY = re.compile(r"^(DEM|REP)\s+(.+)$")
PRECINCTS_REPORTING = re.compile(r"^\s*Precincts Reporting\b")
PARTY_STATS_LABEL = re.compile(
    r"^\s*(Registered Voters|Ballots Cast) - (?:DEMOCRATIC|REPUBLICAN|"
    r"NONPARTISAN|OTHER)\b"
)
NUM_ONLY = re.compile(r"^\s*\d[\d,\s]*%?\s*$")
TRAIL_NUMS = re.compile(
    r"^(.*\S)\s+(\d[\d,]*)\s+(\d[\d,]*)\s+(\d[\d,]*)\s+(\d[\d,]*)\s*$")
NOT_CANDIDATE = re.compile(
    r"^\s*(Write-In\b|Write-In:|Not Assigned|Total Votes Cast|Contest Totals|"
    r"Overvotes|Undervotes|Registered Voters|Ballots Cast|Voter Turnout)",
    re.IGNORECASE,
)


def is_numbers_only(s: str) -> bool:
    return bool(NUM_ONLY.match(s)) and bool(re.search(r"\d", s))


def prep_text(lines, precinct_mode: bool):
    """De-party contest headers, prefix candidate rows with the header's
    party token, drop per-contest footers and per-party statistics rows."""
    vote_for_idx = {i for i, l in enumerate(lines) if VOTE_FOR_LINE.match(l.strip())}
    out = []
    in_contest = False
    current_party = None
    n = len(lines)
    for i, raw in enumerate(lines):
        st = raw.rstrip()
        s = st.strip()
        if i in vote_for_idx:
            in_contest = True
            out.append(st)
            continue
        # contest header: the next non-blank line is "Vote For N"
        j = i + 1
        while j < n and not lines[j].strip():
            j += 1
        if j < n and j in vote_for_idx:
            m = HEADER_PARTY.match(s)
            if m:
                current_party = m.group(1)
                out.append(m.group(2).rstrip())
            else:
                current_party = None
                out.append(st)
            continue
        if PRECINCTS_REPORTING.match(s):
            continue
        if PARTY_STATS_LABEL.match(s):
            # per-party statistics rows are not recorded (repo convention).
            # In the precinct report "Ballots Cast - DEMOCRATIC"'s values
            # are printed on the preceding numbers-only line; drop both.
            # "Registered Voters - DEMOCRATIC N": value on the same line.
            if precinct_mode and s.upper().startswith("BALLOTS CAST") \
                    and out and is_numbers_only(out[-1].strip()):
                out.pop()
            continue
        if in_contest and current_party and not is_numbers_only(s):
            # "Write-In Totals" -> "<party> Write-ins": the aggregate row
            # joins its contest's party (DEM and REP sections of the same
            # office are separate contests here, so their Write-ins rows
            # must stay distinct).
            if re.match(r"^\s*Write-In Totals\b", s):
                out.append(f"{current_party} Write-ins{s[len('Write-In Totals'):]}")
                continue
            if not NOT_CANDIDATE.match(s):
                m = TRAIL_NUMS.match(s)
                if m:
                    # candidate row -> prefix with the contest's party token
                    out.append(f"{current_party} {s.rstrip()}")
                    continue
        out.append(st)
    return out


# ---------------------------------------------------------------------------
# County summary (parse_esr) post-processing.
# ---------------------------------------------------------------------------

# The county summary engine maps a few statewide names to canonical mixed
# case; Beaver's 2023 general files keep candidates as printed (ALL-CAPS),
# so restore the printed spellings.
PRINTED_NAMES = {
    v: k for k, v in {
        "DANIEL MCCAFFERY": "Daniel McCaffery",
        "CAROLYN CARLUCCIO": "Carolyn Carluccio",
        "JILL BECK": "Jill Beck",
        "TIMIKA LANE": "Timika Lane",
        "MARIA BATTISTA": "Maria Battista",
        "HARRY F SMAIL JR": "Harry F. Smail Jr.",
        "MATT WOLF": "Matt Wolf",
        "MEGAN MARTIN": "Megan Martin",
    }.items()
}


def sum_same_key(rows):
    """Sum rows sharing (office, district, party, candidate); keep first
    order.  Handles the repeated same-header term seats (see module doc)."""
    merged = []
    index = {}
    for r in rows:
        key = (r[1], r[2], r[3], r[4])
        pos = index.get(key)
        if pos is None:
            index[key] = len(merged)
            merged.append(list(r))
            continue
        p = merged[pos]
        if any(r[5:9]):
            for k in range(5, 9):
                if r[k]:
                    p[k] = str(int(p[k] or 0) + int(r[k]))
        # all-empty duplicate (a 0-vote repeat section) -- dropped
    return merged


def parse_county_summary(text_path):
    lines = open(text_path, encoding="utf-8").read().split("\n")
    prepped = prep_text(lines, precinct_mode=False)
    tf = tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False)
    tf.write("\n".join(prepped))
    tf.close()
    try:
        R = parse_esr(tf.name, COUNTY, normalize_office)
    finally:
        os.unlink(tf.name)
    # restore printed ALL-CAPS spellings (match the county's general files)
    for r in R.rows:
        if r[4] in PRINTED_NAMES:
            r[4] = PRINTED_NAMES[r[4]]
    R.rows = sum_same_key(R.rows)
    return R


# ---------------------------------------------------------------------------
# Precinct report (electionware_txt).
# ---------------------------------------------------------------------------

CONFIG = TxtConfig(
    county=COUNTY,
    normalize_office=normalize_office,
    prettify_precinct=prettify_all_caps_precinct,
)


def parse_precincts(text_path):
    lines = open(text_path, encoding="utf-8").read().split("\n")
    prepped = prep_text(lines, precinct_mode=True)
    tf = tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False)
    tf.write("\n".join(prepped))
    tf.close()
    try:
        rows, precinct_count, warnings, unnamed = parse_input(tf.name, CONFIG)
    finally:
        os.unlink(tf.name)
    return rows, precinct_count, warnings, unnamed


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def load_text(path):
    if path.lower().endswith(".pdf"):
        path = text_from_pdf(path)
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def main(argv):
    if len(argv) != 3:
        sys.exit(f"Usage: {argv[0]} <input.txt|pdf> <output.csv>")
    src, dst = argv[1], argv[2]
    title = load_text(src)[:400]
    if "Precinct Summary Results Report" in title:
        rows, precinct_count, warnings, unnamed = parse_precincts(src)
        with open(dst, "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(P_FIELDNAMES)
            for r in rows:
                w.writerow([r[c] for c in P_FIELDNAMES])
        print(f"Wrote {len(rows)} rows across {precinct_count} precincts to {dst}")
        if unnamed:
            print(f"Skipped {unnamed} unnamed (county-summary) Statistics segment(s)")
        if warnings:
            print(f"WARNING: {len(warnings)} unmatched lines:")
            for w in warnings[:40]:
                print("  " + w)
            if len(warnings) > 40:
                print("  ...")
        return
    # county summary
    if src.lower().endswith(".pdf"):
        src = text_from_pdf(src)
    R = parse_county_summary(src)
    n = write_rows_csv(dst, R)
    print(f"wrote {n} rows -> {dst}")
    print(f"contests parsed: {len(R.contests)}")


if __name__ == "__main__":
    main(sys.argv)