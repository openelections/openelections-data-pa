#!/usr/bin/env python3
"""Huntingdon County, PA 2023 Municipal Primary -- county-level results.

Source: Huntingdon_County_Official_Results_2023_Primary.txt (Electionware
"Summary Results Report" COUNTY summary; pdftotext -layout extract).
Huntingdon's 2023 primary source is countywide only -- no per-precinct
file exists -- so only the county-level CSV is produced.

Wrapper notes (all forced by quirks of the source; the shared engine in
pa_2023_summary_common.py is used unmodified; the wrapper pattern follows
pa_clinton_primary_2023_results_parser.py):
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
  its contest afterwards.  Write-ins rows carry the contest party too
  (Berks/Clinton precedent): the duplicate_entries data test hashes the
  non-vote columns, so party-empty Write-ins rows from the DEM and REP
  halves of the same office would collide.  Contest blocks are counted
  from the text (rows each contest emits) because DEM and REP contests
  share the same (office, district) key and cannot be distinguished by
  key alone.
- The source has NO per-contest "Total Votes Cast" rows and no
  Overvotes/Undervotes rows, so contest reconciliation here is the
  write-in arithmetic ("Write-In Totals" == sum of the "Write-In: NAME"
  detail rows + "Not Assigned") plus the party-ballot arithmetic against
  the per-party Ballots Cast lines from the STATISTICS block.

Office/district conventions mirror the county's 2023 general files
(``2023/counties/20231107__pa__general__huntingdon__county.csv`` /
``parsers/pa_huntingdon_general_2023_results_parser.py``):
  - "MINORITY INSPECTOR <MUNI>" / "MAJORITY INSPECTOR <MUNI>": the
    municipality stays embedded in the office (general convention), e.g.
    "Minority Inspector Birmingham", "Majority Inspector Shirley/shirley";
  - other local headers "<Office> [NYR] <Muni>": municipality becomes the
    district ("CONSTABLE BARREE TOWNSHIP" -> "Barree"), slash forms mirror
    the general file ("MOUNT UNION/2ND WARD" -> "Mount Union 2",
    "HOPEWELL/PUTTSTOWN" -> "Hopewell/puttstown",
    "SHIRLEY TWP/VALLEY PT" -> "Shirley/valley Point");
  - "MAGISTERIAL DISTRICT JUDGE DISTRICT 20-3-02" -> Magisterial
    District Judge | 20-3-02 (the county summary runs ONE contest per
    district, unlike the general's per-precinct sections);
  - school headers mirror the general: "SOUTHERN HUNTINGDON COUNTY
    SCHOOL EASTERN REGION" -> School Director | Southern Huntingdon -
    Eastern Region, "TUSSEY MOUNTAIN SCHOOL REGION 1" -> School Director
    Region 1 | Tussey Mountain, "JUNIATA VALLEY SCHOOL AT LARGE" ->
    School Director | Juniata Valley;
  - term tokens (2YR/4YR/6YR) that distinguish separate contests in this
    source are kept on the office ("Borough Council (4 Year)",
    "Township Supervisor (6 Year)", "Auditor (2 Year)", "Mayor (2 Year)",
    "School Director Region 2 (4 Year)") per the primary brief/SPEC --
    the county's general file had no term tokens (it disambiguated
    duplicate contests with "(2)" occurrence suffixes instead), and
    dropping the token here would collide the DEM/2yr and DEM/4yr
    contests on one (office, district) key.

Usage:
    python parsers/pa_huntingdon_primary_2023_results_parser.py \
        <input.txt|input.pdf> <output.csv>
"""

import re
import sys
import tempfile

from pa_2023_summary_common import (is_junk, nums_from, parse_esr,
                                   text_from_pdf, write_csv)

COUNTY = "Huntingdon"

