#!/usr/bin/env python3
"""Parse Berks County PA 2022 General Primary (May 17, 2022) precinct results.

Sources (Electionware "Summary Results Report", pdftotext -layout text):

  * ``Berks PA Precinct Summary with 8-9 Prov. final OFFICIAL.pdf`` — per-precinct
    report (1,594 pages, 202 precincts).  Produces the 10-column precinct CSV:
    ``2022/counties/20220517__pa__primary__berks__precinct.csv``.
  * ``Berks PA Official Results General Primary 2022.pdf`` — countywide summary
    (202 pages).  Used as the internal verification target (precinct-row sums vs
    the county-printed totals); pass ``--county <in> <out>`` to also emit a
    county-level CSV.

Both files share the section layout: a party-prefixed contest header
(``DEM United States Senator`` / ``REP Governor``) directly above a
``Vote For N`` line, candidate rows, ``Write-In Totals`` and ``Total Votes
Cast``.  In a closed primary the party lives on the **contest header**; every
candidate row under it inherits that party.  The aggregate ``Write-In Totals``
row carries the contest's party too (party-empty primary write-in rows collide
on (office, district, '', 'Write-ins') and trip the duplicate_entries test).

Contest/office mapping follows the 2022-primary brief and the county's other
files:

  * ``DEM United States Senator``                    -> U.S. Senate
  * ``REP Governor`` / ``Lieutenant Governor``       -> Governor / Lieutenant Governor
  * ``Representative in Congress District N``        -> U.S. House (district N)
  * ``Representative in the General Assembly District N`` -> State House (N)
  * ``Senator in the General Assembly District N``   -> State Senate (N)
  * ``Member of State Committee District N``         -> Member of
    Democratic/Republican State Committee (district N; anchor naming)
  * ``County Committeeman/Committeewoman/Committeeperson <precinct>``
      -> office as printed with the precinct appended (Allegheny 2022-primary
         convention), district empty
  * ``Home Rule Charter 606`` / ``907`` (City of Reading ballot questions,
    no party prefix)      -> office as printed, candidates Yes/No
  * candidate names are kept exactly as printed (e.g. "Christopher Dianna")

The per-precinct STATISTICS block carries only ``Ballots Cast - Total /
Democratic / Republican / Nonpartisan`` (no Registered Voters rows); they are
emitted as ``Ballots Cast`` rows with the party ("" / DEM / REP / NON), the
Centre-County 2022-primary convention.

Usage:
    python parsers/pa_berks_primary_2022_results_parser.py <precinct.txt|pdf> <out.csv>
    python parsers/pa_berks_primary_2022_results_parser.py --county <county.txt|pdf> <out.csv>
"""

import os
import re
import sys

COUNTY = "Berks"

FIELDNAMES = ["county", "precinct", "office", "district", "party",
              "candidate", "votes", "election_day", "mail", "provisional"]

# A row ending with 4 integer tokens (comma-thousands allowed):
# total, election_day, mail, provisional
TAIL4_RE = re.compile(
    r"^(.*?)\s+(\d[\d,]*)\s+(\d[\d,]*)\s+(\d[\d,]*)\s+(\d[\d,]*)$")
# A line consisting of exactly four integer tokens (statistics payload is
# extracted on its own line, *above* its label, in the precinct report)
BARE4_RE = re.compile(
    r"^(\d[\d,]*)\s+(\d[\d,]*)\s+(\d[\d,]*)\s+(\d[\d,]*)$")
VOTE_FOR_RE = re.compile(r"^Vote For\s+(\d+)$", re.I)
CONTEST_HDR_RE = re.compile(r"^(DEM|REP)\s+(\S.*)$")
PARTYLESS_HDR_RE = re.compile(r"^Home Rule Charter \d+$")

# Page furniture / column-header lines that are never data
FURNITURE_RE = re.compile(
    r"^(?:"
    r"Summary Results Report(?: By Precinct)?\b.*"
    r"|OFFICIAL RESULTS|UNOFFICIAL RESULTS"
    r"|2022 General Primary|May 17, 2022"
    r"|Berks County$"
    r"|Precinct Summary -|Election Summary -"
    r"|Report generated|Copyright"
    r")", re.I)
COLHDR_RE = re.compile(
    r"^(?:TOTAL(?:\s+(?:Election|Day|Mail|Provisional))*"
    r"|Day|Mail|Provisional|Election)$", re.I)

BC_LABELS = {
    "Total": ("Ballots Cast", ""),
    "Democratic": ("Ballots Cast", "DEM"),
    "Republican": ("Ballots Cast", "REP"),
    "Nonpartisan": ("Ballots Cast", "NON"),
}


def nums_from(tokens):
    """Trailing integer tokens with commas stripped."""
    out = []
    for t in tokens:
        t = t.replace(",", "")
        if t.isdigit():
            out.append(int(t))
        else:
            break
    return out


