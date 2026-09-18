#!/usr/bin/env python3
"""Parse Washington County PA 2023 Municipal Primary results.

Two sources, same parser, selected by the input file name:

- ``Washington_County_Summary_Results_2023_Primary.txt`` (Electionware
  "Summary Results Report" county summary, 191 pages) -> county-level CSV
  via the shared ``pa_2023_summary_common.parse_esr`` engine (same wrapper
  shape as ``pa_clinton_primary_2023_results_parser.py``).

- ``Washington_County_Precinct_Summary_Results_2023_Primary.txt``
  (Electionware per-precinct summary, 2695 pages, 180 precincts) ->
  precinct CSV via the shared ``electionware_txt`` engine, using the same
  office conventions as ``pa_washington_general_2023_results_parser.py``
  (whose ``normalize`` is reused).

Primary-specific handling:
  - Contest headers carry the party as a prefix ("Dem JUSTICE OF THE
    SUPREME COURT" / "Rep AUDITOR - 2 YEARS BLAINE"; liquor questions have
    no prefix).  A preprocessor strips the prefix off contest-header lines
    and records each contest's party in file order; the county engine gets
    it back through map_contest, the precinct engine through a party
    marker embedded in the office name and stamped onto every candidate
    row afterwards (Write-ins/Overvotes/Undervotes rows carry the contest
    party too, as in the Blair/Clinton/Lawrence primary parsers; only
    question Yes/No rows stay party-empty).
  - Per-party "Registered Voters - Democratic/..." and "Ballots Cast -
    Democratic/..." statistics labels (values printed on the line above)
    are dropped in the precinct file so the engine keeps only the
    Total/Blank rows.
  - Headers Washington prints only in the primary are mapped explicitly:
    "MAGISTERIAL DISTRICT JUDGE MD 27-1-02" (district "27-1-02"),
    "BOROUGH COUNCIL - CBB FIRST WARD"/"- CBG 2ND SECOND WARD" (CBB/CBG
    both abbreviate Canonsburg), "SCHOOL DIRECTOR - RINGGOLD AT LARGE
    RINGGOLD SCHOOL DISTRICT", "BURGETTSTOWN AREA SCHOOL DISTRICT REGION
    III - 2 YRS", doubled municipality in "CONSTABLE- SMITH SMITH" /
    "TAX COLLECTOR - CECIL CECIL", and the leading dash in "Dem - MAYOR -
    CITY MONONGAHELA".
  - The county summary prints Avella Area School District Region II in
    two consecutive sections (a candidate section and a write-in-only
    section, both "Vote For 1"); the second occurrence is merged back
    into the first (Write-ins/Overvotes/Undervotes summed, "Total Votes
    Cast" summed) so the contest appears once.
  - Liquor-license ballot questions ("QUESTION 1 MORRIS - LIQUOR
    LICENSES SALE") are recorded as office "Question N" with the rest of
    the header (township + question subject) in the district column.

Usage:
    python parsers/pa_washington_primary_2023_results_parser.py \
        <input.txt|input.pdf> <output.csv>
"""

import csv
import re
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from pa_2023_summary_common import (  # noqa: E402
    CANON_NAMES,
    check_totals,
    parse_esr,
    text_from_pdf,
    write_csv,
)
from electionware_txt import FIELDNAMES, TxtConfig, parse_txt  # noqa: E402
import pa_washington_general_2023_results_parser as g23  # noqa: E402
from pa_washington_general_2025_results_parser import (  # noqa: E402
    school_director as school_director_2025,
)

COUNTY = "Washington"

MARK = "\x01"  # party marker appended to office names, stripped post-parse
DUP = "\x02"   # marks the second of two consecutive duplicate sections

PARTY_PREFIX_RE = re.compile(r"^(DEM|REP)\s+(.+)$", re.IGNORECASE)
VOTEFOR_LINE_RE = re.compile(r"^\s*vote for \d+\s*$", re.IGNORECASE)
PARTY_STAT_RE = re.compile(
    r"^\s*(?:Registered Voters|Ballots Cast) - "
    r"(?:Democratic|Republican|NONPARTISAN)\b", re.IGNORECASE)
DATE_PAGE_RE = re.compile(r"^\s*May \d{1,2},\s+\d{4}\b", re.IGNORECASE)
NOCAND_RE = re.compile(r"^(\s*)NO CANDIDATE(\s+\d[\d,]*){4}\s*$")

AGG_CANDIDATES = ("Write-ins", "Overvotes", "Undervotes")


# ---------------------------------------------------------------------------
# Header -> (office, district).
# ---------------------------------------------------------------------------


