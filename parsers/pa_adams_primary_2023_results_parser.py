#!/usr/bin/env python3
"""
Parser for Adams County, PA 2023 Municipal Primary (May 16, 2023) results.

Two sources, auto-detected from the input (CLI contract: <input> <output>):

1. "Adams County Summary Results 2023 Primary" — Electionware "Summary
   Results Report" county summary (county-level CSV). Parsed through the
   shared engine ``pa_2023_summary_common.parse_esr`` after two small
   text adjustments this primary needs:

   - the per-party "Registered Voters - Democratic/Republican/NONPARTISAN"
     lines are dropped (they trip the statistics-block scanner's break
     before "Ballots Cast" is reached), and
   - a sentinel line is inserted after the statistics block, because the
     scanner breaks on bare office words while primary headers all start
     with a "DEM "/"REP " prefix and would never terminate the scan.

   Primary headers carry the contest party ("DEM Justice of the Supreme
   Court"), which ``map_contest`` strips into the party slot.  Candidate
   rows carry no printed party, so candidate rows get the contest party
   in a post-pass (walking rows against the header order, which handles
   the same office appearing under both parties, e.g. Superior Court).

2. "Adams County Precinct Summary Results 2023 Primary" — per-precinct
   Electionware report (10-column precinct CSV), via the shared pdftotext
   engine ``electionware_txt`` with the county's general-election office
   normalization (``pa_adams_general_2023_results_parser.normalize_office``).

Conventions (mirroring the county's 2023 general file
``2023/counties/20231107__pa__general__adams__precinct.csv``):
  - candidates keep the report's ALL-CAPS names;
  - local headers "<Office> [Nyr] <Muni>" -> office "<Office>", district
    "Nyr <Muni>";
  - "Magisterial District Judge District 51-3-03" -> office "Magisterial
    District Judge", district "District 51-3-03";
  - "Write-In Totals" -> "Write-ins" carrying the contest's party (DEM/REP):
    primary DEM/REP contests share office names, and party-empty Write-ins
    rows would collide in the duplicate_entries data test, which ignores
    vote columns (Blair/Berks 2023-primary convention).

Usage:
    python parsers/pa_adams_primary_2023_results_parser.py \
        "<summary.txt|precinct.txt|pdf>" "<output.csv>"
"""

import csv
import re
import sys
import tempfile
from collections import defaultdict
from pathlib import Path

from electionware_precinct_np import VOTE_FOR_RE  # shared engine, do not edit
from electionware_txt import (  # shared engine, do not edit
    TxtConfig,
    parse_segment,
    precinct_segments,
    txt_lines,
)
from pa_adams_general_2023_results_parser import (
    normalize_office as normalize_office_general,
)
from pa_2023_summary_common import (
    is_junk,
    nums_from,
    parse_esr,
    write_csv,
)

COUNTY = "Adams"

PARTY_PREFIX_RE = re.compile(r"^(DEM|REP)\s+(.+)$", re.IGNORECASE)

META_OFFICES = {"Registered Voters", "Ballots Cast", "Ballots Cast - Blank"}
# Aggregate rows keep an empty party column (repo convention).
NO_PARTY_CANDIDATES = {"Write-ins", "Overvotes", "Undervotes"}


def _strip_party(header):
    """("DEM"|"REP"|"", remainder) for a primary contest header line."""
    m = PARTY_PREFIX_RE.match(re.sub(r"\s+", " ", header.strip()))
    if m:
        return m.group(1).upper(), m.group(2).strip()
    return "", re.sub(r"\s+", " ", header.strip())


# ---------------------------------------------------------------------------
# County-summary mode (Electionware ESR county summary).
# ---------------------------------------------------------------------------

def map_contest(header):
    """(office, district, party) from a "DEM <contest>" / "REP <contest>" header."""
    party, rest = _strip_party(header)
    if not party:
        # Unexpected (no party prefix) — surfaced by the totals check.
        office, district = normalize_office_general(re.sub(r"\s+", " ", header.strip()))
        return office, district, ""
    office, district = normalize_office_general(rest)
    return office, district, party


