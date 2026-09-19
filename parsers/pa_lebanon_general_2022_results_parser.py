#!/usr/bin/env python3
"""
Parse Lebanon County PA 2022 General (November 8, 2022) precinct results.

Source: "Lebanon PA FINAL Official Precinct Summary Report.pdf" (120 pages,
Electionware "Summary Results Report" precinct summary; pre-extracted with
pdftotext -layout).  60 precincts, each spanning exactly 2 pages: page 1
carries the Statistics block

    LEBANON FIRST WARD EAST
    Statistics         TOTAL Election     Mail Provision
                             Day in/Absent         al
                                 ee
    Registered Voters - Total                    0
    Ballots Cast - Total                       313       256         50          7
    Ballots Cast - Blank                         0         0          0          0
    Voter Turnout - Total                   0.00%

and page 2 repeats the precinct label above the remaining contest sections.
Contest sections are

      UNITED STATES SENATOR
      Vote For 1
      <column-header junk>
    DEM JOHN FETTERMAN                         143        106        35          2
    ...
    Write-In Totals                              0             0      0          0

Contest headers carry NO party prefix (general election); candidate rows
carry the party code (DEM/REP/LIB/GRN/KEY).

Contests in this source (no local offices, no retention questions):
  UNITED STATES SENATOR                       -> U.S. Senate
  GOVERNOR AND LIEUTENANT GOVERNOR            -> Governor
  REPRESENTATIVE IN CONGRESS 9TH DISTRICT     -> U.S. House, district 9
  SENATOR IN THE GENERAL ASSEMBLY 48TH DISTRICT -> State Senate, district 48
  REPRESENTATIVE IN THE GENERAL ASSEMBLY 98TH/101ST/102ND
                                              -> State House, district N

Column mapping: TOTAL -> votes, "Election Day in/Absentee" -> election_day,
Mail -> early_voting (2022 repo convention), Provisional -> provisional.

NOT emitted (matching all other 2022 counties files): the source's
"Ballots Cast - Blank" statistics rows (all zeros but one precinct).
"Registered Voters - Total" prints 0 for every precinct in this report;
the rows are emitted as printed.

Usage:
    python parsers/pa_lebanon_general_2022_results_parser.py \\
        <input.txt-or-.pdf> <output.csv>
"""

import os
import re
import subprocess
import sys
import tempfile
from collections import defaultdict

COUNTY = "Lebanon"

PRECINCT_FIELDNAMES = ["county", "precinct", "office", "district", "party",
                       "candidate", "votes", "election_day", "early_voting",
                       "provisional"]

VOTEFOR_RE = re.compile(r"^Vote For\s+(\d+)$", re.IGNORECASE)
# candidate / statistics rows: label + 4 integers
ROW_RE = re.compile(r"^(.*?)\s+(\d[\d,]*)\s+(\d[\d,]*)\s+(\d[\d,]*)\s+(\d[\d,]*)$")
# Registered Voters has a single total.
SINGLE_RE = re.compile(r"^(.*?)\s+(\d[\d,]*)$")
PARTY_RE = re.compile(r"^(DEM|REP|LIB|GRN|KEY)\s+(.+)$")

PAGE_JUNK_RE = re.compile(
    r"^(Summary Results Report\b|2022 General$|November 8, 2022\b"
    r"|Report generated with Electionware\b|Precinct Summary - )")


def _title(name):
    out = []
    for w in name.split():
        # repo-wide 2022 convention (other counties' files) spells the Green
        # candidate "Christina DiGiulio"; plain capitalize gives "Digiulio".
        if w.upper() == "DIGIULIO":
            out.append("DiGiulio")
        else:
            out.append(w.capitalize())
    return " ".join(out)