EXACT_OFFICES = {
    "JUSTICE OF THE SUPREME COURT": "Justice of the Supreme Court",
    "JUDGE OF THE SUPERIOR COURT": "Judge of the Superior Court",
    "JUDGE OF THE COMMONWEALTH COURT": "Judge of the Commonwealth Court",
    "JUDGE OF THE COURT OF COMMON PLEAS": "Judge of the Court of Common Pleas",
    "COUNTY COMMISSIONER": "County Commissioner",
    "COUNTY AUDITOR": "County Auditor",
    "DISTRICT ATTORNEY": "District Attorney",
    "TREASURER": "Treasurer",
    "CORONER": "Coroner",
    "REGISTER OF WILLS & RECORDER OF DEEDS": "Register and Recorder",
}

PARTY_PREFIX_RE = re.compile(r"^(DEM|REP)\s+(.+)$", re.I)

# Per-party statistics lines -- neutralized (prefixed with ";") so
# parse_esr's STATISTICS break regex ("register" matches "Registered",
# "county" would stop the scan) does not end the scan before
# "Ballots Cast - Total" / "Ballots Cast - Blank" are read.  Per-party
# ballots-cast rows are not recorded in the CSV (repo convention); the
# wrapper reads their values from the raw text for the party-ballot
# arithmetic check.
PER_PARTY_STATS_RE = re.compile(
    r"^(Registered Voters|Ballots Cast|Voter Turnout) - "
    r"(DEMOCRATIC|REPUBLICAN|NONPARTISAN|GREEN|LIBERTARIAN)\b", re.I)

VOTEFOR_RE = re.compile(r"^vote for \d+$", re.I)

# lines that never produce a CSV row inside a contest block
SKIP_ROW_RE = re.compile(
    r"^(write-in:|not assigned|contest totals|total votes cast|"
    r"over ?votes|under ?votes)", re.I)

MDJ_RE = re.compile(
    r"^MAGISTERIAL DISTRICT JUDGE DISTRICT (\d{2}-\d-\d{2})$")

_SUFFIX_RE = re.compile(r"\s+(TOWNSHIP|TOWNHIP|TWP|BOROUGH|BORO)$", re.I)
# _SUFFIX_RE covers the source's "TOWNSHIP" typo (missing S), matching the
# general file, which shortens the municipality to "Carbon" either way.

# slash-form district tails, exactly as the county's 2023 general file
# prints them (mirrors its title-casing quirks)
_SLASH_DISTRICT = {
    "HUNTINGDON/1ST DIST": "Huntingdon 1",
    "HUNTINGDON/3RD DIST": "Huntingdon 3",
    "HUNTINGDON/4TH DIST": "Huntingdon 4",
    "HUNTINGDON/5TH DIST": "Huntingdon 5",
    "HUNTINGDON/6TH DIST": "Huntingdon 6",
    "HUNTINGDON/2ND DIST": "Huntingdon 2",
    "MOUNT UNION/1ST WARD": "Mount Union 1",
    "MOUNT UNION/2ND WARD": "Mount Union 2",
    "MOUNT UNION/3RD WARD": "Mount Union 3",
    "SHIRLEY TWP/SHIRLEY": "Shirley/shirley",
    "SHIRLEY TWP/VALLEY PT": "Shirley/valley Point",
    "HOPEWELL/PUTTSTOWN": "Hopewell/puttstown",
    "HOPEWELL/HOPEWELL": "Hopewell/hopewell",
    "SHIRLEY/MOUNT UNION": "Shirley/mount Union",
}

# majority/minority-inspector office tails (municipality embedded in the
# office, district empty -- general-file convention).  These keep the
# ordinal words where the general file did.
_INSPECTOR_SLASH = {
    "SHIRLEY TWP/SHIRLEY": "Shirley/shirley",
    "SHIRLEY TWP/VALLEY PT": "Shirley/valley Point",
    "HOPEWELL/HOPEWELL": "Hopewell/hopewell",
    "HUNTINGDON/2ND DIST": "Huntingdon 2nd District",
    "HUNTINGDON/4TH DIST": "Huntingdon 4th District",
    "HUNTINGDON/5TH DIST": "Huntingdon 5th District",
    "MOUNT UNION/1ST WARD": "Mount Union 1st Ward",
}