def _dedupe(tail: str) -> str:
    parts = tail.split()
    if len(parts) == 2 and parts[0].upper() == parts[1].upper():
        return parts[0]
    return tail


CBG_WARD_RE = re.compile(
    r"^BOROUGH COUNCIL - (?:CBB|CBG) (FIRST|2ND SECOND|SECOND|THIRD) WARD$")
COUNCIL_AL_RE = re.compile(r"^(?:BOROUGH )?COUNCIL AT LARGE\s*-\s*(.+)$")
CONSTABLE_RE = re.compile(r"^CONSTABLE\s*-?\s*(.+)$")
TAXCOLL_DASH_RE = re.compile(r"^TAX COLLECTOR\s*-\s*(.+)$")
MDJ_MD_RE = re.compile(r"^MAGISTERIAL DISTRICT JUDGE\s+MD\s+([\d-]+)$")
QUESTION_RE = re.compile(r"^QUESTION (\d+)\s+(.+)$")
RINGGOLD_AL_RE = re.compile(r"^SCHOOL DIRECTOR - RINGGOLD")
SD_YRS_RE = re.compile(
    r"^(.+?)\s+SCHOOL DISTRICT\s+(REGION \S+)\s*-\s*(\d+)\s*YRS?$", re.I)

# Exact headers (checked before the school/term handlers).  The precinct
# report truncates the Canon McMillan header at "CANONSBURG EXCEPT"; the
# county summary shows the full contest is "... CANONSBURG EXCEPT CBG 1-4"
# (same candidates), so both spellings map to the county summary's name.
EXACT_HEADERS = {
    "CANON MCMILLAN SCHOOL DISTRICT SCHOOL DIRECTOR CANONSBURG EXCEPT": (
        "School Director Canonsburg Except Cbg 1-4", "Canon Mcmillan"),
}


def map_header(header: str):
    """Map a party-stripped contest header to (office, district)."""
    h = re.sub(r"\s+", " ", header.strip()).upper()

    if h in EXACT_HEADERS:
        return EXACT_HEADERS[h]

    m = QUESTION_RE.match(h)
    if m:
        return ("Question " + m.group(1), m.group(2).strip().title())

    m = MDJ_MD_RE.match(h)
    if m:
        return ("Magisterial District Judge", m.group(1))

    m = CBG_WARD_RE.match(h)
    if m:
        word = m.group(1)
        word = "Second" if word in ("SECOND", "2ND SECOND") \
            else word.capitalize()
        return ("Borough Council", "Canonsburg %s Ward" % word)

    m = COUNCIL_AL_RE.match(h)
    if m:
        return ("Borough Council At Large", g23.title_case(m.group(1)))

    m = CONSTABLE_RE.match(h)
    if m:
        return ("Constable", g23.title_case(_dedupe(m.group(1))))

    m = TAXCOLL_DASH_RE.match(h)
    if m:
        return ("Tax Collector", g23.title_case(_dedupe(m.group(1))))

    if RINGGOLD_AL_RE.match(h):
        return ("School Director At Large", "Ringgold")

    m = SD_YRS_RE.match(h)
    if m:
        return ("School Director %s (%s Year)" % (m.group(2), m.group(3)),
                g23.title_case(m.group(1)))

    school = g23.school_director_2023(h) or school_director_2025(h)
    if school:
        return school

    return g23.normalize(h)


# ---------------------------------------------------------------------------
# Preprocessing (both engines): strip the party prefix off contest headers.
# ---------------------------------------------------------------------------


def strip_party_prefixes(lines):
    """Blank the DEM/REP prefix off each contest-header line.

    Returns (new lines, [party, ...] in contest order; "" for the
    non-partisan QUESTION contests).
    """
    out = list(lines)
    parties = []
    for i, line in enumerate(lines):
        if not line.strip():
            continue
        j = i + 1
        while j < len(lines) and not lines[j].strip():
            j += 1
        if j >= len(lines) or not VOTEFOR_LINE_RE.match(lines[j]):
            continue
        m = PARTY_PREFIX_RE.match(line.strip())
        if m:
            parties.append(m.group(1).upper())
            rest = m.group(2)
        else:
            parties.append("")
            rest = line.strip()
        out[i] = " " + rest.lstrip("- ").strip()
    return out, parties


def preprocess_common(lines):
    """Both files: rename bare "NO CANDIDATE" data rows to "NO CANDIDATE
    FILED" (parse_esr's label; the bare form would otherwise fall into its
    YES/NO retention branch)."""
    out = []
    for line in lines:
        if NOCAND_RE.match(line):
            line = line.replace("NO CANDIDATE", "NO CANDIDATE FILED", 1)
        out.append(line)
    return out


