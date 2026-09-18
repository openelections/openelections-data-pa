#!/usr/bin/env python3
"""Tioga County, PA 2023 Municipal Primary (May 16, 2023) — county-level
and precinct results.

Source: Tioga_County_Official_Results_2023_Primary.txt — pdftotext -layout
extract of Tioga's Electionware "Summary Results Report" (364 pages).  The
file concatenates TWO reports:

  * the county "Election Summary" (64 pages, footers "Election Summary -
    ... N of 64") — the county-level summary; parsed in the default mode
    into the county-level CSV,
  * a per-municipality "Precinct Summary" (300 pages, footers
    "Precinct Summary - ... N of 300", 41 municipality sections) — parsed
    with `--precinct` into the 10-column precinct CSV
    (2023/counties/20230516__pa__primary__tioga__precinct.csv).

Usage:
    python parsers/pa_tioga_primary_2023_results_parser.py \\
        <input.txt-or-.pdf> <output.csv>            # county-level (9 col)
    python parsers/pa_tioga_primary_2023_results_parser.py --precinct \\
        <input.txt-or-.pdf> <output.csv>            # precinct (10 col)

Wrapper notes (shared engine parsers/pa_2023_summary_common.py is used
unmodified; same approach as pa_clinton_primary_2023_results_parser.py):

- Contest headers carry the party as a prefix ("DEM JUSTICE OF THE
  SUPREME COURT").  parse_esr's STATISTICS scanner would never reach the
  contest section through that prefix (its break regex is anchored to the
  bare office words), so the wrapper pre-strips the "DEM "/"REP " prefix
  from the contest-header lines; map_contest pops the recorded parties
  back off in file order.
- Per-party statistics lines ("Registered Voters - DEMOCRATIC",
  "Ballots Cast - REPUBLICAN", ...) would trip parse_esr's STATISTICS
  break regex ("register" matches "Registered"), ending the scan before
  "Ballots Cast - Total"/"Ballots Cast - Blank" are read; the wrapper
  blanks those lines (repo convention records only totals).
- "Precincts Reporting N of M" footer lines inside each contest block
  would otherwise be emitted as bogus "Precincts Reporting" candidate
  rows by parse_esr; the wrapper drops them.
- Candidate rows carry no per-row party token in an Electionware primary
  summary, so each contest's party is stamped onto its candidate rows and
  its Write-ins row afterwards (DEM and REP sections of one contest share
  the same (office, district) key, so party-empty Write-ins rows would
  collide in the duplicate_entries data test).  Contest blocks are counted
  from the text because DEM and REP contests
  share the same (office, district) key.
- Offices/districts follow the Tioga 2023 general conventions
  (parsers/pa_tioga_general_2023_results_parser.py + the general precinct
  CSV): "MOC4/4YR <boro>" -> Borough Council (4 Year), "SUP 6YR <twp>" ->
  Township Supervisor (6 Year), "AUDITOR nYR" -> Township Auditor (n
  Year) for townships and boroughs alike, "TAX COL 2YR" -> Tax Collector
  (term dropped, as the general file did), "MDJ 04-3-02" ->
  Magisterial District Judge | 04-3-02, "REGISTER RECORDER CLERK" ->
  Register and Recorder.  School-director headers carry the region in a
  trailing "Region N" token; term suffixes are appended only for
  non-4-year seats, matching the general file's labels for the same seats
  ("School Director Region 3 (2 Year)", "School Director (2 Year)").
"""

import csv
import re
import sys
import tempfile
from collections import defaultdict

from pa_2023_summary_common import (is_junk, nums_from, parse_esr,
                                   text_from_pdf, write_csv)

COUNTY = "Tioga"