def cap_muni(s):
    return " ".join(w.capitalize() for w in s.split())


def norm_district(t):
    """Municipality tail -> district, mirroring the 2023 general file."""
    t = re.sub(r"\s+", " ", t.strip()).upper()
    if t in _SLASH_DISTRICT:
        return _SLASH_DISTRICT[t]
    if t == "BROAD TOP CITY BORO":
        return "Broad Top"
    t = _SUFFIX_RE.sub("", t)
    return cap_muni(t)


def map_contest(header):
    """(office, district, party) for a pre-stripped contest header.

    The party prefix has already been stripped by preprocess(); the
    party slot is filled by the caller's queue (the header itself no
    longer carries it), so this returns party "" and the stamping pass
    assigns it.
    """
    h = re.sub(r"\s+", " ", header.strip()).upper()

    m = MDJ_RE.match(h)
    if m:
        return "Magisterial District Judge", m.group(1), ""
    if h in EXACT_OFFICES:
        return EXACT_OFFICES[h], "", ""

    m = re.match(r"^(MAJORITY|MINORITY) INSPECTOR (.+)$", h)
    if m:
        kind, tail = m.group(1), m.group(2).strip()
        tail = _INSPECTOR_SLASH.get(tail) or norm_district(tail)
        return f"{kind.capitalize()} Inspector {tail}", "", ""

    m = re.match(r"^SOUTHERN HUNTINGDON COUNTY SCHOOL "
                 r"(EASTERN|WESTERN|CENTRAL) REGION$", h)
    if m:
        return "School Director", \
            f"Southern Huntingdon - {m.group(1).capitalize()} Region", ""

    m = re.match(r"^(HUNTINGDON AREA|MOUNT UNION AREA|TUSSEY MOUNTAIN) "
                 r"SCHOOL REGION (\d)(?:\s*-\s*(\d)YR)?$", h)
    if m:
        area, region, term = m.group(1), m.group(2), m.group(3)
        area_name = {"HUNTINGDON AREA": "Huntingdon Area",
                     "MOUNT UNION AREA": "Mount Union Area",
                     "TUSSEY MOUNTAIN": "Tussey Mountain"}[area]
        office = "School Director Region " + region
        if term:
            office += f" ({term} Year)"
        return office, area_name, ""

    m = re.match(r"^TYRONE AREA SCHOOL AT LARGE(?:\s+(\d)YR)?$", h)
    if m:
        office = "School Director"
        if m.group(1):
            office += f" ({m.group(1)} Year)"
        return office, "Tyrone Area", ""

    m = re.match(r"^JUNIATA VALLEY SCHOOL AT LARGE$", h)
    if m:
        return "School Director", "Juniata Valley", ""

    # "AUDITOR - AT LARGE 6YR SHIRLEY TWP" -> "Auditor (6 Year)" |
    # "At Large Shirley" (the general file's "ATL ARGE" tail maps the
    # same municipality to "At Large Shirley")
    m = re.match(r"^(AUDITOR|MAYOR)\s+-\s+AT\s+LARGE\s+(.+)$", h)
    if m:
        office = "Auditor" if m.group(1) == "AUDITOR" else "Mayor"
        rest = m.group(2).strip()
        term = ""
        mt = re.match(r"^(2|4|6)YR\s+(.+)$", rest)
        if mt:
            term = mt.group(1)
            rest = mt.group(2).strip()
        if term:
            office += f" ({term} Year)"
        return office, "At Large " + norm_district(rest), ""

    # generic local offices: "<Office> [NYR] <Muni...>"
    m = re.match(r"^(BORO COUNCIL|TOWNSHIP SUPERVISOR|AUDITOR|MAYOR|"
                 r"TAX COLLECTOR|CONSTABLE|JUDGE OF ELECTION|"
                 r"INSPECTOR OF ELECTION)\s+(.+)$", h)
    if m:
        base, rest = m.group(1), m.group(2).strip()
        term = ""
        mt = re.match(r"^(2|4|6)YR\s+(.+)$", rest)
        if mt:
            term = mt.group(1)
            rest = mt.group(2).strip()
        office = {"BORO COUNCIL": "Borough Council",
                  "TOWNSHIP SUPERVISOR": "Township Supervisor",
                  "AUDITOR": "Auditor",
                  "MAYOR": "Mayor",
                  "TAX COLLECTOR": "Tax Collector",
                  "CONSTABLE": "Constable",
                  "JUDGE OF ELECTION": "Judge of Election",
                  "INSPECTOR OF ELECTION": "Inspector of Election"}[base]
        if term:
            office += f" ({term} Year)"
        district = norm_district(rest)
        return office, district, ""

    raise SystemExit(f"unrecognized contest header: {header!r}")


