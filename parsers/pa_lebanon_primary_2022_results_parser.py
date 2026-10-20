#!/usr/bin/env python3
"""
Parse Lebanon County PA 2022 Primary (May 17, 2022) precinct results.

Sources (Electionware "Summary Results Report" precinct summaries, 60
precincts, pre-extracted with pdftotext -layout):
  1. "Lebanon PA Primary 2022 Precinct Summary-Without GOP Senate Race.pdf"
     (240 pages; every contest EXCEPT the Republican U.S. Senate race --
     the county's file pre-recount omits that race entirely)
  2. "Lebanon PA Primary 2022 Precinct Summary-ONLY GOP Senate Race.pdf"
     (60 pages; the Republican U.S. Senate precinct results as amended by
     the June 2022 statewide recount, generated 06/06/2022)

Each source page opens with two junk lines plus the "May 17, 2022" date
line, then the precinct label at column 0.  Contest sections are

      DEM UNITED STATES SENATOR
      Vote For 1
                                          TOTAL     Election     Mail Provision ...
                                                        Day In/Absent         al   ...
JOHN FETTERMAN                              41         28      13        0       0      0
Write-In Totals                              0             0      0          0        0        0
Total Votes Cast                            56         38      18        0         0      0
  Overvotes                                  0             0    0        0         0        0
  Undervotes                                 3             2    1        0         0        0

Differences from the 2022 general Lebanon source
(parsers/pa_lebanon_general_2022_results_parser.py):
  * contest headers carry the ballot party (DEM/REP); candidate rows carry
    NO party prefix (inversely from the general format);
  * no Statistics block -- this primary source has no Registered Voters /
    Ballots Cast rows, so none are emitted;
  * every contest row prints 6 numbers (Total, Election Day,
    Mail In/Absentee, Provisional, DEMOCRATIC, REPUBLICAN); the first four
    are used;
  * "MEMBER OF THE DEMOCRATIC COUNTY COMMITTEE" headers repeat the
    precinct name, which is stripped (local office; rows are emitted as
    printed, write-ins included);
  * "MEMBER OF THE DEMOCRATIC/REPUBLICAN STATE COMMITTEE" maps to the
    2022-repo anchor strings "Member of Democratic State Committee" /
    "Member of Republican State Committee".

Column mapping (2022 primary brief): TOTAL -> votes, "Election Day
In/Absentee" -> election_day, "Mail In/Absentee" -> mail, Provisional ->
provisional.

The Republican U.S. Senate rows come exclusively from source #2 (the
post-recount amended file); source #1 does not carry that contest.

Verification (2026-10):
  * every contest section's candidate + write-in rows sum exactly to the
    source's printed "Total Votes Cast" in all four columns (900/900);
  * county-sum cross-check vs the companion countywide Summary PDFs
    (same vintage): 78 contests x 4 columns, 0 mismatches;
  * vs the ENR anchor (2022/20220517__pa__primary__county.csv) and the
    DOS certified primary precinct-returns bulk file (county 38): the
    two county PDFs are the 06/06/2022 3:35 PM print, which is short of
    the certified values in 42 precinct cells (8 precincts, each -1 or
    -2, all named candidates, both parties -- the same late-counted
    pattern as contemporaneous counties).  Contest-sum residuals vs the
    anchor total -67 named votes (largest single: Governor/U.S. Senate
    DEM -7 on Shapiro/Fetterman; GOP Senate -1 each on Barnette/Oz/
    Sands at Millcreek X2 and Union Lickdale).  The DOS bulk file has
    no vote-type breakdown, so adopting its corrected totals would
    break the election_day/mail/provisional columns' internal sums; the
    county's own print (internally consistent, and exactly tied to the
    companion countywide Summary) is kept, residuals documented here.

Usage:
    python parsers/pa_lebanon_primary_2022_results_parser.py \
        <main.pdf-or-txt> <gop-senate-recount.pdf-or-txt> <output.csv>
"""

import os
import re
import subprocess
import sys
import tempfile
from collections import defaultdict

COUNTY = "Lebanon"

FIELDNAMES = ["county", "precinct", "office", "district", "party",
              "candidate", "votes", "election_day", "mail", "provisional"]

VOTEFOR_RE = re.compile(r"^Vote For\s+(\d+)$", re.IGNORECASE)
# a data row: label + at least 4 numbers; extra (party-column) numbers
# after the 4th are ignored
ROW_RE = re.compile(r"^(.*?)\s+(\d[\d,]*)\s+(\d[\d,]*)\s+(\d[\d,]*)"
                    r"\s+(\d[\d,]*)(?:\s.*)?$")
