#!/usr/bin/env python3
"""
Parse Lackawanna County PA 2023 Primary (Municipal Primary, May 16, 2023)
election results.

Sources (Electionware "Summary Results Report" prints, pdftotext -layout):
  * "Lackawanna County Certified Summary Results 2023 Primary" — countywide
    summary -> county-level CSV (9 columns, no precinct)
  * "Lackawanna County Certified Precinct Results 2023 Primary" — per-precinct
    print (863 pages, 163 precincts) -> precinct CSV (10 columns)

The engine used for the precinct report is the shared
``electionware_txt_2023.parse_report`` (same as the 2023 general parser
``pa_lackawanna_general_2023_results_parser.py``, whose ``map_header`` this
script reuses so office/district conventions match that file exactly).  The
county summary goes through the shared ``pa_2023_summary_common.parse_esr``.

Primary-specific quirks handled here:
  * contest headers carry a party prefix ("DEM JUSTICE OF THE SUPREME COURT")
    -> the prefix is stripped and the party is carried through to the output
    party column for every candidate row of that contest (DEM/REP sections of
    the same office are kept separate by tagging the office with the party
    while parsing, then untagged before the CSV is written);
  * primary headers append the precinct qualifier ("WAVERLY TOWNSHIP W-00
    P-00") and spell out "TOWNSHIP"/"TWP." where the 2023 general headers did
    not ("SUPERVISOR WAVERLY"); a few headers order tokens differently
    ("REGION 3 FOUR YEAR TERM" vs "FOUR YEAR TERM REGION 3").  These are
    normalized back to the general-file header shape;
  * the ESR statistics block is consumed by parse_esr until a line starting
    with justice/judge/county/... appears; the per-party "Registered Voters -
    DEMOCRATIC/..." lines would trip that break before "Ballots Cast - Total"
    is reached.  The wrapper drops the per-party statistics lines and inserts
    a sentinel line after "Ballots Cast - Blank" so the shared engine sees
    RV-Total, Ballots Cast-Total and Ballots Cast-Blank;
  * page furniture ("PRIMARY ELECTION", "May 16, 2023 ... LACKAWANNA COUNTY")
    is stripped; the first page's missing footer is prepended synthetically;
  * ALL-CAPS precinct lines are title-cased to match the 2023 general file
    ("WAVERLY TOWNSHIP W-00 P-00" -> "Waverly Township W-00 P-00").

Usage:
    python parsers/pa_lackawanna_primary_2023_results_parser.py \
        <input.txt-or-.pdf> <output.csv> [--county Lackawanna]

The output shape (county-level vs precinct) is chosen by the input's own
report type, not by the file name.
"""

import os
import re
import sys

try:
    import electionware_txt_2023 as ew
    import pa_lackawanna_general_2023_results_parser as gen
    from pa_2023_summary_common import parse_esr, check_totals, write_csv
except ImportError:  # allow running from the repo root
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import electionware_txt_2023 as ew
    import pa_lackawanna_general_2023_results_parser as gen
    from pa_2023_summary_common import parse_esr, check_totals, write_csv

# In this primary the candidate rows carry no per-row party token (the party
# is in the contest header), while the shared engine classifies a row by a
# leading party code.  Patch the engine's row-party regex at runtime (same
# technique the 2023 general county parsers use for their extra party codes)
# so that a plain candidate row is accepted with an empty row party; the
# contest party is attached afterwards from the office tag.  Aggregate/label
# heads are excluded so they reach their own branches.
ew.PARTY_RE = re.compile(
    r"^()(?!Write-In Totals$|Overvotes$|Undervotes$|Not Assigned$|"
    r"Write-In:)(.+)$",
    re.IGNORECASE,
)

COUNTY = "Lackawanna"

PAGE_HEADER_KEY = "LACKAWANNA PRECINCT"