def preprocess(path):
    """Prepare the ESR text for parse_esr.

    - strips the DEM/REP prefix off each contest-header line,
    - renames per-party statistics lines,
    - records, per contest in file order, (party, office, district),
      the Vote-For count and the number of CSV rows its block emits,
    - collects the per-party Ballots Cast numbers from the raw
      STATISTICS block.
    Returns (temp text path, blocks, party_bc).
    """
    with open(path, encoding="utf-8") as fh:
        lines = [l.rstrip() for l in fh]

    header_idx = {}
    for k, l in enumerate(lines):
        if VOTEFOR_RE.match(l.strip()):
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
        party = m.group(1).upper()
        office, district, _ = map_contest(m.group(2))
        blocks.append({"party": party, "office": office,
                       "district": district, "votefor": 1})
        lines[j] = " " + m.group(2)

    keys = list(header_idx)
    header_line_set = set(header_idx.values())
    for bi, k in enumerate(keys):
        end = keys[bi + 1] if bi + 1 < len(keys) else len(lines)
        m = re.match(r"^vote for (\d+)$", lines[k].strip(), re.I)
        if m:
            blocks[bi]["votefor"] = int(m.group(1))
        n = 0
        for li in range(k + 1, end):
            if li in header_line_set:
                # next contest's header line (may carry digits, e.g.
                # "HUNTINGDON AREA SCHOOL REGION 1 - 4YR"); the engine
                # skips it as a header
                continue
            s = lines[li].strip()
            if not s or SKIP_ROW_RE.match(s):
                continue
            if VOTEFOR_RE.match(s):
                continue
            if is_junk(s):
                continue
            toks = s.split()
            if len(toks) < 2 or not nums_from(toks[1:]):
                continue
            n += 1
        blocks[bi]["nrows"] = n

    # per-party Ballots Cast numbers from the RAW STATISTICS lines (read
    # before they are neutralized below)
    party_bc = {}
    for l in lines:
        m = re.match(r"^Ballots Cast - (DEMOCRATIC|REPUBLICAN|NONPARTISAN)"
                     r"\s+([\d,]+)", l.strip())
        if m:
            party_bc[m.group(1)] = int(m.group(2).replace(",", ""))

    # neutralize per-party statistics lines
    for i, l in enumerate(lines):
        if PER_PARTY_STATS_RE.match(l.strip()):
            lines[i] = ";" + l

    out = tempfile.NamedTemporaryFile(mode="w", suffix=".txt",
                                      delete=False, encoding="utf-8")
    out.write("\n".join(lines) + "\n")
    out.close()
    return out.name, blocks, party_bc


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
    """Stamp each contest's party onto all of its rows, Write-ins rows
    included (following the Berks/Clinton primary precedent: the
    duplicate_entries data test hashes the non-vote columns
    county/office/district/party/candidate, so party-empty Write-ins
    rows from the DEM and REP halves of the same office would collide;
    the contest party identifies which ballot the write-ins came from)."""
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


