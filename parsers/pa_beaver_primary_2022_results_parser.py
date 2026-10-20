#!/usr/bin/env python3
"""Parser for Beaver County, PA 2022 General Primary (May 17, 2022).

Source: "Beaver PA 2022 Primary 6.13 AMENDED OFFICIAL PRECINCT RESULTS.pdf"
(Electionware "Precinct Summary Results Report", official results INCLUDING
the June U.S. Senate recount -- the GOP U.S. Senate numbers below are the
post-recount certified totals, matching the 6.13 AMENDED summary and the
DOS ENR anchors).

The report is the same Electionware text family as the county's 2023
primary (see pa_beaver_primary_2023_results_parser.py / electionware_txt),
with two 2022-specific differences handled here:

* Column order: the 2022 report prints TOTAL, VOTE %, Mail Votes,
  Provisional Votes, Election Day -- i.e. the numeric run on a row is
  [total, mail, provisional, election_day] -- while the shared engine
  expects [total, election_day, mail, provisional] (the 2023 order).
  A preprocessing pass reorders every trailing 4-number numeric run
  [total, mail, prov, ed] -> [total, ed, mail, prov] and drops the VOTE %
  token(s).  Runs with other numbers of numeric tokens (precinct-number
  lines, RV same-line values, footers) are left untouched.
* Contest set: 2022 primary offices (U.S. Senator, Governor, Lieutenant
  Governor, Representative in Congress 17th District, Senator in the
  General Assembly 46th District, Representative in the General Assembly
  14th/15th/16th District, Member of Democratic/Republican State
  Committee, and per-municipality Democratic Committeeman/Committeewoman
  seats -- office "Committeeman"/"Committeewoman" with the committee
  district as `district`).

Party handling mirrors the 2023 primary parser: the DEM/REP token is
printed on the contest header; it is stripped there and prefixed to that
contest's data rows so the shared engine's row-level party handling
assigns each (named and write-in) row its contest party.  Per-party
statistics rows ("Registered Voters - DEMOCRATIC" ...) are dropped (repo
convention); only the totals ("Registered Voters", "Ballots Cast",
party "") are recorded.

Offices map to repo-standard names (U.S. Senate, Governor, U.S. House,
State Senate, State House, Member of ... State Committee); candidates
keep their printed ALL-CAPS spellings, matching the county's other
Electionware files.

Usage:
    python parsers/pa_beaver_primary_2022_results_parser.py \
        "<input.pdf|input.txt>" "<output.csv>"
"""

import csv
import os
import re
import sys
import tempfile

from electionware_txt import TxtConfig, parse_input
from electionware_precinct_np import (expand_muni_flexible, prettify_all_caps_precinct,
                                      title_case)

COUNTY = "Beaver"

# ---------------------------------------------------------------------------
# Office normalization (2022 primary).
# ---------------------------------------------------------------------------

NUMWORD = r"(\d+)(?:ST|ND|RD|TH)"

CONGRESS_RE = re.compile(r"^REPRESENTATIVE IN CONGRESS\s+(\d+)(?:ST|ND|RD|TH)?\s+DISTRICT$")
STATE_SENATE_RE = re.compile(r"^SENATOR IN THE GENERAL ASSEMBLY\s+(\d+)(?:ST|ND|RD|TH)?\s+DISTRICT$")
STATE_HOUSE_RE = re.compile(r"^REPRESENTATIVE IN THE GENERAL ASSEMBLY\s+(\d+)(?:ST|ND|RD|TH)?\s+DISTRICT$")
STATE_COMMITTEE_RE = re.compile(r"^MEMBER OF (DEMOCRATIC|REPUBLICAN) STATE COMMITTEE$")
COMMITTEE_RE = re.compile(r"^COMMITTE(EMAN|EWOMAN)\s+(.+)$")

EXACT_OFFICES = {
    "UNITED STATES SENATOR": ("U.S. Senate", ""),
    "GOVERNOR": ("Governor", ""),
    "LIEUTENANT GOVERNOR": ("Lieutenant Governor", ""),
}


def normalize_office(line: str):
    line = re.sub(r"\s+", " ", line).strip()
    if line in EXACT_OFFICES:
        return EXACT_OFFICES[line]

    m = CONGRESS_RE.match(line)
    if m:
        return ("U.S. House", m.group(1))

    m = STATE_SENATE_RE.match(line)
    if m:
        return ("State Senate", m.group(1))

    m = STATE_HOUSE_RE.match(line)
    if m:
        return ("State House", m.group(1))

    m = STATE_COMMITTEE_RE.match(line)
    if m:
        party = m.group(1)
        # Beaver's state committee districts: 4 (D, full county at-large)
        # per the DOS ENR anchor's district for this contest.
        return (f"Member of {party.capitalize()} State Committee", "4")

    m = COMMITTEE_RE.match(line)
    if m:
        office = "Committeeman" if m.group(1) == "EMAN" else "Committeewoman"
        return (office, title_case(expand_muni_flexible(m.group(2))))

    return (title_case(line), "")



