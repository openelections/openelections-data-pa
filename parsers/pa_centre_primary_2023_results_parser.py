#!/usr/bin/env python3
"""
Parser for Centre County, PA 2023 Primary Election (May 16, 2023).

Sources (Electionware "Summary Results Report" prints, pdftotext -layout):

- County summary ("Accumulated Results"): parsed through the shared
  ``parse_esr`` engine (parsers/pa_2023_summary_common.py) after light
  text preparation:
    * contest headers carry the ballot party (" DEM JUSTICE OF THE
      SUPREME COURT") -> rewritten to "<OFFICE> (DEM)" so the shared
      STATISTICS-block terminator regex sees the office keyword and
      map_contest can extract the contest party;
    * a sentinel data row ("ZZCONTESTMARKER 0 0 0 0") is inserted after
      each "Vote For N" line so candidate rows can be assigned to their
      contest exactly; marker rows are stripped before output.  The
      contest party is stamped onto every candidate row (primary
      convention); "Write-ins"/"Undervotes"/"Overvotes" rows keep an
      empty party.
- Per-precinct report: parsed with the county's 2023-general text engine
  ``electionware_txt`` (TxtConfig) after analogous preparation (party
  stripped from headers, party token prepended to candidate rows so the
  shared PARTY_RE assigns the contest party; per-party Registered
  Voters/Ballots Cast statistics lines dropped).

Column mapping matches the 2023 general files: "Absentee/ Mail-In" -> mail.

Usage:
    python parsers/pa_centre_primary_2023_results_parser.py \
        "<input.txt|input.pdf>" "<output.csv>"

The mode is auto-detected: a text with per-precinct "Statistics" markers
produces the 10-column precinct schema, otherwise the 9-column county
schema.
"""

import csv
import os
import pathlib
import re
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pa_2023_summary_common as sc
from pa_2023_summary_common import parse_esr
import electionware_txt as eng
from electionware_txt import TxtConfig, FIELDNAMES as PRECINCT_FIELDNAMES
import pa_centre_general_2023_results_parser as cg
from electionware_precinct_np import expand_muni_flexible

COUNTY = "Centre"

HEADER_PARTY_RE = re.compile(r"^(DEM|REP) (.+)$")
MARKER = "ZZCONTESTMARKER"
AGGREGATE_CANDIDATES = ("Write-ins", "Undervotes", "Overvotes",
                        "No Candidate Filed")

# Primary header spellings that differ from the 2023 general headers:
# "KCSD" (Keystone Central) vs the general file's "KSCSD"; PVASD's
# at-large seat prints as "SCHOOL DIRECTOR AT LARGE PVASD @ LARGE"; and
# the at-large Ferguson supervisor seat carries a "(2-YEAR INTERIM)" term.


def normalize_office(line: str):
    line = re.sub(r"\s+", " ", line).strip()
    if line == "SCHOOL DIRECTOR AT LARGE PVASD @ LARGE":
        return ("School Director At Large", "Penns Valley")
    line = line.replace("KCSD", "KSCSD")
    m = re.match(r"^SUPERVISOR AT LARGE \((\d+)-YEAR INTERIM\) (.+)$", line)
    if m:
        return (f"Township Supervisor At Large ({m.group(1)} Year)",
                expand_muni_flexible(m.group(2)))
    return cg.normalize_office(line)


def map_contest(header):
    h = re.sub(r"\s+", " ", header.strip())
    party = ""
    m = re.match(r"^(.*) \((DEM|REP)\)$", h)
    if m:
        rest, party = m.group(1), m.group(2)
    else:
        rest = h
    office, district = normalize_office(rest)
    return (office, district, party)


# Clean_name() canonicalizes a few statewide candidates to mixed case;
# Centre's 2023 general convention (mirrored here) keeps ALL-CAPS, so
# revert the canonicalizations to the exact source spelling.
CANON_REVERT = {
    "Daniel McCaffery": "DANIEL MCCAFFERY",
    "Jill Beck": "JILL BECK",
    "Timika Lane": "TIMIKA LANE",
    "Matt Wolf": "MATT WOLF",
    "Carolyn Carluccio": "CAROLYN CARLUCCIO",
    "Megan Martin": "MEGAN MARTIN",
    "Maria Battista": "MARIA BATTISTA",
    "Harry F. Smail Jr.": "HARRY F SMAIL JR",
}