PAGE_DATE_RE = re.compile(r"^May 17, 2022\b")
PAGE_JUNK_RE = re.compile(
    r"^(Summary Results Report\b|Precinct Summary Results Report\b"
    r"|2022 Primary$|Electionware County$)")
FOOTER_JUNK_RE = re.compile(
    r"^(Precinct Summary - |Report generated with Electionware)")
SKIP_ROW_RE = re.compile(r"^(Total Votes Cast|Overvotes|Undervotes)\b",
                         re.IGNORECASE)
CONTEST_RE = re.compile(r"^(DEM|REP)\s+(.+)$", re.IGNORECASE)


def title(name):
    """Title-case an ALL-CAPS candidate name."""
    specials = {"MCSWAIN": "McSwain", "MCCORMICK": "McCormick",
                "DIGIULIO": "DiGiulio", "DEISSEROTH": "Deisseroth"}
    out = []
    for w in name.split():
        key = w.replace(".", "").replace(",", "")
        if key.upper() in specials:
            out.append(specials[key.upper()])
            continue
        pieces = re.split(r"([\-'])", w)
        out.append("".join(p.capitalize() if p else p
                           for p in [x for x in pieces]))
    return " ".join(out)


def map_contest(header, precinct):
    """(office, district, candidate-party-code) for a contest header."""
    hu = header.upper()
    if hu == "UNITED STATES SENATOR":
        return ("U.S. Senate", "", None)
    if hu == "GOVERNOR":
        return ("Governor", "", None)
    if hu == "LIEUTENANT GOVERNOR":
        return ("Lieutenant Governor", "", None)
    m = re.match(r"^REPRESENTATIVE IN CONGRESS (\d+)(ST|ND|RD|TH) DISTRICT$",
                 hu)
    if m:
        return ("U.S. House", m.group(1), None)
    m = re.match(r"^SENATOR IN THE GENERAL ASSEMBLY (\d+)(ST|ND|RD|TH)"
                 r"( DISTRICT)?$", hu)
    if m:
        return ("State Senate", m.group(1), None)
    m = re.match(r"^REPRESENTATIVE IN THE GENERAL ASSEMBLY (\d+)"
                 r"(ST|ND|RD|TH)( DISTRICT)?$", hu)
    if m:
        return ("State House", m.group(1), None)
    m = re.match(r"^MEMBER OF THE (DEMOCRATIC|REPUBLICAN) STATE COMMITTEE$",
                 hu)
    if m:
        word = m.group(1).capitalize()   # DEMOCRATIC -> Democratic
        return ("Member of %s State Committee" % word, "", None)
    m = re.match(r"^MEMBER OF THE DEMOCRATIC COUNTY COMMITTEE", hu)
    if m:
        # header repeats the precinct name; strip it
        office = "Member of the Democratic County Committee"
        return (office, "", None)
    raise SystemExit(f"unrecognized contest header: {header!r} "
                     f"(precinct {precinct!r})")


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