# ---------------------------------------------------------------------------
# 2022 column-order fix: numeric run [total, mail, prov, ed] -> [total, ed,
# mail, prov]; VOTE % tokens dropped.
# ---------------------------------------------------------------------------

PTOK = re.compile(r"^[\d,]+(?:\.\d+)?%?$")


def reorder_2022_columns(lines):
    out = []
    for raw in lines:
        st = raw.rstrip()
        toks = st.split()
        i = len(toks)
        while i > 0 and PTOK.match(toks[i - 1]):
            i -= 1
        run = toks[i:]
        nums = [t for t in run if not t.endswith("%")]
        if len(nums) == 4:
            total, mail, prov, ed = (t.replace(",", "") for t in nums)
            head = " ".join(toks[:i]).rstrip()
            st = f"{head} {total} {ed} {mail} {prov}".rstrip()
        out.append(st)
    return out


# ---------------------------------------------------------------------------
# Text preprocessing: header party -> candidate rows; drop footers, page
# headers and per-party statistics rows.
# ---------------------------------------------------------------------------

VOTE_FOR_LINE = re.compile(r"^Vote For \d+$")
HEADER_PARTY = re.compile(r"^(DEM|REP)\s+(.+)$")
PRECINCTS_REPORTING = re.compile(r"^\s*Precincts Reporting\b")
PARTY_STATS_LABEL = re.compile(
    r"^\s*(Registered Voters|Ballots Cast) - (?:DEMOCRATIC|REPUBLICAN|"
    r"NONPARTISAN|OTHER)\b"
)
NUM_ONLY = re.compile(r"^\s*[\d,\s.%]+$")
TRAIL_NUMS = re.compile(
    r"^(.*\S)\s+(\d[\d,]*)\s+(\d[\d,]*)\s+(\d[\d,]*)\s+(\d[\d,]*)$")
NOT_CANDIDATE = re.compile(
    r"^\s*(Write-In:|Not Assigned|Total Votes Cast|Contest Totals|"
    r"Overvotes|Undervotes|Voter Turnout)",
    re.IGNORECASE,
)
NOT_CANDIDATE_IN_CONTEST = re.compile(r"^\s*Write-In Totals\b")


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
            if precinct_mode and s.upper().startswith("BALLOTS CAST") \
                    and out and is_numbers_only(out[-1].strip()):
                out.pop()
            continue
        if in_contest and current_party and not is_numbers_only(s):
            # "Write-In Totals" -> "<party> Write Ins": the aggregate row
            # joins its contest's party (DEM and REP sections of the same
            # office are separate contests here, so their write-in rows
            # must stay distinct and party-tagged).
            if NOT_CANDIDATE_IN_CONTEST.match(s):
                rest = s[len("Write-In Totals"):]
                out.append(f"{current_party} Write-ins{rest}")
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
# Precinct report (electionware_txt).
# ---------------------------------------------------------------------------

CFG = TxtConfig(
    county=COUNTY,
    normalize_office=normalize_office,
    prettify_precinct=prettify_all_caps_precinct,
    extra_junk=(
        r"2022 General Primary",
        r"US SENATE RECOUNT",
        r"OFFICIAL RESULTS",
        r"\bMay 17, 2022\b",
    ),
)


def load_text(path):
    if path.lower().endswith(".pdf"):
        import subprocess
        text = subprocess.run(
            ["pdftotext", "-layout", path, "-"],
            capture_output=True, text=True, check=True,
        ).stdout
        return text
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def parse_precincts(text_path):
    lines = load_text(text_path).split("\n")
    lines = reorder_2022_columns(lines)
    prepped = prep_text(lines, precinct_mode=True)
    tf = tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False)
    tf.write("\n".join(prepped))
    tf.close()
    try:
        rows, precinct_count, warnings, unnamed = parse_input(tf.name, CFG)
    finally:
        os.unlink(tf.name)
    return rows, precinct_count, warnings, unnamed


P_FIELDNAMES = ("county", "precinct", "office", "district", "party",
                "candidate", "votes", "election_day", "mail", "provisional")


def main(argv):
    if len(argv) != 3:
        sys.exit(f"Usage: {argv[0]} <input.pdf|txt> <output.csv>")
    src, dst = argv[1], argv[2]
    rows, precinct_count, warnings, unnamed = parse_precincts(src)
    with open(dst, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(P_FIELDNAMES)
        for r in rows:
            w.writerow([r[c] for c in P_FIELDNAMES])
    print(f"Wrote {len(rows)} rows across {precinct_count} precincts to {dst}")
    if unnamed:
        print(f"Skipped {unnamed} unnamed Statistics segment(s)")
    if warnings:
        print(f"WARNING: {len(warnings)} unmatched lines:")
        for w_ in warnings[:40]:
            print("  " + w_)
        if len(warnings) > 40:
            print("  ...")
    return 0


if __name__ == "__main__":
    main(sys.argv)