# ---------------------------------------------------------------------------
# County summary mode.
# ---------------------------------------------------------------------------

def prepare_summary_text(text, problems):
    """Rewrite " DEM X" contest headers to "X (DEM)" and insert a marker
    row after each "Vote For N" line.  Per-party Registered Voters /
    Ballots Cast statistics lines are dropped (not recorded; per-party
    RV lines would also stop the shared engine's STATISTICS scanner
    early, since they start with "Registered...")."""
    lines = text.split("\n")
    out = []
    for l in lines:
        s = l.strip()
        if re.match(r"^(?:Registered Voters|Ballots Cast) - "
                    r"(?:DEMOCRATIC|REPUBLICAN|NONPARTISAN)", s):
            continue
        out.append(l)
    lines = out
    out = list(lines)
    for i, l in enumerate(lines):
        s = l.strip()
        if not re.match(r"^vote for \d+$", s, re.I):
            continue
        out[i] = l + "\n{} 0 0 0 0".format(MARKER)
        j = i - 1
        while j >= 0 and not lines[j].strip():
            j -= 1
        if j < 0:
            problems.append("no header line above 'Vote For': %r" % s)
            continue
        h = lines[j].strip()
        m = HEADER_PARTY_RE.match(h)
        if not m:
            problems.append("contest header without DEM/REP prefix: %r" % h)
            continue
        out[j] = "%s (%s)" % (m.group(2), m.group(1))
    return "\n".join(out)


def parse_county_summary(text, problems):
    prepared = prepare_summary_text(text, problems)
    R = parse_prepared_summary(prepared, problems)

    # Assign contest parties (candidate rows only) and drop marker rows,
    # preserving row order; metadata rows keep their original position.
    k = -1
    blocks = []
    new_rows = []
    for r in R.rows:
        if r[4] == MARKER:
            k += 1
            blocks.append([])
            continue
        if r[4] == "":
            new_rows.append(r)  # Registered Voters / Ballots Cast row
            continue
        if k >= 0:
            blocks[k].append(r)
        else:
            problems.append("data row before first contest marker: %r" % (r,))
            continue
        contest = R.contests[k] if k < len(R.contests) else None
        party = contest[2] if contest else ""
        # Write-in/over-under aggregate rows carry the contest party: the
        # repo's duplicate_entries data test hashes the party column, so
        # party-empty write-in rows from the DEM and REP sections of the
        # same local office would collide (mirrors the 2020/2024 primary
        # precinct files, which stamp the contest party on write-ins).
        if r[4] in AGGREGATE_CANDIDATES:
            r[3] = party
        elif r[3] == "":
            r[3] = party
        elif party and r[3] != party:
            problems.append("%s | %s: candidate %r party %r disagrees with "
                            "contest header party %r (header wins)" %
                            (r[1], r[2], r[4], r[3], party))
            r[3] = party
        if r[4] in CANON_REVERT:
            r[4] = CANON_REVERT[r[4]]
        new_rows.append(r)
    R.rows = new_rows

    if len(blocks) != len(R.contests):
        problems.append("marker/contest count mismatch: %d markers vs %d "
                        "contests" % (len(blocks), len(R.contests)))
    return R, blocks


def parse_prepared_summary(prepared, problems):
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False,
                                     encoding="utf-8") as tf:
        tf.write(prepared)
        tmp = tf.name
    try:
        return parse_esr(tmp, COUNTY, map_contest, titlecase_names=False)
    finally:
        os.unlink(tmp)


def check_summary_reconciliation(R, blocks, problems):
    """Per contest: candidate rows + write-ins (+ over/under where the
    source total includes them) == 'Total Votes Cast'."""
    ok = 0
    bad = []
    for idx, blk in enumerate(blocks):
        if idx >= len(R.contests):
            break
        contest = R.contests[idx]
        total = sum(int(r[5]) for r in blk
                    if r[4] not in ("Undervotes", "Overvotes"))
        expected = R.totals.get(contest)
        if expected is None:
            problems.append("no 'Total Votes Cast' row for contest %r" %
                            (contest,))
            continue
        if total == expected:
            ok += 1
        else:
            bad.append((contest, total, expected))
    return ok, bad


