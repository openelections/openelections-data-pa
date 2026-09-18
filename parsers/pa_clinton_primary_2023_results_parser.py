#!/usr/bin/env python3
"""Clinton County, PA 2023 Municipal Primary -- county-level results.

Source: Clinton_County_Summary_Results_2023_Primary.txt (Electionware
"Summary Results Report" COUNTY summary; pdftotext -layout extract).
Clinton's 2023 primary source is countywide only -- no per-precinct file
exists -- so only the county-level CSV is produced.

Wrapper notes (all forced by quirks of the source; the shared engine in
pa_2023_summary_common.py is used unmodified):
- Contest headers carry the party as a prefix ("DEM JUSTICE OF THE
  SUPREME COURT").  parse_esr's STATISTICS scanner would never reach the
  contest section through that prefix (its break regex is anchored to the
  bare office words), so the wrapper pre-strips the "DEM "/"REP " prefix
  from the contest-header lines and remembers each contest's party in
  file order; map_contest pops the parties back off in that order.
- The per-party statistics lines ("Registered Voters - DEMOCRATIC",
  "Ballots Cast - REPUBLICAN", ...) would trip parse_esr's STATISTICS
  break regex ("register" matches the "Registered" prefix), ending the
  scan before "Ballots Cast - Total"/"Ballots Cast - Blank" are read;
  the wrapper renames those per-party stats lines so they are skipped.
- Candidate rows carry no per-row party token in an Electionware primary
  summary, so the contest party is stamped onto every candidate row of
  its contest afterwards, Write-ins rows included (the CI
  duplicate_entries test hashes all non-vote columns, so party-empty
  Write-ins rows would collide across the DEM and REP sections of the
  same office).  Contest blocks are counted from the text (rows each
  contest emits) because DEM and REP contests share the same
  (office, district) key and cannot be distinguished by key alone.
- Candidate names keep the ALL-CAPS spelling used by Clinton's 2023
  general precinct file (the shared CANON_NAMES title-case hits are
  reverted).
- Offices/districts follow the Clinton 2023 general conventions:
  "MAGISTERIAL DISTRICT JUDGE 25-3-01 DISTRICT I" -> Magisterial
  District Judge | 25-3-01.

Usage:
    python parsers/pa_clinton_primary_2023_results_parser.py \
        <input.txt|input.pdf> <output.csv>
"""

import re
import sys
import tempfile

from pa_2023_summary_common import (CANON_NAMES, is_junk, nums_from,
                                   parse_esr, text_from_pdf, write_csv)

COUNTY = "Clinton"

EXACT_OFFICES = {
    "JUSTICE OF THE SUPREME COURT": "Justice of the Supreme Court",
    "JUDGE OF THE SUPERIOR COURT": "Judge of the Superior Court",
    "JUDGE OF THE COMMONWEALTH COURT": "Judge of the Commonwealth Court",
    "SHERIFF": "Sheriff",
    "COUNTY COMMISSIONER": "County Commissioner",
    "COUNTY AUDITOR": "County Auditor",
    "DISTRICT ATTORNEY": "District Attorney",
    "PROTHONOTARY": "Prothonotary",
    "REGISTER AND RECORDER": "Register and Recorder",
}

MDJ_RE = re.compile(
    r"^MAGISTERIAL DISTRICT JUDGE\s+(\d[\d-]*)\s+DISTRICT\s+\S+$")

PARTY_PREFIX_RE = re.compile(r"^(DEM|REP)\s+(.+)$", re.I)

# Per-party statistics lines ("Registered Voters - DEMOCRATIC",
# "Ballots Cast - NONPARTISAN", "Voter Turnout - REPUBLICAN") -- not
# emitted as data (repo convention records only totals), and renamed here
# so parse_esr's STATISTICS break regex does not stop on "Registered
# Voters - <PARTY>" (the "register" alternative matches the "Registered"
# prefix) before the Ballots Cast totals have been read.
PER_PARTY_STATS_RE = re.compile(
    r"^(Registered Voters|Ballots Cast|Voter Turnout) - "
    r"(DEMOCRATIC|REPUBLICAN|NONPARTISAN|GREEN|LIBERTARIAN)\b", re.I)

VOTEFOR_RE = re.compile(r"^vote for \d+$", re.I)

# Candidate-row spellings that CANON_NAMES would title-case; Clinton's
# general file keeps ALL-CAPS names.
REVERT_CANON = {v: k for k, v in CANON_NAMES.items()
                if k in {"DANIEL MCCAFFERY", "CAROLYN CARLUCCIO",
                         "JILL BECK", "TIMIKA LANE", "MARIA BATTISTA",
                         "HARRY F SMAIL JR", "MATT WOLF", "MEGAN MARTIN"}}

# lines that never produce a CSV row inside a contest block
SKIP_ROW_RE = re.compile(
    r"^(write-in:|not assigned|contest totals|total votes cast|"
    r"over ?votes|under ?votes)", re.I)


def classify(header):
    """(office, district) for a contest header with the party stripped."""
    h = re.sub(r"\s+", " ", header.strip()).upper()
    m = MDJ_RE.match(h)
    if m:
        return "Magisterial District Judge", m.group(1)
    if h in EXACT_OFFICES:
        return EXACT_OFFICES[h], ""
    raise SystemExit(f"unrecognized contest header: {header!r}")