EXACT_OFFICES = {
    "JUSTICE OF THE SUPREME COURT": ("Justice of the Supreme Court", ""),
    "JUDGE OF THE SUPERIOR COURT": ("Judge of the Superior Court", ""),
    "JUDGE OF THE COMMONWEALTH COURT": ("Judge of the Commonwealth Court", ""),
    "COUNTY COMMISSIONER": ("County Commissioner", ""),
    "COUNTY AUDITOR": ("County Auditor", ""),
    "REGISTER RECORDER CLERK": ("Register and Recorder", ""),
    "DISTRICT ATTORNEY": ("District Attorney", ""),
    "CORONER": ("Coroner", ""),
}

MDJ_RE = re.compile(r"^MDJ (\d{2}-\d-\d{2})$")
SUP_RE = re.compile(r"^SUP (\d)YR (.+)$")
AUD_RE = re.compile(r"^AUDITOR (\d)YR (.+)$")
TAX_RE = re.compile(r"^TAX COL (\d)YR (.+)$")
# "MOC4/4YR Westfield Borough" / "MOC3/4YR ..." (seats/term) and the
# seat-only-term form "MOC2YR <boro>" (2-year term)
MOC_RE = re.compile(r"^MOC(\d)(?:/(\d))?YR (.+)$")
# "SD N/T REG 1/3 Region 1" / "SD S/T REG 1/3 2/4YR Region 1" / etc.
SD_REG_RE = re.compile(r"^SD (N/T|S/T) (.+)$")
# "SD WELLSBORO 5/4YR Wellsboro Area" (at-large seats)
SD_LARGE_RE = re.compile(r"^SD WELLSBORO (\d)/(\d)YR (.+)$")
# "GALETON AREA SCHOOL Region 2" / "CANTON AREA SCHOOL Region 3"
SCHOOL_BARE_RE = re.compile(r"^(GALETON|CANTON) AREA SCHOOL Region (\d)$")

TERM_YEARS = {"1": 1, "2": 2, "4": 4, "6": 6}

SCHOOLS = {"N/T": "Northern Tioga", "S/T": "Southern Tioga",
           "WELLSBORO": "Wellsboro Area", "GALETON": "Galeton Area",
           "CANTON": "Canton Area"}

PARTY_PREFIX_RE = re.compile(r"^(DEM|REP)\s+(.+)$", re.I)

# Per-party statistics / turnout lines (see module docstring).
PER_PARTY_STATS_RE = re.compile(
    r"^(Registered Voters|Ballots Cast|Voter Turnout) - "
    r"(DEMOCRATIC|REPUBLICAN|NONPARTISAN|GREEN|LIBERTARIAN)\b", re.I)

PRECINCTS_REPORTING_RE = re.compile(r"^Precincts Reporting\b", re.I)

VOTEFOR_RE = re.compile(r"^vote for (\d+)$", re.I)

# the per-municipality "Precinct Summary" report appended after the
# county "Election Summary" pages (parsed only with --precinct)
ELECTION_SUMMARY_FOOTER_RE = re.compile(r"Election Summary - \d")
PRECINCT_FOOTER_RE = re.compile(r"Precinct Summary - \d")

# lines that never produce a CSV row inside a contest block
SKIP_ROW_RE = re.compile(
    r"^(write-in:|not assigned|contest totals|total votes cast|"
    r"over ?votes|under ?votes)", re.I)


def _muni(raw):
    """Normalize a Tioga municipality name from a contest header."""
    s = raw.strip()
    s = re.sub(r"\btownship\b", "Township", s)
    s = re.sub(r"\bBoro(?![a-z])", "Borough", s)
    # duplicated trailing municipality ("Jackson Township Jackson Township")
    if " " in s:
        parts = s.split()
        n = len(parts) // 2
        if parts[:n] == parts[n:]:
            s = " ".join(parts[:n])
    return s