def map_contest(header):
    """(office, district) for a contest header line; raises on unknown."""
    h = re.sub(r"\s+", " ", header).strip()
    hu = h.upper()

    if hu == "UNITED STATES SENATOR":
        return ("U.S. Senate", "")
    if hu in ("GOVERNOR AND LIEUTENANT GOVERNOR", "GOVERNOR"):
        return ("Governor", "")
    m = re.match(r"^REPRESENTATIVE IN CONGRESS (\d+)(ST|ND|RD|TH) DISTRICT$", hu)
    if m:
        return ("U.S. House", m.group(1))
    m = re.match(r"^SENATOR IN THE GENERAL ASSEMBLY (\d+)(ST|ND|RD|TH) DISTRICT$", hu)
    if m:
        return ("State Senate", m.group(1))
    m = re.match(r"^REPRESENTATIVE IN THE GENERAL ASSEMBLY (\d+)(ST|ND|RD|TH)$", hu)
    if m:
        return ("State House", m.group(1))

    raise SystemExit(f"unrecognized contest header: {header!r}")


def load_text(input_path):
    if input_path.lower().endswith(".pdf"):
        tf = tempfile.NamedTemporaryFile(suffix=".txt", delete=False)
        tmp = tf.name
        tf.close()
        try:
            subprocess.run(["pdftotext", "-layout", input_path, tmp],
                           check=True)
            with open(tmp, encoding="utf-8", errors="replace") as fh:
                return fh.read()
        finally:
            try:
                os.unlink(tmp)
            except OSError:
                pass
    with open(input_path, encoding="utf-8", errors="replace") as fh:
        return fh.read()