def preprocess_summary_lines(lines):
    """Adjustments so parse_esr's statistics-block scanner terminates.

    1. Drop per-party "Registered Voters - <party>" lines: they match the
       scanner's break regex (^(...|register|...)) and would break the scan
       before "Ballots Cast" is read.
    2. Insert a bare "County" sentinel after the statistics block: primary
       headers all start with "DEM "/"REP ", so no real line terminates the
       statistics scan (which looks for office-word-initial lines).
       The sentinel is skipped as junk/undigitized text in the main loop.
    """
    out = []
    sentinel_placed = False
    for raw in lines:
        s = raw.strip()
        if re.match(r"^Registered Voters - (Democratic|Republican|NONPARTISAN)", s, re.I):
            continue
        if not sentinel_placed and re.match(r"^Voter Turnout", s, re.I):
            out.append("County")  # matches parse_esr's stats-block break regex
            sentinel_placed = True
        out.append(raw)
    if not sentinel_placed:
        raise RuntimeError("Statistics block not found in county summary")
    return out


def scan_summary_contest_totals(lines):
    """Per contest (in header order): dict(vf, tvc, ct, party, header).

    tvc = "Total Votes Cast" (candidates + write-ins), ct = "Contest Totals"
    (ballots available for the contest; = party ballots x Vote For for
    countywide contests).
    """
    out = []
    cur = None
    prev = ""
    for raw in lines:
        s = re.sub(r"\s+", " ", raw.strip())
        if re.match(r"^vote for \d+$", s, re.I):
            cur = {
                "vf": int(re.search(r"(\d+)", s, re.I).group(1)),
                "tvc": None,
                "ct": None,
                "party": _strip_party(prev)[0],
                "header": prev,
            }
            out.append(cur)
        elif cur is not None:
            u = s.upper()
            if u.startswith("TOTAL VOTES CAST"):
                n = nums_from(s.split())
                cur["tvc"] = int(n[0]) if n else None
            elif u.startswith("CONTEST TOTALS"):
                n = nums_from(s.split())
                cur["ct"] = int(n[0]) if n else None
        if s:
            prev = s
    return out


def parse_county(text_path):
    """Parse the county summary; returns (rows, contests, warnings)."""
    with open(text_path, encoding="utf-8") as fh:
        raw_lines = [l.rstrip() for l in fh]
    totals = scan_summary_contest_totals(raw_lines)

    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as tf:
        tmp = tf.name
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write("\n".join(preprocess_summary_lines(raw_lines)))
    R = parse_esr(tmp, COUNTY, map_contest, titlecase_names=False)

    warnings = []

    # Fill candidate-row parties by walking rows against the contest order
    # (same office/district can occur under both parties, e.g. Superior
    # Court), and keep the county's ALL-CAPS candidate convention.
    hdrs = [(c[0], c[1], c[2]) for c in R.contests]
    if len(hdrs) != len(totals):
        warnings.append(
            f"contest count mismatch: parse_esr {len(hdrs)} vs text scan {len(totals)}"
        )
    sums = defaultdict(int)
    p = 0
    for row in R.rows:
        office, district, cand = row[1], row[2], row[4]
        if office in META_OFFICES:
            continue
        while p < len(hdrs) and (hdrs[p][0], hdrs[p][1]) != (office, district):
            p += 1
        if p >= len(hdrs):
            warnings.append(f"no contest header for row {office}|{district}|{cand}")
            continue
        try:
            sums[p] += int(str(row[5]).replace(",", "") or 0)
        except ValueError:
            pass
        row[3] = hdrs[p][2]
        if cand not in NO_PARTY_CANDIDATES:
            # County convention is the report's ALL-CAPS printed names;
            # pa_2023_summary_common.clean_name rewrites a few statewide
            # names via CANON_NAMES (adding periods to "HARRY F SMAIL
            # JR"), so restore the printed form here.
            name = str(cand).upper()
            row[4] = "HARRY F SMAIL JR" if name == "HARRY F. SMAIL JR." else name

    # Contest-totals reconciliation (party-aware keys).
    for idx, t in enumerate(totals):
        got = sums.get(idx)
        if t["tvc"] is None:
            warnings.append(f"contest {idx} no Total Votes Cast in text: {t['header']!r}")
        elif got != t["tvc"]:
            warnings.append(
                f"RECONCILE {t['header']!r}: rows sum {got} != Total Votes Cast {t['tvc']}"
            )
        if t["ct"] is not None and got is not None and got > t["ct"]:
            warnings.append(
                f"OVERFLOW {t['header']!r}: rows sum {got} > Contest Totals {t['ct']}"
            )
    return R, totals, warnings