def parse(text, source_name):
    """Parse one Electionware primary precinct-summary text.

    Returns (rows, warnings, checks).  rows carry the contest party in
    the party field (taken from the DEM/REP contest prefix).
    """
    lines = text.splitlines()

    rows = []
    warnings = []

    def warn(msg):
        warnings.append(f"[{source_name}] {msg}")

    # --- pass 1: precinct labels (nonblank line after the page date line).
    # Every page of the report repeats its precinct's label, so keep the
    # first occurrence in document order.
    seen = set()
    precincts = []
    for i, l in enumerate(lines):
        if PAGE_DATE_RE.match(l.strip()):
            for j in range(i + 1, min(i + 5, len(lines))):
                t = lines[j].strip()
                if not t or PAGE_JUNK_RE.match(t):
                    continue
                if re.search(r"\d", t) or FOOTER_JUNK_RE.match(t):
                    warn(f"unexpected page-top line: {t[:60]!r}")
                break
            else:
                t = ""
            if t and t not in seen:
                seen.add(t)
                precincts.append(t)
    labels = set(precincts)
    if len(labels) != len(precincts):
        raise SystemExit(f"duplicate precinct labels: {sorted(labels)}")

    # --- pass 2: mark contest-header lines ("DEM X"/"REP X" + "Vote For N")
    contest_header_idx = set()
    for i, l in enumerate(lines):
        s = l.strip()
        if not s or not CONTEST_RE.match(s) or s in labels:
            continue
        j = i + 1
        while j < len(lines) and not lines[j].strip():
            j += 1
        if j < len(lines) and VOTEFOR_RE.match(lines[j].strip()):
            contest_header_idx.add(i)

    precinct = None
    contest = None
    n_contests = 0
    # (precinct, office, district) -> [sum_total, sum_ed, sum_mail, sum_prov]
    contest_sums = defaultdict(lambda: [0, 0, 0, 0])
    contest_totals = {}   # (precinct, office, district) -> print totals

    for i, l in enumerate(lines):
        s = l.strip()
        if not s or FOOTER_JUNK_RE.match(s) or PAGE_JUNK_RE.match(s):
            continue
        if PAGE_DATE_RE.match(s):
            # the label is the next non-junk line
            precinct = None
            continue
        if s in labels:
            precinct = s
            continue
        if i in contest_header_idx:
            pm = CONTEST_RE.match(s)
            party_prefix = pm.group(1).upper()
            header = pm.group(2).strip()
            office, district, _ = map_contest(header, precinct)
            contest = (office, district, party_prefix)
            n_contests += 1
            continue
        if VOTEFOR_RE.match(s):
            continue
        if precinct is None or contest is None:
            if re.search(r"\d", s):
                warn(f"row outside any contest: {s[:70]!r}")
            continue
        if SKIP_ROW_RE.match(s):
            m = ROW_RE.match(s)
            if m and "Total Votes Cast" in s:
                contest_totals[(precinct,) + contest] = \
                    [int(v.replace(",", "")) for v in m.groups()[1:5]]
            continue

        m = ROW_RE.match(s)
        if not m:
            if not re.search(r"\d", s):
                continue  # wrapped column-header fragments
            warn(f"unrecognized row in contest {contest}: {s[:70]!r}")
            continue

        head = m.group(1).strip()
        vals = [int(v.replace(",", "")) for v in m.groups()[1:5]]
        office, district, party = contest
        if re.match(r"^Write-?In Totals$", head, re.IGNORECASE):
            rows.append([COUNTY, precinct, office, district, party,
                         "Write Ins", *vals])
        else:
            rows.append([COUNTY, precinct, office, district, party,
                         title(head), *vals])
        k = (precinct, office, district, party)
        for c, v in enumerate(vals):
            contest_sums[k][c] += v

    # ---- arithmetic checks ------------------------------------------------
    mismatches = []
    for k, sums in sorted(contest_sums.items()):
        printed = contest_totals.get(k)
        if printed is None:
            warn(f"{k}: no 'Total Votes Cast' row found")
            continue
        for c, colname in enumerate(["votes", "election_day", "mail",
                                     "provisional"]):
            if sums[c] != printed[c]:
                mismatches.append((k, colname, sums[c], printed[c]))

    return rows, warnings, {
        "precincts": precincts,
        "n_contests": n_contests,
        "mismatches": mismatches,
        "contest_totals": contest_totals,
    }


def main():
    if len(sys.argv) != 4:
        sys.exit(f"Usage: {sys.argv[0]} <main-input> <gop-senate-recount-"
                 f"input> <output.csv>")
    main_src, recount_src, dst = sys.argv[1:4]

    all_rows = []
    warnings = []
    n_contests = 0
    n_precincts = set()
    mismatches = []
    for src in (main_src, recount_src):
        rows, warns, checks = parse(load_text(src),
                                    os.path.basename(src))
        all_rows.extend(rows)
        warnings.extend(warns)
        n_contests += checks["n_contests"]
        n_precincts.update(checks["precincts"])
        mismatches.extend(checks["mismatches"])

    with open(dst, "w", newline="", encoding="utf-8") as fh:
        import csv
        w = csv.writer(fh)
        w.writerow(FIELDNAMES)
        w.writerows(all_rows)

    print(f"wrote {len(all_rows)} rows -> {dst} "
          f"({len(n_precincts)} precincts, {n_contests} contest sections)")
    if mismatches:
        for (p, office, district), col, got, want in mismatches:
            print(f"MISMATCH {p} | {office} {district} | {col}: rows sum "
                  f"{got} != printed totals {want}")
    else:
        print("arithmetic: every contest's candidate+write-in rows sum to "
              "the printed 'Total Votes Cast' (votes/election_day/mail/"
              "provisional)")
    for w in warnings:
        print("WARN:", w)


if __name__ == "__main__":
    main()