def _school_region(header):
    """School Director contests; (office, district) or None.

    Region seats: office "School Director Region N" plus " (N Year)" only
    for non-4-year terms (the general file leaves 4-year seats
    unsuffixed).  At-large Wellsboro seats mirror the general labels too.
    """
    h = header
    if h.startswith("SD N/T ") or h.startswith("SD S/T "):
        district = SCHOOLS[h.split()[1]]
        rest = h.split(None, 2)[2]
        region = None
        m = re.search(r"\bRegion (\d+)\s*$", rest)
        if m:
            region = m.group(1)
        else:
            return None
        term = None
        t = re.search(r"(\d)/(\d)YR\b", rest)
        if t:
            term = t.group(2)
        office = f"School Director Region {region}"
        if term and term != "4":
            office += f" ({TERM_YEARS[term]} Year)"
        return office, district
    m = SD_LARGE_RE.match(h)
    if m:
        office = "School Director"
        if m.group(2) != "4":
            office += f" ({TERM_YEARS[m.group(2)]} Year)"
        return office, SCHOOLS["WELLSBORO"]
    m = SCHOOL_BARE_RE.match(h)
    if m:
        return f"School Director Region {m.group(2)}", SCHOOLS[m.group(1)]
    return None


def classify(header):
    """(office, district) for a contest header with the party stripped."""
    h = re.sub(r"\s+", " ", header.strip())
    if h in EXACT_OFFICES:
        return EXACT_OFFICES[h]
    m = MDJ_RE.match(h)
    if m:
        return "Magisterial District Judge", m.group(1)
    m = SUP_RE.match(h)
    if m:
        return f"Township Supervisor ({TERM_YEARS[m.group(1)]} Year)", \
            _muni(m.group(2))
    m = AUD_RE.match(h)
    if m:
        return f"Township Auditor ({TERM_YEARS[m.group(1)]} Year)", \
            _muni(m.group(2))
    m = TAX_RE.match(h)
    if m:
        return "Tax Collector", _muni(m.group(2))
    m = MOC_RE.match(h)
    if m:
        term = m.group(2) or m.group(1)
        rest = m.group(3).strip()
        w = re.match(r"^Wellsboro Ward (One|Two)$", rest)
        if w:
            return f"Borough Council ({TERM_YEARS[term]} Year)", \
                f"Wellsboro Borough Ward {w.group(1)}"
        return f"Borough Council ({TERM_YEARS[term]} Year)", _muni(rest)
    s = _school_region(h)
    if s:
        return s
    raise SystemExit(f"unrecognized contest header: {header!r}")


def preprocess(path):
    """Prepare the ESR text for parse_esr.

    - cuts the text at the end of the county "Election Summary" pages
      (the trailing per-municipality "Precinct Summary" report is out of
      scope for the county-level file),
    - drops the "Precincts Reporting N of M" lines inside contest blocks,
    - strips the DEM/REP prefix off each contest-header line, recording
      per contest, in file order, (party, office, district) and the
      number of CSV rows its block emits,
    - blanks per-party statistics / turnout lines.
    Returns (temp text path, blocks).
    """
    with open(path, encoding="utf-8") as fh:
        lines = [l.rstrip() for l in fh]

    # keep only the county "Election Summary" pages
    footers = [i for i, l in enumerate(lines)
               if ELECTION_SUMMARY_FOOTER_RE.search(l)]
    if footers:
        lines = lines[:footers[-1] + 1]

    lines = ["" if PRECINCTS_REPORTING_RE.match(l.strip()) else l
             for l in lines]

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
    # line and the next one that parse_esr turns into CSV rows).  The next
    # block's header line is inside this span (it sits just above the next
    # "Vote For" line) and parse_esr skips it as a header -- skip it here
    # too, or its trailing digits ("... Region 3") would be miscounted as
    # a candidate row of the previous contest.
    header_line_idx = set(header_idx.values())
    keys = list(header_idx)
    for bi, k in enumerate(keys):
        end = keys[bi + 1] if bi + 1 < len(keys) else len(lines)
        n = 0
        for j2 in range(k + 1, end):
            l = lines[j2]
            s = l.strip()
            if j2 in header_line_idx:
                continue
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

    # neutralize per-party statistics / turnout lines
    for i, l in enumerate(lines):
        if PER_PARTY_STATS_RE.match(l.strip()) \
                or re.match(r"^Voter Turnout - ", l.strip(), re.I):
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
    """Stamp each contest's party onto its rows (candidates AND Write-ins).

    In a primary, DEM and REP sections of the same contest share the same
    (office, district) key, so party-empty Write-ins rows would collide as
    duplicates in the duplicate_entries data test; the contest party is
    stamped on Write-ins rows too."""
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
            row[3] = b["party"]
            b.setdefault("party_rows", []).append(row)
        ptr += b["nrows"]
    if ptr != len(idxs):
        raise SystemExit(
            f"contest blocks cover {ptr} of {len(idxs)} non-meta rows")