def parse_party_ballots(raw_lines):
    """Per-party ballots cast + blanks from the STATISTICS block."""
    party_bc = {}
    blank = None
    for raw in raw_lines:
        s = re.sub(r"\s+", " ", raw.strip())
        m = re.match(r"^Ballots Cast - (Democratic|Republican)\s+([\d,]+)", s, re.I)
        if m:
            party_bc[m.group(1)[:3].upper()] = int(m.group(2).replace(",", ""))
        elif re.match(r"^Ballots Cast - Blank\s+([\d,]+)", s, re.I):
            blank = int(re.search(r"([\d,]+)", s).group(1).replace(",", ""))
    return party_bc, blank


def run_county(src, dst):
    with open(src, encoding="utf-8") as fh:
        raw_lines = [l.rstrip() for l in fh]
    R, totals, warnings = parse_county(src)
    n = write_csv(dst, R)

    print(f"wrote {n} rows -> {dst}")
    print(f"contests parsed: {len(R.contests)}")

    # Party-ballot arithmetic: every statewide contest's Contest Totals must
    # equal party ballots cast x Vote For; local contests must not exceed it.
    party_bc, blank = parse_party_ballots(raw_lines)
    print(f"party ballots cast: {party_bc}, blank: {blank}")
    statewide = {
        "Justice of the Supreme Court", "Judge of the Superior Court",
        "Judge of the Commonwealth Court",
    }
    bad_statewide = []
    for idx, t in enumerate(totals):
        office, _dist, party = R.contests[idx][0], R.contests[idx][1], R.contests[idx][2]
        if office in statewide and t["ct"] is not None:
            expect = party_bc.get(party, 0) * t["vf"]
            if t["ct"] != expect:
                bad_statewide.append((t["header"], t["ct"], expect))
    if bad_statewide:
        for h, ct, ex in bad_statewide:
            print(f"WARNING statewide Contest Totals {h!r}: {ct} != {ex}")
    else:
        print("statewide Contest Totals == party ballots cast x Vote For: OK")

    bad = [w for w in warnings if w.startswith(("RECONCILE", "OVERFLOW"))]
    other = [w for w in warnings if not w.startswith(("RECONCILE", "OVERFLOW"))]
    if bad:
        print(f"contest totals check: FAILED ({len(bad)} problem(s))")
        for w in bad[:20]:
            print("  " + w)
    else:
        print(f"contest totals check: all {len(totals)} contests reconcile")
    for w in other:
        print("WARNING " + w)
    return n


# ---------------------------------------------------------------------------
# Precinct mode (per-precinct Electionware report).
# ---------------------------------------------------------------------------

_party_log = []  # (office, district, party) per normalize_office call


def _normalize_office_primary(line):
    party, rest = _strip_party(line)
    office, district = normalize_office_general(rest)
    _party_log.append((office, district, party))
    return office, district