def preprocess(path):
    """Prepare the ESR text for parse_esr.

    - strips the DEM/REP prefix off each contest-header line,
    - renames per-party statistics lines,
    - records, per contest in file order, (party, office, district) and
      the number of CSV rows its block emits.
    Returns (temp text path, blocks).
    """
    with open(path, encoding="utf-8") as fh:
        lines = [l.rstrip() for l in fh]

    header_idx = {}
    for k, l in enumerate(lines):
        if re.match(r"^vote for \d+$", l.strip(), re.I):
            j = k - 1
            while j >= 0 and not lines[j].strip():
                j -= 1
            if j >= 0:
                header_idx[k] = j

    blocks = []
    for k, j in header_idx.items():
        m = PARTY_PREFIX_RE.match(lines[j].strip())
        if not m:
            raise SystemExit(
                f"contest header without DEM/REP prefix: {lines[j]!r}")
        office, district = classify(m.group(2))
        blocks.append({"party": m.group(1).upper(),
                       "office": office, "district": district})
        lines[j] = " " + m.group(2)

    # count emitted rows per contest block (lines between this "Vote For"
    # line and the next one that parse_esr turns into CSV rows)
    keys = list(header_idx)
    for bi, k in enumerate(keys):
        end = keys[bi + 1] if bi + 1 < len(keys) else len(lines)
        n = 0
        for l in lines[k + 1:end]:
            s = l.strip()
            if not s or SKIP_ROW_RE.match(s):
                continue
            if re.match(r"^vote for \d+$", s, re.I):
                continue
            if is_junk(s):
                continue
            toks = s.split()
            if len(toks) < 2 or not nums_from(toks[1:]):
                continue
            n += 1
        blocks[bi]["nrows"] = n

    # neutralize per-party statistics lines
    for i, l in enumerate(lines):
        if PER_PARTY_STATS_RE.match(l.strip()):
            lines[i] = ";" + l

    out = tempfile.NamedTemporaryFile(mode="w", suffix=".txt",
                                      delete=False, encoding="utf-8")
    out.write("\n".join(lines) + "\n")
    out.close()
    return out.name, blocks


def make_map_contest(blocks):
    queue = list(reversed(blocks))

    def map_contest(header):
        if not queue:
            raise SystemExit(
                f"more contest headers than DEM/REP prefixes: {header!r}")
        b = queue.pop()
        return b["office"], b["district"], b["party"]

    return map_contest


def stamp_party(R, blocks):
    """Stamp each contest's party onto its candidate rows (Write-ins rows
    included; see the wrapper notes) and revert CANON_NAMES title-casing
    to the source's ALL-CAPS spellings."""
    # meta rows (Ballots Cast - Blank is emitted during the STATISTICS
    # block, Registered Voters/Ballots Cast after the loop) never belong
    # to a contest block
    idxs = [i for i, r in enumerate(R.rows)
            if r[1] not in ("Registered Voters", "Ballots Cast",
                            "Ballots Cast - Blank")]
    ptr = 0
    for b in blocks:
        if ptr + b["nrows"] > len(idxs):
            raise SystemExit(f"row spans exceed parsed rows: {b!r}")
        span = [R.rows[i] for i in idxs[ptr:ptr + b["nrows"]]]
        for row in span:
            if row[1] != b["office"] or row[2] != b["district"]:
                raise SystemExit(
                    f"row block misalignment: {row} does not match "
                    f"{b['office']!r} | {b['district']!r}")
            # write-in aggregate rows carry the contest party too: the CI
            # duplicate_entries test hashes all non-vote columns and the
            # DEM/REP sections of the same office would otherwise collide
            row[3] = b["party"]
            if row[4] in REVERT_CANON:
                row[4] = REVERT_CANON[row[4]]
            b.setdefault("party_rows", []).append(row)
        ptr += b["nrows"]
    if ptr != len(idxs):
        raise SystemExit(
            f"contest blocks cover {ptr} of {len(idxs)} non-meta rows")


def check_contest_totals(R, blocks):
    """Reconcile each contest's candidate+write-in rows against the
    source's Total Votes Cast for that contest.  A contest whose report
    section is split across pages appears as several blocks; aggregates
    and totals are keyed per unique (office, district, party)."""
    agg = {}
    for b in blocks:
        key = (b["office"], b["district"], b["party"])
        for r in b.get("party_rows") or []:
            if r[4] in ("Overvotes", "Undervotes"):
                continue
            agg[key] = agg.get(key, 0) + int(r[5])
    bad = []
    for key, tot in R.totals.items():
        if key not in agg:
            bad.append((key, None, tot))
        elif agg[key] != tot:
            bad.append((key, agg[key], tot))
        del agg[key]
    for key, a in agg.items():
        bad.append((key, a, None))
    return bad


def main():
    args = sys.argv[1:]
    if len(args) < 2:
        sys.exit(f"Usage: {sys.argv[0]} <input.pdf|txt> <output.csv>")
    src, dst = args[0], args[1]
    if src.lower().endswith(".pdf"):
        src = text_from_pdf(src)
    tmp, blocks = preprocess(src)
    R = parse_esr(tmp, COUNTY, make_map_contest(blocks),
                  titlecase_names=False)
    stamp_party(R, blocks)
    n = write_csv(dst, R)
    print(f"wrote {n} rows -> {dst}")
    unique = {(b["office"], b["district"], b["party"]) for b in blocks}
    print(f"contests parsed: {len(blocks)} ({len(unique)} unique)")
    bad = check_contest_totals(R, blocks)
    if bad:
        for (office, dist, party), a, t in bad:
            print(f"WARNING {party} {office} | {dist}: rows sum {a} != "
                  f"Total Votes Cast {t}")
    else:
        print("contest totals check: all contests reconcile")


if __name__ == "__main__":
    main()