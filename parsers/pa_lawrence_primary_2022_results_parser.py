#!/usr/bin/env python3
"""Lawrence County, PA -- May 17, 2022 General Primary precinct results.

Source: "Lawrence PA 2022 Primary Precinct.pdf" -- Electionware "Summary
Results Report / Precinct Summary" (OFFICIAL RESULTS, generated June 7, 2022
10:15 AM, 375 pages, 75 precincts).  The generation date is after the June 3
GOP U.S. Senate recount and the companion county summary PDF matches the DOS
ENR post-recount GOP U.S. Senate anchor (Oz 3,613 / McCormick 3,600 /
Barnette 2,531), so no separate recount source is needed.

Format family: electionware_txt (pdftotext engine over the Electionware
"Summary Results Report" text grammar), adapted from
``pa_lawrence_primary_2023_results_parser.py`` for the same county/vendor.
2022-primary specifics handled here:

* Numeric run order on every data row is [total, election_day, mail,
  provisional] behind a VOTE % token (the 2023 order -- verified against the
  Statistics block: per-contest ED/mail sums equal the Ballots Cast
  breakdown), so no Beaver-style 2022 reorder is needed.
* Contest headers carry the ballot party ("DEM UNITED STATES SENATOR"); a
  preprocessor strips it and prefixes that section's data rows -- including
  "Write-In Totals" / "Overvotes" / "Undervotes" aggregate rows -- with the
  party token so each write-in/aggregate row carries its contest's party and
  the DEM and REP sections of the same office key distinctly (party-empty
  write-ins fail duplicate_entries in a primary).
* Per-party statistics rows ("Registered Voters - DEMOCRATIC", "Ballots
  Cast - REPUBLICAN", ...) are dropped (Beaver/Cambria/Cumberland
  2022-primary convention; the county's own 2022 general file keeps only the
  totals): "Registered Voters" / "Ballots Cast" / "Ballots Cast - Blank" go
  out with party "".
* "MEMBER OF DEMOCRATIC COUNTY COMMITTEE - MALE <seat>" headers have the
  seat area appended; the tail becomes `district` and the office normalizes
  to "Committeeman"/"Committeewoman" (Beaver/McKean precedent).  These
  contests are write-in-only in this report.
* Precinct name "New Castle1-2" prints without the space (as in the 2023
  primary); fixed to "New Castle 1-2" to match the county's 2022 general
  and 2023 files.

Candidates keep their printed ALL-CAPS spellings (the county's 2023
primary/general files do the same).

Usage:
    python parsers/pa_lawrence_primary_2022_results_parser.py \\
        <input.txt|input.pdf> <output.csv>

After writing the CSV the parser reconciles every contest section's
candidate+write-in sums against the source's printed "Total Votes Cast"
(all four columns) and prints any mismatch.
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

PROBLEMS = []

# ---------------------------------------------------------------------------
# Office / district mapping (2022 primary contest set).
# ---------------------------------------------------------------------------

EXACT_OFFICES = {
    "UNITED STATES SENATOR": ("U.S. Senate", ""),
    "GOVERNOR": ("Governor", ""),
    "LIEUTENANT GOVERNOR": ("Lieutenant Governor", ""),
}

CONGRESS_RE = re.compile(r"^REPRESENTATIVE IN CONGRESS\s+(\d+)(?:ST|ND|RD|TH)?\s+DISTRICT$")
STATE_SENATE_RE = re.compile(r"^SENATOR IN THE GENERAL ASSEMBLY\s+(\d+)(?:ST|ND|RD|TH)?\s+DISTRICT$")
STATE_HOUSE_RE = re.compile(r"^REPRESENTATIVE IN THE GENERAL ASSEMBLY\s+(\d+)(?:ST|ND|RD|TH)?\s+DISTRICT$")
STATE_COMMITTEE_RE = re.compile(r"^MEMBER OF (DEMOCRATIC|REPUBLICAN) STATE COMMITTEE$")
# "MEMBER OF DEMOCRATIC COUNTY COMMITTEE - MALE New Castle 1-1": the seat's
# precinct committee-district area is appended to the header (one seat per
# precinct here; the seats are write-in-only contests).
COUNTY_COMMITTEE_RE = re.compile(
    r"^MEMBER OF (DEMOCRATIC|REPUBLICAN) COUNTY COMMITTEE\s*-\s*(MALE|FEMALE)"
    r"(?:\s+(.+?))?\s*$",
    re.IGNORECASE,
)


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
        return (f"Member of {m.group(1).capitalize()} State Committee", "")

    m = COUNTY_COMMITTEE_RE.match(line)
    if m:
        office = "Committeeman" if m.group(2).upper() == "MALE" else "Committeewoman"
        area = (m.group(3) or "").strip()
        area = re.sub(r"^(New Castle)(\d)", r"\1 \2", area)
        return (office, title_case(area) if area else "")

    PROBLEMS.append(f"unmapped contest header: {line!r}")
    return (line.title(), "")


def _split_party_header(header: str):
    """("DEM", rest) from "DEM rest ..."; ("", header) otherwise."""
    m = re.match(r"^(DEM|REP)\s+(.+)$", header.strip())
    if m:
        return m.group(1), m.group(2).strip()
    return "", header.strip()


def normalize_office_primary(line: str):
    party, rest = _split_party_header(line)
    return normalize_office(rest)


# ---------------------------------------------------------------------------
# Text preprocessing.
# ---------------------------------------------------------------------------

VOTE_FOR_LINE = re.compile(r"^Vote For \d+$", re.IGNORECASE)
PAGE_FURNITURE = re.compile(
    r"(summary results report|report generated|page \d+ of|precinct summary -"
    r"|2022 primary|official results|election summary -|^May 17, 2022\s)",
    re.IGNORECASE)
STAT_RE = re.compile(r"^STATISTICS\b", re.IGNORECASE)
# Per-party statistics rows are not recorded (repo convention).
PARTY_STATS_LABEL = re.compile(
    r"^\s*(Registered Voters|Ballots Cast) - "
    r"(?:DEMOCRATIC|REPUBLICAN|NONPARTISAN|OTHER)\b")
NUM_TOKEN = re.compile(r"\d[\d,]*\.?\d*%?")
TRAIL_INT = re.compile(r"\s\d[\d,]*\s*$")
TOTAL_VOTES_CAST = re.compile(r"^Total Votes Cast\b", re.IGNORECASE)


def fix_precinct(name: str) -> str:
    """The primary source prints "New Castle1-2" (missing space); the
    county's general/other files use "New Castle 1-2"."""
    return re.sub(r"^(New Castle)(\d)", r"\1 \2", name)