def map_contest(header):
    """(office, district, party, vote_for-agnostic) for a contest header.

    Returns None for unrecognized headers (reported, never guessed).
    """
    h = re.sub(r"\s+", " ", header.strip())
    m = CONTEST_HDR_RE.match(h)
    if m:
        party, rest = m.group(1), m.group(2).strip()
        if rest == "United States Senator":
            return "U.S. Senate", "", party
        if rest == "Governor":
            return "Governor", "", party
        if rest == "Lieutenant Governor":
            return "Lieutenant Governor", "", party
        m2 = re.match(r"^Representative in Congress District (\d+)$", rest, re.I)
        if m2:
            return "U.S. House", m2.group(1), party
        m2 = re.match(
            r"^Representative in the General Assembly District (\d+)$",
            rest, re.I)
        if m2:
            return "State House", m2.group(1), party
        m2 = re.match(
            r"^Senator in the General Assembly District (\d+)$", rest, re.I)
        if m2:
            return "State Senate", m2.group(1), party
        m2 = re.match(r"^Member of State Committee District (\d+)$", rest, re.I)
        if m2:
            office = ("Member of Democratic State Committee" if party == "DEM"
                      else "Member of Republican State Committee")
            return office, m2.group(1), party
        m2 = re.match(r"^(County Committeeperson|County Committeewoman"
                      r"|County Committeeman)\s+(.+)$", rest, re.I)
        if m2:
            # keep the printed gendered label verbatim, precinct appended
            label = m2
            return f"{label.group(1)} {label.group(2).strip()}", "", party
        return None
    m = PARTYLESS_HDR_RE.match(h)
    if m:
        return h, "", ""           # e.g. "Home Rule Charter 606" (Yes/No)
    return None


def is_junk(s):
    if FURNITURE_RE.match(s) or COLHDR_RE.match(s) or VOTE_FOR_RE.match(s):
        return True
    if BARE4_RE.match(s):
        return True                 # statistics payload / wrapped numbers
    return False


def text_from_pdf(src):
    import subprocess
    with open(os.devnull, "w") as devnull:
        return subprocess.run(
            ["pdftotext", "-layout", src, "-"], stdout=subprocess.PIPE,
            stderr=devnull, text=True).stdout


def load_text(src):
    if src.lower().endswith(".pdf"):
        return text_from_pdf(src)
    with open(src, encoding="utf-8") as fh:
        return fh.read()


def unwrap_writein_totals(lines):
    """Rejoin a 'Write-In Totals' label wrapped across three lines when the
    totals payload was pushed onto a later line (Electionware page-break
    artifact, seen once in the 2022 precinct report):

        Write-In
        Total     Totals
        Votes Cast    0    0    0    0
    """
    out = []
    i = 0
    n = len(lines)
    while i < n:
        s = lines[i].strip()
        if s == "Write-In" and i + 2 < n:
            joined = (s + " " + lines[i + 1].strip() + " "
                      + lines[i + 2].strip())
            m = TAIL4_RE.match(joined)
            if m and re.search(r"\bTotals?\b", lines[i + 1], re.I):
                out.append("Write-In Totals "
                           + " ".join(m.groups()[1:]))
                i += 3
                continue
        out.append(lines[i])
        i += 1
    return out