# candidate rows get the contest party, as do the contest's Write-ins /
# Overvotes / Undervotes aggregate rows (repo primary convention: the
# CONTEST's party is stamped on those rows so the DEM and REP sections of
# one office never collide in the duplicate_entries test). Only metadata
# (Registered Voters / Ballots Cast) and "No Candidate Filed" stay
# party-empty.
NO_PARTY_CANDIDATES = {"", "No Candidate Filed"}

PARTY_PREFIX_RE = re.compile(r"^(DEM|REP)\s+(.+)$", re.IGNORECASE)
PRECINCT_SUFFIX_RE = re.compile(r"\s+W-\d+ P-\d+\s*$")


def normalize_body(body):
    """Rewrite a primary contest header into the 2023 general header shape."""
    b = PRECINCT_SUFFIX_RE.sub("", body).strip()
    # general headers drop the trailing municipality-type word
    b = re.sub(r"\s+(TOWNSHIP|TWP\.?|BORO)$", "", b, flags=re.I).strip()
    # spelling differences between the primary and general header text
    b = b.replace("LA PLUME", "LAPLUME")
    b = re.sub(r"^(FOREST CITY REGIONAL SCHOOL DIRECTOR REGION \d)\s+VANDLING$",
               r"\1", b)
    b = re.sub(r"^(LACKAWANNA TRAIL SCHOOL DIRECTOR)\s+REGION (\d)\s+"
               r"(FOUR YEAR TERM|TWO YEAR UNEXPIRED TERM)$", r"\1 \3 REGION \2",
               b)
    return b


def split_party(raw):
    """Return (party, body) for a 'DEM ...'/'REP ...' header, else None."""
    h = re.sub(r"\s+", " ", raw.strip())
    m = PARTY_PREFIX_RE.match(h)
    if not m:
        return None
    return m.group(1).upper(), m.group(2).strip()


def map_party_header(raw):
    """Shared header mapper: general conventions + party tag on the office.

    Returns (office_with_party_tag, district) or None for unknown headers
    (reported as warnings by the engines, never guessed).
    """
    split = split_party(raw)
    if split is None:
        return None
    party, body = split
    mapped = gen.map_header(normalize_body(body))
    if mapped is None:
        return None
    office, district = mapped
    # tag carries the contest party through the parse; stripped before write
    return (office + "\t" + party, district)


# --------------------------------------------------------------------------
# county summary (parse_esr)
# --------------------------------------------------------------------------

def preprocess_summary(text):
    """Make the ESR statistics block parseable by the shared engine.

    parse_esr's statistics loop breaks on the first line starting with
    register/justice/judge/... ; "Registered Voters - DEMOCRATIC" trips it
    before the Ballots Cast lines are seen.  Drop the per-party statistics
    lines and insert a break sentinel after "Ballots Cast - Blank".
    """
    out = []
    sentineled = False
    for line in text.split("\n"):
        s = line.strip()
        if re.match(r"^Registered Voters - (DEMOCRATIC|REPUBLICAN|"
                    r"NONPARTISAN)\b", s):
            continue
        if re.match(r"^Ballots Cast - (DEMOCRATIC|REPUBLICAN|NONPARTISAN)\b",
                    s):
            continue
        out.append(line)
        if not sentineled and re.match(r"^Ballots Cast - Blank\b", s):
            out.append("Judge Placeholder")  # terminates the statistics loop
            sentineled = True
    return "\n".join(out)


def map_contest(raw):
    return map_party_header(raw)


def untag_rows(R):
    """Move the office's party tag into the party column."""
    for r in R.rows:
        if "\t" in r[1]:
            office, party = r[1].split("\t", 1)
            r[1] = office
            if r[4] not in NO_PARTY_CANDIDATES:
                r[3] = party
    return R