def preprocess_precinct(text: str):
    """Inject each contest's header party onto its candidate/aggregate rows.

    The engine derives each row's party from a leading party token, so every
    data row inside a contest section gets the section's party prefix (Write
    -In Totals / Overvotes / Undervotes included; "Write-In: NAME" detail
    rows are absent from this report, "Not Assigned"/"Total Votes Cast" are
    left for the engine to skip by head label).

    Returns (preprocessed text, totals) where totals maps
    (pretty precinct, office, district, party) -> [total, ed, mail, prov]
    captured from the source's "Total Votes Cast" lines for reconciliation.
    """
    lines = text.split("\n")
    out = list(lines)
    totals = {}

    # Pass 1: collect every precinct name as printed (the skip-set the engine
    # likewise uses for continuation-page repeats).
    name_set = set()
    stats_idx = [i for i, ln in enumerate(lines) if STAT_RE.match(ln.strip())]
    for idx in stats_idx:
        name = fix_precinct(_name_above(lines, idx))
        if name:
            name_set.add(name)
    if len(name_set) != len(stats_idx):
        PROBLEMS.append(f"precinct name scan: {len(name_set)} names for "
                        f"{len(stats_idx)} Statistics markers")

    # Pass 2: drop per-party statistics rows, prefix contest data rows with
    # the section's party, capture each section's "Total Votes Cast".
    # Office-header lines are identified by "Vote For N" on the next
    # non-blank line (committee headers end in digits, so they must never
    # be treated as data rows).
    vote_for_idx = {i for i, ln in enumerate(lines)
                    if VOTE_FOR_LINE.match(ln.strip())}
    header_idx = set()
    for i, ln in enumerate(lines):
        if not ln.strip() or i in vote_for_idx:
            continue
        j = i + 1
        while j < len(lines) and not lines[j].strip():
            j += 1
        if j < len(lines) and j in vote_for_idx:
            header_idx.add(i)

    precinct = ""
    in_contest = False
    party = office = district = ""
    prev_nonblank = -1

    for i, raw in enumerate(lines):
        s = raw.strip()
        if not s:
            continue
        if STAT_RE.match(s):
            in_contest = False
            party = office = district = ""
            precinct = fix_precinct(_name_above(lines, i))
            prev_nonblank = i
            continue
        if i in header_idx:
            hdr = lines[i].strip()
            hdr_party, rest = _split_party_header(hdr)
            office, district = normalize_office(rest)
            party = hdr_party or ""
            if not hdr_party:
                PROBLEMS.append(f"contest header without party: {hdr!r}")
            in_contest = True
            prev_nonblank = i
            continue
        if VOTE_FOR_LINE.match(s):
            in_contest = True
            prev_nonblank = i
            continue
        if PARTY_STATS_LABEL.match(s):
            out[i] = ""  # party-level RV/BC rows not recorded (repo convention)
            prev_nonblank = i
            continue
        prev_nonblank = i
        if not (in_contest and party):
            continue
        if TOTAL_VOTES_CAST.match(s):
            head, nums = _numeric_run(s.split())
            vals = strip_percent(nums)
            if len(vals) == 4:
                totals[(precinct, office, district, party)] = [
                    int(v.replace(",", "")) for v in vals]
            else:
                PROBLEMS.append(f"Total Votes Cast tokens {vals}: {s!r}")
            continue
        # Data rows (named candidates, Write-In Totals, Overvotes, Undervotes)
        # end in a numeric run.  Precinct-name lines (the next precinct's name
        # between the previous section and its Statistics marker, or the
        # continuation-page repeat) end in digits but are never data -- the
        # engine drops both by name; skip them here too.
        if TRAIL_INT.search(s) and s not in name_set:
            out[i] = party + " " + s

    return "\n".join(out), totals