def parse_report(text_path, by_precinct=True):
    """Parse the Berks 2022-primary Electionware text into rows.

    Returns (rows, warnings, checks) where rows are dicts with COUNTY
    filled in; checks carry per-section 'Total Votes Cast' records for
    internal verification.
    """
    text = load_text(text_path)
    lines = unwrap_writein_totals([l.rstrip() for l in text.split("\n")])

    warnings = []
    checks = []
    rows = []
    row = [COUNTY, "", "", "", "", "", "", "", "", ""]  # mutable template

    def add(precinct, office, district, party, candidate, total, ed, mi, pr):
        rows.append([COUNTY, precinct, office, district, party, candidate,
                     total, ed, mi, pr])

    precinct = ""
    expect_precinct = False
    section = None            # (office, district, party, header, vote_for)
    in_stats = False
    prev_ints = None          # BARE4 line payloads (statistics/wrapped rows)

    for raw in lines:
        s = raw.strip()
        if not s:
            continue

        # -- page / precinct boundaries -------------------------------------
        if re.match(r"^Summary Results Report\b", s):
            expect_precinct = by_precinct
            in_stats = False
            section = None
            prev_ints = None
            continue
        if expect_precinct:
            if is_junk(s) or s == "STATISTICS":
                continue
            precinct = s
            expect_precinct = False
            in_stats = False
            section = None
            prev_ints = None
            continue

        if s == "Berks County":       # page furniture line
            continue
        if FURNITURE_RE.match(s):
            continue
        if VOTE_FOR_RE.match(s):
            vf = int(VOTE_FOR_RE.match(s).group(1))
            if section is not None:
                section = (section[0], section[1], section[2], section[3], vf)
            continue

        if COLHDR_RE.match(s):
            continue

        # -- STATISTICS block ------------------------------------------------
        if s == "STATISTICS":
            in_stats = True
            prev_ints = None
            continue

        if BARE4_RE.match(s):
            nums = [int(t.replace(",", "")) for t in BARE4_RE.match(s).groups()]
            prev_ints = nums
            continue

        m = re.match(r"^Ballots Cast - (Total|Democratic|Republican"
                     r"|Nonpartisan)(?:\b(.*))?$", s, re.I)
        if m:
            label, rest = m.group(1), m.group(2).strip() if m.group(2) else ""
            office, party = BC_LABELS[label]
            if rest:
                nums = nums_from(rest.split())
                if len(nums) != 4:
                    warnings.append(f"{precinct}: BC '{label}' bad row {s!r}")
                    nums = None
            elif not in_stats and prev_ints is None:
                warnings.append(f"{precinct}: BC '{label}' no numbers")
                nums = None
            else:
                nums = list(prev_ints) if prev_ints else None
                if not nums or len(nums) < 4:
                    warnings.append(f"{precinct}: BC '{label}' no numbers")
                    nums = None
            if nums:
                add(precinct, office, "", party, "",
                    nums[0],
                    nums[1] if len(nums) > 1 else "",
                    nums[2] if len(nums) > 2 else "",
                    nums[3] if len(nums) > 3 else "")
            prev_ints = None
            continue
        if in_stats:
            # the stats block ends at the first contest header on the page
            m = map_contest(s)
            if m:
                in_stats = False
                section = (m[0], m[1], m[2], s, None)
                prev_ints = None
                continue
            continue                  # remaining stats labels/dross

        # -- contest header ---------------------------------------------------
        if s.startswith(("Write-In Totals", "Write-In:", "Total Votes Cast",
                         "Not Assigned")):
            if s.startswith("Total Votes Cast"):
                nums = nums_from(s.split()[3:])
                if not nums and prev_ints is not None:
                    nums = list(prev_ints)
                if section is not None and nums:
                    checks.append((precinct, section[0], section[1],
                                   section[2], tuple(nums[:4])))
                prev_ints = None
                continue
            if s.startswith("Write-In Totals"):
                nums = nums_from(s.split()[2:])
                if not nums and prev_ints is not None:
                    nums = list(prev_ints)
                if section is not None and nums and len(nums) == 4:
                    add(precinct, section[0], section[1], section[2],
                        "Write-ins", nums[0], nums[1], nums[2], nums[3])
                elif section is not None:
                    warnings.append(
                        f"{precinct}: Write-In Totals row bad {s!r}")
                prev_ints = None
                continue
            prev_ints = None
            continue                  # Write-In: detail / Not Assigned

        if section is None:
            m = map_contest(s)
            if m:
                section = (m[0], m[1], m[2], s, None)
                prev_ints = None
                continue
            # stray text outside any section
            warnings.append(f"{precinct}: line outside contest: {s[:70]!r}")
            continue

        # inside a contest: candidate row
        m = TAIL4_RE.match(s)
        if m:
            head = m.group(1).strip()
            vals = [int(t.replace(",", "")) for t in m.groups()[1:]]
            if head.upper() in ("YES", "NO"):
                add(precinct, section[0], section[1], section[2],
                    head.capitalize(), vals[0], vals[1], vals[2], vals[3])
            else:
                add(precinct, section[0], section[1], section[2],
                    head, vals[0], vals[1], vals[2], vals[3])
            prev_ints = None
            continue

        m = map_contest(s)
        if m:
            section = (m[0], m[1], m[2], s, None)
            prev_ints = None
            continue
        warnings.append(f"{precinct}: unrecognized data row in "
                        f"{section[0]!r}: {s[:80]!r}")
        prev_ints = None

    return rows, warnings, checks


def write_csv(rows, output_path):
    with open(output_path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(FIELDNAMES)
        for r in rows:
            w.writerow(r)


def main(argv):
    if len(argv) < 3:
        sys.exit(f"Usage: {argv[0]} [--county] <input.txt-or-.pdf> <output.csv>")
    county_mode = argv[1] == "--county"
    src, dst = argv[-2], argv[-1]
    if os.path.basename(src).lower().endswith(".pdf") and county_mode:
        pass  # handled by load_text
    rows, warnings, checks = parse_report(src, by_precinct=not county_mode)
    write_csv(rows, dst)
    print(f"wrote {len(rows)} rows -> {dst}")
    print(f"warnings: {len(warnings)}")
    for w in warnings[:30]:
        print("WARN:", w, file=sys.stderr)
    if not county_mode:
        # internal sum check: candidate+write-in rows vs the section's
        # printed 'Total Votes Cast' row
        bad = 0
        for (p, office, dist, party, tot) in checks:
            cum = [0, 0, 0, 0]
            for r in rows:
                if (r[1] == p and r[2] == office and r[3] == dist
                        and r[4] == party):
                    for k in range(4):
                        cum[k] += int(r[6 + k] or 0)
            if cum != list(tot):
                bad += 1
                if bad <= 10:
                    print(f"SUM MISMATCH {p} {party} {office} {dist}: "
                          f"rows {cum} vs printed {list(tot)}", file=sys.stderr)
        print(f"internal Total-Votes-Cast checks: {len(checks)} sections, "
              f"{bad} mismatches")


if __name__ == "__main__":
    import csv  # noqa: F401  (used by write_csv)
    main(sys.argv)