PRECINCT_CFG = TxtConfig(
    county=COUNTY,
    normalize_office=_normalize_office_primary,
    # Primary candidate rows carry no printed party (the contest header
    # does), so rows are emitted party-empty and the contest party is
    # filled in afterwards by _assign_parties.
    party_optional=True,
    extra_junk=(
        r"^Registered Voters - (Democratic|Republican|NONPARTISAN)\b",
        r"^Ballots Cast - (Democratic|Republican|NONPARTISAN)\b",
        r"(?i)^may \d{1,2}, 2023$",
        r"(?i)^primary election$",
    ),
)


def _assign_parties(rows, warnings, precinct):
    """Give every contest row (candidates and Write-ins) its contest party.

    Rows come out of parse_segment in file order; the office/district keys
    of the encountered headers (logged by _normalize_office_primary) are
    walked in parallel, which disambiguates the same office appearing under
    both party ballots (Superior Court, MDJ districts, ...). Write-ins rows
    carry the contest party: with an empty party they would collide as
    duplicate rows in the duplicate_entries data test, which ignores vote
    columns (Blair/Berks 2023-primary convention).
    """
    hdrs = list(_party_log)
    p = 0
    for row in rows:
        if row["office"] in META_OFFICES:
            continue
        key = (row["office"], row["district"])
        while p < len(hdrs) and (hdrs[p][0], hdrs[p][1]) != key:
            p += 1
        if p >= len(hdrs):
            warnings.append(f"{precinct}: no contest header for row {key} {row['candidate']!r}")
            continue
        if not hdrs[p][2] and row["candidate"] not in NO_PARTY_CANDIDATES:
            warnings.append(
                f"{precinct}: contest {key} has no party prefix "
                f"(row {row['candidate']!r})"
            )
        row["party"] = hdrs[p][2]


def run_precinct(src, dst):
    lines = txt_lines(Path(src))
    segs = list(precinct_segments(lines, PRECINCT_CFG))
    all_names = {re.sub(r"\s{2,}", " ", PRECINCT_CFG.prettify_precinct(nm)).strip()
                 for nm, _ in segs if nm}
    all_names |= {nm.strip() for nm, _ in segs if nm}
    rows = []
    warnings = []
    precinct_count = 0
    unnamed = 0
    for name, seg in segs:
        if name is None:
            unnamed += 1
            continue
        precinct_count += 1
        pretty = re.sub(r"\s{2,}", " ", PRECINCT_CFG.prettify_precinct(name)).strip()
        del _party_log[:]
        seg_rows = parse_segment(pretty, seg, PRECINCT_CFG, warnings, all_names)
        _assign_parties(seg_rows, warnings, pretty)
        rows.extend(seg_rows)

    with open(dst, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=[
            "county", "precinct", "office", "district", "party", "candidate",
            "votes", "election_day", "mail", "provisional",
        ])
        w.writeheader()
        w.writerows(rows)

    print(f"wrote {len(rows)} rows across {precinct_count} precincts -> {dst}")
    if unnamed:
        print(f"Skipped {unnamed} unnamed (county-summary) Statistics segment(s)")
    if warnings:
        print(f"WARNING: {len(warnings)} unmatched line(s):")
        for w in warnings[:40]:
            print("  " + w)
        if len(warnings) > 40:
            print("  ...")
    else:
        print("no unmatched lines")
    return len(rows)


# ---------------------------------------------------------------------------

def to_text(src):
    if src.lower().endswith(".pdf"):
        out = tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False)
        out.close()
        import subprocess
        subprocess.run(["pdftotext", "-layout", src, out.name], check=True)
        return out.name
    return src


def main(argv):
    if len(argv) != 3:
        sys.exit(f"Usage: {argv[0]} <summary.txt|precinct.txt|pdf> <output.csv>")
    src = to_text(argv[1])
    with open(src, encoding="utf-8", errors="replace") as fh:
        text = fh.read()
    if re.search(r"^[ \t]*Precinct Summary - ", text, re.M):
        return run_precinct(src, argv[2])
    return run_county(src, argv[2])


if __name__ == "__main__":
    main(sys.argv)