def check_contest_totals(R, blocks):
    """Reconcile each contest's candidate+write-in rows against the
    source's Total Votes Cast for that contest."""
    bad = []
    for b in blocks:
        rows = b.get("party_rows") or []
        agg = sum(int(r[5]) for r in rows if r[4] not in
                  ("Overvotes", "Undervotes"))
        key = (b["office"], b["district"], b["party"])
        tot = R.totals.get(key)
        if tot is None:
            bad.append((key, agg, None))
        elif agg != tot:
            bad.append((key, agg, tot))
    return bad


def main():
    args = sys.argv[1:]
    if "--precinct" in args:
        args = [a for a in args if a != "--precinct"]
        if len(args) < 2:
            sys.exit(f"Usage: {sys.argv[0]} [--precinct] <input.pdf|txt> "
                     f"<output.csv>")
        src, dst = args[0], args[1]
        if src.lower().endswith(".pdf"):
            src = text_from_pdf(src)
        parse_precinct(src, dst)
        return
    if len(args) < 2:
        sys.exit(f"Usage: {sys.argv[0]} [--precinct] <input.pdf|txt> "
                 f"<output.csv>")
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


# --------------------------------------------------------------------------
# --precinct mode: the per-municipality "Precinct Summary" report
# --------------------------------------------------------------------------

PRECINCT_FIELDNAMES = ["county", "precinct", "office", "district", "party",
                       "candidate", "votes", "election_day", "mail",
                       "provisional"]