def run_county(src, dst, county):
    # parse_esr reads from a path; preprocess the text (drop the per-party
    # statistics lines, add the sentinel) and parse from a temp file
    with open(src, encoding="utf-8", errors="replace") as fh:
        text = preprocess_summary(fh.read())
    import tempfile
    tf = tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False)
    tf.write(text)
    tf.close()
    R = parse_esr(tf.name, county, map_contest, titlecase_names=False)
    bad = check_totals(R)
    untag_rows(R)
    n = write_csv(dst, R)
    print(f"wrote {n} rows -> {dst}")
    print(f"contests parsed: {len(R.contests)}")
    if bad:
        for (office, dist), a, t in bad:
            print(f"WARNING {office} | {dist}: rows sum {a} != "
                  f"Total Votes Cast {t}")
    else:
        print("contest totals check: all contests reconcile")
    return n


# --------------------------------------------------------------------------
# precinct report (electionware_txt_2023)
# --------------------------------------------------------------------------

PAGE_FURNITURE_RE = re.compile(
    r"^\s*((MUNICIPAL )?PRIMARY ELECTION"
    r"|May 16, 2023\s+LACKAWANNA COUNTY)\s*$")


def preprocess_precinct(text):
    """Drop page furniture the shared engine does not know; add the missing
    first-page footer so the first precinct is captured."""
    lines = ["LACKAWANNA PRECINCT - 06/05/2023         2:05 PM   0 of 863"]
    for line in text.split("\n"):
        if PAGE_FURNITURE_RE.match(line):
            continue
        lines.append(line)
    return "\n".join(lines)


def prettify_precinct(s):
    """'CARBONDALE TWP. W-00 P-NE' -> 'Carbondale Twp. W-00 P-NE'."""
    out = []
    for tok in s.split():
        if "-" in tok or any(ch.isdigit() for ch in tok):
            out.append(tok)  # W-00 / P-NE / P-1-1 keep their source form
        else:
            out.append(tok.capitalize())
    return " ".join(out)


def run_precinct(text, dst, county):
    rows, warnings = ew.parse_report(
        preprocess_precinct(text), county, map_party_header,
        page_header_key=PAGE_HEADER_KEY,
        header_skip=("PRIMARY ELECTION", "CERTIFIED RESULTS"),
        strip_pct=True,
        prettify_precinct=prettify_precinct,
        log=lambda m: print(m, file=sys.stderr),
    )
    for r in rows:
        if "\t" in r["office"]:
            office, party = r["office"].split("\t", 1)
            r["office"] = office
            if r["candidate"] not in NO_PARTY_CANDIDATES:
                r["party"] = party
    ew.write_csv(rows, dst, county)
    print(f"wrote {len(rows)} rows -> {dst} ({len(warnings)} warnings)")
    for w in warnings[:30]:
        print("WARN:", w, file=sys.stderr)
    return len(rows)


def load_text(src):
    if src.lower().endswith(".pdf"):
        return ew.load_text(src)
    with open(src, encoding="utf-8", errors="replace") as fh:
        return fh.read()


def _pdf_txt(pdf_path):
    """pdftotext -layout extraction to a temp file; returns the path."""
    import tempfile
    import subprocess
    tf = tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False)
    tf.close()
    subprocess.run(["pdftotext", "-layout", pdf_path, tf.name], check=True)
    return tf.name


def main():
    args = [a for a in sys.argv[1:] if a != "--county"]
    county = COUNTY
    argv = sys.argv[1:]
    if "--county" in argv:
        county = argv[argv.index("--county") + 1]
    if len(args) != 2:
        sys.exit(f"Usage: {sys.argv[0]} <input.txt-or-.pdf> <output.csv> "
                 f"[--county NAME]")
    src, dst = args
    text = load_text(src)
    if "Precinct Results Report" in text:
        run_precinct(text, dst, county)
    else:
        # county summary: parse_esr reads from a path
        if src.lower().endswith(".pdf"):
            src = _pdf_txt(src)
        run_county(src, dst, county)


if __name__ == "__main__":
    main()