def vote_for_counts(raw_text):
    """Map contest header line -> 'Vote For N' (party prefix stripped)."""
    lines = raw_text.split("\n")
    counts = {}
    for i, l in enumerate(lines):
        s = l.strip()
        if not re.match(r"^vote for \d+$", s, re.I):
            continue
        j = i - 1
        while j >= 0 and not lines[j].strip():
            j -= 1
        if j < 0:
            continue
        m = HEADER_PARTY_RE.match(lines[j].strip())
        if m:
            counts[map_contest("%s (%s)" % (m.group(2), m.group(1)))] = \
                int(s.split()[-1])
    return counts


def check_party_ballots(R, raw_text, problems):
    m = re.search(r"Ballots Cast - DEMOCRATIC\s+([\d,]+)", raw_text)
    dem_bc = int(m.group(1).replace(",", "")) if m else None
    m = re.search(r"Ballots Cast - REPUBLICAN\s+([\d,]+)", raw_text)
    rep_bc = int(m.group(1).replace(",", "")) if m else None
    m = re.search(r"Ballots Cast - Blank\s+([\d,]+)", raw_text)
    blank = int(m.group(1).replace(",", "")) if m else None
    vfc = vote_for_counts(raw_text)
    over = 0
    for contest in R.contests:
        expected = R.totals.get(contest)
        if expected is None:
            continue
        cap = (dem_bc if contest[2] == "DEM"
               else rep_bc if contest[2] == "REP" else None)
        n = vfc.get(contest, 1)
        if cap is not None and expected > cap * n:
            problems.append(
                "contest %s | %s | %s: total %d exceeds party ballots "
                "cast %d" % (contest[0], contest[1], contest[2], expected,
                             cap))
            over += 1
    return dem_bc, rep_bc, blank, over


def check_writein_details(raw_text, problems):
    """Named 'Write-In: NAME' detail rows must sum to the contest's
    'Write-In Totals' aggregate (when the source reports both)."""
    lines = raw_text.split("\n")
    header_idx = []
    for i, l in enumerate(lines):
        if re.match(r"^vote for \d+$", l.strip(), re.I):
            j = i - 1
            while j >= 0 and not lines[j].strip():
                j -= 1
            header_idx.append(j)
    checked = 0
    for n, start in enumerate(header_idx):
        end = header_idx[n + 1] if n + 1 < len(header_idx) else len(lines)
        seg = lines[start:end]
        details = 0
        aggregate = None
        for l in seg:
            s = l.strip()
            m = re.match(r"^Write-In: (.+?)\s+(\d[\d,]*)\s+(\d[\d,]*)\s*"
                         r"(\d[\d,]*)\s+(\d[\d,]*)\s*$", s)
            if m:
                details += int(m.group(2).replace(",", ""))
            m2 = re.match(r"^Write-In Totals\s+([\d,]+)", s)
            if m2 and m2.group(1):
                aggregate = int(m2.group(1).replace(",", ""))
        if details and aggregate is not None:
            checked += 1
            if details != aggregate:
                problems.append(
                    "write-in details %d != aggregate %d in %r" %
                    (details, aggregate, lines[start].strip()))
    return checked


# ---------------------------------------------------------------------------
# Precinct mode.
# ---------------------------------------------------------------------------

PREC_TAIL4_RE = re.compile(
    r"^(.*?)\s+(\d[\d,]*)\s+(\d[\d,]*)\s+(\d[\d,]*)\s+(\d[\d,]*)\s*$")
RESERVED_HEADS = {"write-in totals", "not assigned", "total votes cast",
                  "contest totals", "overvotes", "undervotes", "yes", "no"}