def parse_precinct(path, dst):
    """Parse the appended per-municipality "Precinct Summary" report into
    the 10-column precinct CSV.  Each of the 41 municipality sections
    carries its own Statistics block (emitted as Registered Voters /
    Ballots Cast / Ballots Cast - Blank rows, per the 2023 general
    precinct-file convention) followed by that municipality's DEM and REP
    contest blocks.  Write-ins rows carry the contest party (per the
    primary precinct convention); named "Write-In: NAME" detail rows and
    "Not Assigned" are folded into the Write-In Totals aggregate."""
    with open(path, encoding="utf-8") as fh:
        lines = [l.rstrip() for l in fh]

    esf = [i for i, l in enumerate(lines)
           if ELECTION_SUMMARY_FOOTER_RE.search(l)]
    psf = [i for i, l in enumerate(lines)
           if PRECINCT_FOOTER_RE.search(l)]
    if not psf:
        raise SystemExit("no 'Precinct Summary' report in the source; "
                         "drop --precinct for county-level mode")
    start = esf[-1] + 1 if esf else 0
    sec = lines[start:psf[-1] + 1]

    # contest-header lines (the nonblank line above each "Vote For" line)
    hdr_idx = set()
    for i, l in enumerate(sec):
        if VOTEFOR_RE.match(l.strip()):
            j = i - 1
            while j >= 0 and not sec[j].strip():
                j -= 1
            if j >= 0:
                hdr_idx.add(j)

    def nums_from_row(s):
        return nums_from(s.split())

    rows = []
    totals = {}          # (muni, office, district, party) -> Total Votes Cast
    agg = defaultdict(int)
    muni = None
    current = None

    for i, l in enumerate(sec):
        s = l.strip()
        m = VOTEFOR_RE.match(s)
        if m:
            j = i - 1
            while j >= 0 and not sec[j].strip():
                j -= 1
            hm = PARTY_PREFIX_RE.match(sec[j].strip())
            if not hm or muni is None:
                raise SystemExit(
                    f"contest block outside a municipality: {sec[j]!r}")
            office, district = classify(hm.group(2))
            current = (muni, office, district, hm.group(1).upper(),
                       int(m.group(1)))
            continue
        if not s or i in hdr_idx or is_junk(s):
            continue
        if not any(c.isdigit() for c in s):
            # municipality section header: no digits, followed by Statistics
            nxt = [sec[k].strip() for k in range(i + 1, min(i + 5, len(sec)))
                   if sec[k].strip()]
            if nxt and nxt[0].upper().startswith("STATISTICS"):
                muni = s
                current = None
            continue
        if current is None:
            # Statistics block of this municipality
            if s.upper().startswith("REGISTERED VOTERS - TOTAL"):
                n = nums_from_row(s)
                if n:
                    rows.append([COUNTY, muni, "Registered Voters", "", "",
                                 "", n[0], "", "", ""])
            elif s.upper().startswith("BALLOTS CAST - TOTAL"):
                n = nums_from_row(s)
                rows.append([COUNTY, muni, "Ballots Cast", "", "", "",
                             n[0], n[1] if len(n) > 1 else "",
                             n[2] if len(n) > 2 else "",
                             n[3] if len(n) > 3 else ""])
            elif s.upper().startswith("BALLOTS CAST - BLANK"):
                n = nums_from_row(s)
                rows.append([COUNTY, muni, "Ballots Cast - Blank", "", "",
                             "", n[0], n[1] if len(n) > 1 else "",
                             n[2] if len(n) > 2 else "",
                             n[3] if len(n) > 3 else ""])
            # per-party Registered Voters / Ballots Cast, Voter Turnout
            # lines are not emitted (repo convention records only totals)
            continue
        muni_c, office, district, party, vfn = current
        n = nums_from_row(s)
        if s.upper().startswith("WRITE-IN TOTALS"):
            rows.append([COUNTY, muni_c, office, district, party,
                         "Write-ins", int(n[0]),
                         n[1] if len(n) > 1 else "",
                         n[2] if len(n) > 2 else "",
                         n[3] if len(n) > 3 else ""])
            if n:
                agg[(muni_c, office, district, party)] += int(n[0])
        elif s.upper().startswith("WRITE-IN:") \
                or s.upper().startswith("NOT ASSIGNED") \
                or s.upper().startswith("CONTEST TOTALS") \
                or PRECINCTS_REPORTING_RE.match(s):
            pass  # detail rows fold into the Write-In Totals aggregate
        elif s.upper().startswith("TOTAL VOTES CAST"):
            totals[(muni_c, office, district, party)] = int(n[0]) if n else None
        else:
            name = re.sub(r"\s+", " ", re.sub(r"[\d,].*$", "", s)).strip()
            if name and n:
                total = int(n[0])
                ed = n[1] if len(n) > 1 else ""
                mi = n[2] if len(n) > 2 else ""
                pr = n[3] if len(n) > 3 else ""
                rows.append([COUNTY, muni_c, office, district, party,
                             name, total, ed, mi, pr])
                agg[(muni_c, office, district, party)] += total

    bad = [(k, agg[k], t) for k, t in totals.items() if agg[k] != t]
    if bad:
        for k, a, t in bad:
            print(f"WARNING {k[2]} {k[3]} {k[0]} | {k[1]}: rows sum {a} != "
                  f"Total Votes Cast {t}")
    else:
        print("contest totals check: all contests reconcile")

    with open(dst, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(PRECINCT_FIELDNAMES)
        w.writerows(rows)
    munis = {r[1] for r in rows}
    print(f"wrote {len(rows)} rows -> {dst} ({len(munis)} precincts, "
          f"{len(totals)} contest blocks)")


if __name__ == "__main__":
    main()