def preprocess_precinct_lines(lines):
    """Precinct-file line fixes: drop per-party statistics labels and
    bare 'May 16, 2023 [Washington]' page-header lines, then strip the
    party prefixes off contest headers."""
    kept = []
    for line in lines:
        if PARTY_STAT_RE.match(line):
            continue
        if DATE_PAGE_RE.match(line):
            continue
        kept.append(line)
    return strip_party_prefixes(preprocess_common(kept))


# ---------------------------------------------------------------------------
# County summary (parse_esr).
# ---------------------------------------------------------------------------


class ContestMapper:
    """map_contest for parse_esr: returns (office+party-marker, district).

    Consecutive duplicate headers (the split Avella Region II sections)
    get a DUP-marked office so their totals can be summed afterwards.
    """

    def __init__(self, parties):
        self.parties = list(parties)
        self.i = 0
        self.prev = None
        self.party_by_key = {}

    def map(self, header):
        if self.i >= len(self.parties):
            raise SystemExit(f"unqueued contest header: {header!r}")
        party = self.parties[self.i]
        self.i += 1
        office, district = map_header(header)
        key_office = office
        if (office, district) == self.prev:
            key_office = office + DUP
        self.prev = (office, district)
        if party:
            key_office += MARK + party
        self.party_by_key[(key_office, district)] = party
        return (key_office, district)


def merge_dup_sections(R):
    """Fold DUP-marked second sections back into their first section."""
    dup_rows = [(r[1], r[2]) for r in R.rows if DUP in r[1]]
    dup_keys = sorted({k for k in dup_rows})
    for (o, d) in dup_keys:
        base = (o.replace(DUP, ""), d)
        for r in R.rows:
            if r[1] == o and r[2] == d:
                r[1] = base[0]
        if (o, d) in R.totals:
            R.totals[base] = R.totals.get(base, 0) + R.totals.pop((o, d))
    # merge now-contiguous aggregate rows (Write-ins/Overvotes/Undervotes)
    merged = []
    agg_idx = {}
    current = None
    for r in R.rows:
        key = (r[1], r[2])
        if key != current:
            current = key
            agg_idx = {}
        akey = (r[1], r[2], r[4])
        if r[4] in AGG_CANDIDATES and akey in agg_idx:
            prev = merged[agg_idx[akey]]
            for f in (5, 6, 7, 8):
                prev[f] = str(int(prev[f] or 0) + int(r[f] or 0))
        else:
            merged.append(r)
            if r[4] in AGG_CANDIDATES:
                agg_idx[akey] = len(merged) - 1
    R.rows = merged


def stamp_county(R):
    """Split the party marker out of office names into the party column.

    Aggregate rows (Write-ins/Overvotes/Undervotes) carry the contest
    party too: the DEM and REP sections of the same office are separate
    contests, and party-empty aggregate rows would collide on the CI
    duplicate_entries key (same convention as the Clinton/Blair/Lawrence
    primary parsers).
    """
    for r in R.rows:
        if MARK in r[1]:
            office, party = r[1].split(MARK)
            r[1] = office
            if r[4] not in ("Yes", "No"):
                r[3] = party
            if r[4] in REVERT_CANON:
                r[4] = REVERT_CANON[r[4]]
        elif r[4] in REVERT_CANON:
            r[4] = REVERT_CANON[r[4]]


# Source prints statewide candidates in ALL-CAPS (Washington's 2023
# general precinct file convention); parse_esr's CANON_NAMES title-cases
# a few of them, so revert to the printed spelling.
REVERT_CANON = {
    "Daniel McCaffery": "DANIEL MCCAFFERY",
    "Carolyn Carluccio": "CAROLYN CARLUCCIO",
    "Timika Lane": "TIMIKA LANE",
    "Jill Beck": "JILL BECK",
    "Matt Wolf": "MATT WOLF",
    "Maria Battista": "MARIA BATTISTA",
    "Harry F. Smail Jr.": "HARRY F SMAIL JR",
    "Megan Martin": "MEGAN MARTIN",
}


# ---------------------------------------------------------------------------
# Precinct report (electionware_txt engine).
# ---------------------------------------------------------------------------


class PrecinctNormalizer:
    """TxtConfig.normalize_office hook with the party marker attached."""

    def __init__(self, parties):
        self.parties = list(parties)
        self.i = 0

    def __call__(self, line):
        if self.i >= len(self.parties):
            raise SystemExit(f"unqueued contest header: {line!r}")
        party = self.parties[self.i]
        self.i += 1
        office, district = map_header(line)
        if party:
            office = office + MARK + party
        return (office, district)