def prepare_precinct_text(text, problems):
    """Strip DEM/REP from contest headers; prepend the contest party token
    to candidate rows (the shared PARTY_RE then assigns the party); drop
    per-party statistics lines."""
    lines = text.split("\n")
    n = len(lines)
    header_idx = set()
    for i, l in enumerate(lines):
        if not l.strip():
            continue
        j = i + 1
        while j < n and not lines[j].strip():
            j += 1
        if j < n and re.match(r"^\s*vote for \d+\s*$", lines[j].strip(),
                              re.I):
            header_idx.add(i)

    out = []
    party = ""
    in_contest = False
    for i, l in enumerate(lines):
        s = l.strip()
        if re.match(r"^\s*statistics\b", s, re.I):
            in_contest = False
            party = ""
            out.append(l)
            continue
        if i in header_idx:
            m = HEADER_PARTY_RE.match(s)
            if m:
                party = m.group(1)
                out.append(m.group(2))
            else:
                party = ""
                out.append(l)
                problems.append("precinct header without party: %r" % s)
            in_contest = True
            continue
        if re.match(r"^\s*vote for \d+\s*$", s, re.I):
            out.append(l)
            continue
        if re.match(r"^\s*total votes cast\b", s, re.I):
            in_contest = False
            out.append(l)
            continue
        if re.match(r"^(?:Registered Voters|Ballots Cast) - "
                    r"(?:DEMOCRATIC|REPUBLICAN|NONPARTISAN)", s):
            continue  # per-party stats lines are not recorded
        if in_contest and party:
            m = PREC_TAIL4_RE.match(s)
            if m:
                head = m.group(1).strip()
                low = head.lower()
                if head and low in RESERVED_HEADS:
                    # Rewrite the write-in/over-under aggregate rows to
                    # carry the contest party (see run_county comment).
                    if low == "write-in totals":
                        out.append("%s Write-ins%s" % (
                            party, s[len(head):]))
                        continue
                    if low in ("overvotes", "undervotes"):
                        out.append("%s %s%s" % (party, head, s[len(head):]))
                        continue
                if head and low not in RESERVED_HEADS \
                        and not head.upper().startswith("WRITE-IN:"):
                    out.append("%s %s" % (party, s))
                    continue
        out.append(l)
    return "\n".join(out)


# ---------------------------------------------------------------------------
# CLI.
# ---------------------------------------------------------------------------

def load_text(input_path):
    if input_path.lower().endswith(".pdf"):
        tmp = sc.text_from_pdf(input_path)
        with open(tmp, encoding="utf-8") as fh:
            text = fh.read()
        os.unlink(tmp)
        return text
    with open(input_path, encoding="utf-8") as fh:
        return fh.read()


def main(argv):
    if len(argv) != 3:
        sys.exit("Usage: %s <input.txt|pdf> <output.csv>" % argv[0])
    src, dst = argv[1], argv[2]
    text = load_text(src)
    n_stat = len(re.findall(r"^\s*statistics\b", text, re.I | re.M))
    problems = []

    if n_stat >= 3:
        return run_precinct(text, dst, problems)
    return run_county(text, dst, problems)


def run_precinct(text, dst, problems):
    prepared = prepare_precinct_text(text, problems)
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False,
                                     encoding="utf-8") as tf:
        tf.write(prepared)
        tmp = tf.name
    try:
        cfg = TxtConfig(
            county=COUNTY,
            normalize_office=normalize_office,
            prettify_precinct=cg.CONFIG.prettify_precinct,
            extra_junk=(r"(?i)^may 16, 2023\b",
                        r"(?i)^electionware county$"),
        )
        rows, precinct_count, warnings, unnamed = eng.parse_txt(
            pathlib.Path(tmp), cfg)
    finally:
        os.unlink(tmp)
    with open(dst, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(PRECINCT_FIELDNAMES)
        for r in rows:
            w.writerow([r[c] for c in PRECINCT_FIELDNAMES])
    print("wrote %d rows across %d precincts -> %s" %
          (len(rows), precinct_count, dst))
    for w_ in warnings[:20]:
        print("WARN:", w_)
    if len(warnings) > 20:
        print("... %d more warnings" % (len(warnings) - 20))
    for p in problems[:20]:
        print("PROBLEM:", p)
    return 0


def run_county(text, dst, problems):
    R, blocks = parse_county_summary(text, problems)
    n = sc.write_csv(dst, R)
    print("wrote %d rows -> %s" % (n, dst))
    print("contests parsed: %d" % len(R.contests))

    ok, bad = check_summary_reconciliation(R, blocks, problems)
    print("contest totals check: %d/%d reconcile" % (ok, len(R.contests)))
    for contest, total, expected in bad:
        print("WARNING %s | %s | %s: rows sum %d != Total Votes Cast %d" %
              (contest[0], contest[1], contest[2], total, expected))

    dem_bc, rep_bc, blank, over = check_party_ballots(R, text, problems)
    print("party ballots cast: DEM %s / REP %s / blank %s; contest totals "
          "exceeding party ballots cast: %d" % (dem_bc, rep_bc, blank, over))

    checked = check_writein_details(text, problems)
    print("write-in detail checks: %d contests with named details "
          "reconciled" % checked)

    for p in problems[:20]:
        print("PROBLEM:", p)
    if len(problems) > 20:
        print("... %d more problems" % (len(problems) - 20))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))