def _name_above(lines, marker_idx):
    """Precinct name = the last non-blank, non-furniture line above a
    Statistics marker."""
    for j in range(marker_idx - 1, -1, -1):
        t = lines[j].strip()
        if not t or PAGE_FURNITURE.search(t) or STAT_RE.match(t):
            continue
        return t
    return ""


def strip_percent(nums):
    return [t for t in nums if not t.endswith("%")]


def _numeric_run(tokens):
    k = len(tokens)
    while k > 0 and NUM_TOKEN.match(tokens[k - 1]):
        k -= 1
    return " ".join(tokens[:k]), tokens[k:]


CONFIG = TxtConfig(
    county="Lawrence",
    normalize_office=normalize_office_primary,
    prettify_precinct=fix_precinct,
    extra_junk=(
        r"\b2022 PRIMARY\b",
        r"\bMay 17, 2022\b",
    ),
)


def reconcile_totals(rows, totals) -> int:
    """Per contest section: named candidates + write-ins == the source's
    printed Total Votes Cast, all four columns.  Overvotes/Undervotes sit
    outside Total Votes Cast in this report (verified: total = named +
    write-ins)."""
    agg = defaultdict(lambda: [0, 0, 0, 0])
    for r in rows:
        if r["candidate"] in ("Overvotes", "Undervotes", "Registered Voters",
                              "Ballots Cast", "Ballots Cast - Blank"):
            continue
        key = (r["precinct"], r["office"], r["district"], r["party"])
        for k, col in enumerate(("votes", "election_day", "mail", "provisional")):
            v = r[col]
            if v:
                agg[key][k] += int(v)
    bad = 0
    for key, exp in sorted(totals.items()):
        act = agg.get(key)
        if act is None or act != exp:
            bad += 1
            print(f"MISMATCH {key}: rows {act} != Total Votes Cast {exp}")
    return bad


def pdf_to_text_stdin(pdf_path: str) -> str:
    return subprocess.run(["pdftotext", "-layout", pdf_path, "-"],
                          check=True, capture_output=True, text=True).stdout


def run_precinct(src: str, dst: str) -> int:
    if src.lower().endswith(".pdf"):
        text = pdf_to_text_stdin(src)
    else:
        with open(src, encoding="utf-8") as fh:
            text = fh.read()
    pre_text, totals = preprocess_precinct(text)
    tf = tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False)
    tf.write(pre_text)
    tf.close()
    try:
        rows, precinct_count, warnings, unnamed = parse_txt(
            Path(tf.name), CONFIG)
    finally:
        os.unlink(tf.name)
    # The injected party token leaves the raw "Write-In Totals" label on
    # those rows; re-label to the "Write-ins" convention (party stays DEM/REP
    # so the DEM and REP sections of the same office stay distinct, as in the
    # county's 2020 primary / 2023 primary files).
    for r in rows:
        if r["candidate"] == "Write-In Totals":
            r["candidate"] = "Write-ins"

    n_bad = reconcile_totals(rows, totals)
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
    print(f"engine warnings: {len(warnings)}")
    for w in warnings[:30]:
        print("WARN:", w)
    for p in PROBLEMS[:50]:
        print("PROBLEM:", p)
    print(f"Total-Votes-Cast reconciliation: {len(totals)} contest sections, "
          f"{n_bad} mismatch(es)")
    return len(rows)


def main(argv):
    if len(argv) != 3:
        sys.exit(f"Usage: {argv[0]} <input.pdf|input.txt> <output.csv>")
    run_precinct(argv[1], argv[2])


if __name__ == "__main__":
    main(sys.argv)