def stamp_precinct(rows):
    out = []
    for r in rows:
        office = r["office"]
        if MARK in office:
            office, party = office.split(MARK)
            r["office"] = office
            # Write-ins/Overvotes/Undervotes carry the contest party too
            # (same convention as the Blair/Lawrence primary precinct
            # files); only question rows (Yes/No) stay party-empty.
            if r["candidate"] not in ("Yes", "No"):
                r["party"] = party
        if r["candidate"] == "NO CANDIDATE FILED":
            r["candidate"] = "No Candidate Filed"  # county-file spelling
        out.append(r)
    return out


def parse_precinct(src):
    lines = Path(src).read_text(errors="replace").split("\n")
    lines, parties = preprocess_precinct_lines(lines)
    norm = PrecinctNormalizer(parties)
    cfg = TxtConfig(
        county=COUNTY,
        normalize_office=norm,
        prettify_precinct=g23.prettify_precinct,
        party_optional=True,
        # "No Candidate Filed" rows are kept (they carry real votes in
        # the Union Township supervisor contest).
        skip_candidates=(),
    )
    rows, precinct_count, warnings, unnamed = parse_txt_lines(lines, cfg)
    if norm.i != len(parties):
        print(f"WARNING: party queue consumed {norm.i} of "
              f"{len(parties)} contest headers")
    return stamp_precinct(rows), precinct_count, warnings, unnamed


def parse_txt_lines(lines, cfg):
    """parse_txt on an already-read line list (mirrors engine plumbing)."""
    tmp = tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False,
                                      encoding="utf-8")
    tmp.write("\n".join(lines) + "\n")
    tmp.close()
    try:
        return parse_txt(Path(tmp.name), cfg)
    finally:
        Path(tmp.name).unlink()


# ---------------------------------------------------------------------------


def parse_county(src):
    lines = Path(src).read_text(errors="replace").rstrip("\n").split("\n")
    # The shared engine's STATISTICS scanner breaks on any line starting
    # with a bare office word ("register|..."), which would cut the block
    # short at "Registered Voters - Democratic"; drop the per-party
    # statistics labels so only the Total/Blank rows remain.
    lines = [ln for ln in lines if not PARTY_STAT_RE.match(ln)]
    lines, parties = strip_party_prefixes(preprocess_common(lines))
    mapper = ContestMapper(parties)
    tmp = tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False,
                                      encoding="utf-8")
    tmp.write("\n".join(lines) + "\n")
    tmp.close()
    try:
        R = parse_esr(tmp.name, COUNTY, mapper.map, titlecase_names=False)
    finally:
        Path(tmp.name).unlink()
    merge_dup_sections(R)
    bad = check_totals(R)
    stamp_county(R)
    return R, bad, mapper


def write_csv_county(path, R):
    from pa_2023_summary_common import FIELDNAMES as FN9
    import csv as _csv
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = _csv.writer(fh)
        w.writerow(FN9)
        w.writerows(R.rows)
    return len(R.rows)


def main(argv):
    if len(argv) != 3:
        sys.exit(f"Usage: {Path(argv[0]).name} <input.txt|input.pdf> "
                 f"<output.csv>")
    src, dst = argv[1], argv[2]
    out_path = Path(dst)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if src.lower().endswith(".pdf"):
        src = text_from_pdf(src)
    if "precinct" in Path(src).name.lower():
        rows, precinct_count, warnings, unnamed = parse_precinct(src)
        with out_path.open("w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=FIELDNAMES)
            w.writeheader()
            w.writerows(rows)
        print(f"Wrote {len(rows)} rows across {precinct_count} precincts "
              f"to {out_path}")
        if unnamed:
            print(f"Skipped {unnamed} unnamed Statistics segment(s)")
        if warnings:
            print(f"WARNING: {len(warnings)} unmatched lines:")
            for wmsg in warnings[:40]:
                print("  " + wmsg)
            if len(warnings) > 40:
                print("  ...")
        return
    R, bad, mapper = parse_county(src)
    n = write_csv_county(out_path, R)
    print(f"wrote {n} rows -> {out_path}")
    print(f"contests parsed: {len(R.contests)} "
          f"({len({(r[1], r[2]) for r in R.rows if MARK not in r[1]})} unique)")
    if bad:
        for (office, dist), a, t in bad:
            print(f"WARNING {office} | {dist}: rows sum {a} != "
                  f"Total Votes Cast {t}")
    else:
        print("contest totals check: all contests reconcile")


if __name__ == "__main__":
    main(sys.argv)