def parse(text, county=COUNTY):
    """Return (rows, warnings, checks)."""
    lines = text.splitlines()

    # --- collect precinct labels: the nonblank line right before each
    # "Statistics" column-header line (one per precinct, on page 1).
    prec_of = {}
    labels = set()
    for i, l in enumerate(lines):
        if l.strip().startswith("Statistics"):
            for j in range(i - 1, max(i - 5, -1), -1):
                t = lines[j].strip()
                if t and not PAGE_JUNK_RE.match(t):
                    labels.add(t)
                    prec_of[i] = t
                    break
    if len(labels) != 60:
        raise SystemExit(f"expected 60 precinct labels, found {len(labels)}")

    rows = []
    warnings = []

    def warn(msg):
        warnings.append(msg)

    # pre-mark contest-header lines: a line whose next nonblank line is
    # "Vote For N"
    contest_header_idx = set()
    for i, l in enumerate(lines):
        s = l.strip()
        if not s or s.startswith("Statistics") or PAGE_JUNK_RE.match(s):
            continue
        j = i + 1
        while j < len(lines) and not lines[j].strip():
            j += 1
        if j < len(lines) and VOTEFOR_RE.match(lines[j].strip()):
            contest_header_idx.add(i)

    precinct = None
    contest = None  # (office, district)
    n_contests = 0
    contest_sum = defaultdict(int)   # (precinct, office, district) -> sum
    blank = defaultdict(int)         # precinct -> blank total
    ballots = {}                     # precinct -> ballots-cast values
    skipped_nodigit = 0

    for i, l in enumerate(lines):
        s = l.strip()
        if not s:
            continue
        if PAGE_JUNK_RE.match(s):
            continue
        if s in labels:
            precinct = s
            continue
        if i in contest_header_idx:
            contest = map_contest(s)
            n_contests += 1
            continue
        if VOTEFOR_RE.match(s):
            continue  # consumed with the header above
        if s.startswith("Statistics"):
            continue
        if s.startswith("Voter Turnout"):
            continue

        if s.startswith("Registered Voters - Total"):
            m = SINGLE_RE.match(s)
            if not m:
                warn(f"{precinct}: no number on statistics row: {s!r}")
                continue
            rows.append([county, precinct, "Registered Voters", "", "", "",
                         m.group(2), "", "", ""])
            continue
        if s.startswith("Ballots Cast - Total"):
            m = ROW_RE.match(s)
            if not m:
                warn(f"{precinct}: no numbers on statistics row: {s!r}")
                continue
            vals = [int(v.replace(",", "")) for v in m.groups()[1:]]
            if precinct is None:
                raise SystemExit(f"statistics block with no precinct: {s!r}")
            ballots[precinct] = vals
            rows.append([county, precinct, "Ballots Cast", "", "", "",
                         str(vals[0]), str(vals[1]), str(vals[2]),
                         str(vals[3])])
            continue
        if s.startswith("Ballots Cast - Blank"):
            m = ROW_RE.match(s)
            if m:
                blank[precinct] = int(m.group(2).replace(",", ""))
            continue

        if contest is None:
            if re.search(r"\d", s):
                warn(f"{precinct}: data row outside any contest: {s[:70]!r}")
            else:
                skipped_nodigit += 1
            continue

        m = ROW_RE.match(s)
        if not m:
            if not re.search(r"\d", s):
                # wrapped column-header fragments ("Day in/Absent al", "ee")
                skipped_nodigit += 1
                continue
            warn(f"{precinct}: unrecognized row in contest {contest}: "
                 f"{s[:70]!r}")
            continue

        head = m.group(1).strip()
        vals = [int(v.replace(",", "")) for v in m.groups()[1:]]
        k = (precinct,) + contest
        if head == "Write-In Totals":
            rows.append([county, precinct, contest[0], contest[1], "",
                         "Write Ins", str(vals[0]), str(vals[1]),
                         str(vals[2]), str(vals[3])])
        else:
            pm = PARTY_RE.match(head)
            if not pm:
                warn(f"{precinct}: unrecognized contest row: {s[:70]!r}")
                continue
            name = _title(pm.group(2))
            rows.append([county, precinct, contest[0], contest[1],
                         pm.group(1), name, str(vals[0]), str(vals[1]),
                         str(vals[2]), str(vals[3])])
        contest_sum[k] += vals[0]

    # ---- arithmetic checks -------------------------------------------------
    bad_ballots = []
    for (p, office, district), v in sorted(contest_sum.items()):
        b = ballots.get(p)
        if b is None:
            warn(f"{p}: no Ballots Cast row")
            continue
        if v > b[0]:
            bad_ballots.append((p, office, district, v, b[0]))

    return rows, warnings, {
        "contest_sum": dict(contest_sum), "ballots": ballots,
        "blank": dict(blank), "bad_ballots": bad_ballots,
        "n_contests": n_contests, "skipped_nodigit": skipped_nodigit,
    }


def main():
    if len(sys.argv) != 3:
        sys.exit(f"Usage: {sys.argv[0]} <input.txt-or-.pdf> <output.csv>")
    src, dst = sys.argv[1], sys.argv[2]
    text = load_text(src)
    rows, warnings, checks = parse(text)

    with open(dst, "w", newline="", encoding="utf-8") as fh:
        w = __import__("csv").writer(fh)
        w.writerow(PRECINCT_FIELDNAMES)
        w.writerows(rows)
    precincts = {r[1] for r in rows}
    print(f"wrote {len(rows)} rows -> {dst} ({len(precincts)} precincts, "
          f"{checks['n_contests']} contest sections)")

    if checks["bad_ballots"]:
        for p, office, district, v, b in checks["bad_ballots"]:
            print(f"WARNING contest sum {p} {office} {district}: rows sum "
                  f"{v} > Ballots Cast {b}")
    else:
        print("contest arithmetic: every contest's rows sum <= precinct "
              "Ballots Cast")
    blank_nonzero = {p: v for p, v in checks["blank"].items() if v}
    print(f"Ballots Cast - Blank rows in source: "
          f"{len(checks['blank'])} (nonzero: {blank_nonzero or 'none'}) "
          f"- not emitted (2022 repo convention)")
    for w in warnings:
        print("WARN:", w)


if __name__ == "__main__":
    main()