def check_writeins(path, blocks):
    """Write-in arithmetic per contest from the RAW text:
    'Write-In Totals' == sum('Write-In: NAME' detail rows) + 'Not Assigned'
    (when the block carries detail rows; 'Scattered'/'Scatter' rows are
    part of the aggregate per repo convention)."""
    with open(path, encoding="utf-8") as fh:
        lines = [l.rstrip() for l in fh]
    header_idx = []
    for k, l in enumerate(lines):
        if re.match(r"^vote for \d+$", l.strip(), re.I):
            header_idx.append(k)
    spans = []
    for bi, k in enumerate(header_idx):
        end = header_idx[bi + 1] if bi + 1 < len(header_idx) else len(lines)
        spans.append((k, end))
    if len(spans) != len(blocks):
        raise SystemExit(
            f"write-in check: {len(spans)} blocks vs {len(blocks)} contests")

    bad = []
    for b, (k, end) in zip(blocks, spans):
        agg = det_sum = na = 0
        saw_agg = False
        for l in lines[k + 1:end]:
            s = l.strip()
            if s.upper().startswith("WRITE-IN TOTALS"):
                nums = nums_from(s.split())
                if nums:
                    agg = int(nums[0])
                    saw_agg = True
            elif s.upper().startswith("WRITE-IN:"):
                nums = nums_from(s.split()[1:])
                if nums:
                    det_sum += int(nums[0])
            elif re.match(r"^not assigned\b", s, re.I):
                nums = nums_from(s.split()[2:])
                if nums:
                    na = int(nums[0])
        b["writein_agg"] = agg
        b["writein_detail_sum"] = det_sum
        b["writein_na"] = na
        if saw_agg and agg != det_sum + na:
            bad.append((b["party"], b["office"], b["district"], agg,
                        det_sum + na))
    return bad


def check_party_ballots(blocks, party_bc):
    """Each party contest's votes (candidates + write-ins) must fit in
    that party's Ballots Cast x 'Vote For N' (undervotes are not
    reported by this source, so the sum can be far below)."""
    bad = []
    party_names = {"DEM": "DEMOCRATIC", "REP": "REPUBLICAN"}
    for b in blocks:
        rows = b.get("party_rows") or []
        agg = sum(int(r[5]) for r in rows if r[4] not in
                  ("Overvotes", "Undervotes"))
        bc = party_bc.get(party_names.get(b["party"], b["party"]), 0)
        limit = bc * b["votefor"]
        if agg > limit:
            bad.append((b["party"], b["office"], b["district"], agg, limit))
    return bad


def main():
    args = sys.argv[1:]
    if len(args) < 2:
        sys.exit(f"Usage: {sys.argv[0]} <input.pdf|txt> <output.csv>")
    src, dst = args[0], args[1]
    if src.lower().endswith(".pdf"):
        src = text_from_pdf(src)
    tmp, blocks, party_bc = preprocess(src)
    R = parse_esr(tmp, COUNTY, make_map_contest(blocks),
                  titlecase_names=False)
    stamp_party(R, blocks)
    n = write_csv(dst, R)
    print(f"wrote {n} rows -> {dst}")
    unique = {(b["office"], b["district"], b["party"]) for b in blocks}
    print(f"contests parsed: {len(blocks)} ({len(unique)} unique)")
    bad = check_writeins(src, blocks)
    if bad:
        for party, office, dist, agg, calc in bad:
            print(f"WARNING write-in arithmetic {party} {office} | {dist}: "
                  f"aggregate {agg} != details {calc}")
    else:
        print(f"write-in arithmetic check: all {len(blocks)} contests "
              f"reconcile")
    bad = check_party_ballots(blocks, party_bc)
    if bad:
        for party, office, dist, agg, limit in bad:
            print(f"WARNING party-ballot {party} {office} | {dist}: "
                  f"votes {agg} > ballot limit {limit}")
    else:
        print("party-ballot check: all contests within ballots cast")
    print("party ballots cast: "
          + ", ".join(f"{k} {v}" for k, v in sorted(party_bc.items())))


if __name__ == "__main__":
    main()