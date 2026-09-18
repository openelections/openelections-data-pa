#!/usr/bin/env python3
"""
Juniata County PA 2023 Primary (May 16, 2023) results parser.

Two source files, both Electionware "Summary Results Report" prints whose
party affiliation lives on the CONTEST HEADER line ("DEM JUSTICE OF THE
SUPREME COURT" / "REP COUNTY COMMISSIONER"), not on the candidate rows:

  * County summary  -> county-level CSV (9-column schema, no precinct col).
      Parsed via the shared ``pa_2023_summary_common.parse_esr`` engine, with
      two Juniata-primary quirks handled in the wrapper:
        1. The STATISTICS loop in the shared engine only stops at lines
           starting with an office word; every Juniata contest header is
           DEM/REP-prefixed, so nothing would break the loop and the first
           contests would be swallowed.  A single "COUNTY" sentinel line is
           inserted after the "Voter Turnout" block to end the stats block.
        2. ``parse_esr`` keeps the party only in the map_contest return
           value, so the wrapper marks it into the office string
           ("DEM|County Commissioner") and stamps the marker onto candidate
           rows afterwards (Write-ins / Overvotes / Undervotes / metadata
           rows keep an empty party, per the primary brief).

  * Precinct report -> precinct CSV (10-column schema, "precinct" 2nd).
      Electionware per-precinct report, 393 pages / 18 precincts, same
      wrapped-statistics layout as the county's 2023 general report; parsed
      with the shared ``electionware_txt`` segment/junk machinery and a
      party-aware row loop (the office header line carries the party).
      Office normalization is reused unchanged from the county's 2023
      general parser; precinct names are mapped to the county's 2023
      general precinct naming.

Usage:
    python parsers/pa_juniata_primary_2023_results_parser.py <input> <output>

The mode is picked from the input file name (a "precinct" report vs a
county "summary" report).
"""

import csv
import os
import re
import sys
import tempfile

from pa_2023_summary_common import parse_esr, FIELDNAMES
from electionware_txt import (
    TxtConfig,
    is_junk,
    precinct_segments,
    strip_percent,
    trailing_numbers,
)
from electionware_precinct_np import (
    VOTE_FOR_RE,
    _merge_split_aggregates,
)

# Office normalization reused verbatim from the 2023 general parser.
from pa_juniata_general_2023_results_parser import (
    BOROUGH_RE,
    EXACT_OFFICES,
    MDJ_RE,
    SCHOOL_RE,
    TOWNSHIP_RE,
    normalize_office as _general_normalize_office,
)

COUNTY = "Juniata"

PARTY_PREFIX_RE = re.compile(r"^(DEM|REP)\s+(.+)$", re.IGNORECASE)
PARTY_MARKER_RE = re.compile(r"^(DEM|REP)\|(.*)$")

# Aggregate rows keep an empty party column (primary brief + repo general
# convention); only real candidates carry the contest party.
NOPARTY_CANDIDATES = {"Write-ins", "Overvotes", "Undervotes",
                      "No Candidate Filed"}


def normalize_office_primary(line: str):
    """Primary contest header -> (marker_office, district, party).

    Strips the DEM/REP prefix, normalizes the office with the county's
    2023 general mapping, and prefixes the party as a marker that the
    summary post-processing stamps onto candidate rows.
    """
    m = PARTY_PREFIX_RE.match(re.sub(r"\s+", " ", line.strip()))
    party, rest = (m.group(1), m.group(2).strip()) if m else ("", line.strip())
    office, district = _general_normalize_office(rest)
    return f"{party}|{office}" if party else office, district, party


# ---------------------------------------------------------------------------
# County-level summary (Electionware ESR via the shared parse_esr engine).
# ---------------------------------------------------------------------------

def _break_stats_block(text_path: str) -> str:
    """Trim the STATISTICS block so parse_esr captures all metadata.

    parse_esr's statistics loop stops at the first line starting with an
    office word -- which includes "Registered Voters -" -- and Juniata's
    primary headers are all DEM/REP-prefixed, so without help the loop
    would (a) break at "Registered Voters - Democratic", losing the
    "Ballots Cast - Total" / "- Blank" rows, and (b) swallow the first
    contests otherwise.  Rewrite the stats block to contain only the three
    meta lines the engine consumes, followed by a "COUNTY" sentinel line
    that ends the block cleanly.
    """
    with open(text_path, encoding="utf-8") as fh:
        lines = [l.rstrip() for l in fh]
    out = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.strip().upper().startswith("STATISTICS"):
            out.append(line)
            i += 1
            while i < len(lines):
                s = lines[i].strip()
                if re.match(r"^(DEM|REP)\s", s):
                    break
                if s.startswith(("Registered Voters - Total",
                                 "Ballots Cast - Total",
                                 "Ballots Cast - Blank")):
                    out.append(lines[i])
                i += 1
            out.append("COUNTY")
            continue
        out.append(line)
        i += 1
    tf = tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False)
    tf.write("\n".join(out) + "\n")
    tf.close()
    return tf.name


def parse_summary(text_path: str, output_path: str) -> None:
    prepped = _break_stats_block(text_path)
    R = parse_esr(prepped, COUNTY, map_contest, titlecase_names=False)
    n_bad = reconcile_summary(R)
    n = write_summary_csv(R, output_path)
    print(f"wrote {n} rows -> {output_path}")
    print(f"contests parsed: {len(R.contests)}")
    if n_bad:
        for (office, dist, party), a, t in n_bad:
            print(f"WARNING {office} | {dist} | {party}: rows sum {a} != "
                  f"Total Votes Cast {t}")
        sys.exit(1)
    print("contest totals check: all contests reconcile")


def map_contest(header):
    office, district, party = normalize_office_primary(header)
    if "|" not in office:
        # Unrecognized contest header (no DEM/REP prefix and no mapping).
        sys.stderr.write(f"WARNING unmapped contest header: {header!r}\n")
    return office, district, party


def reconcile_summary(R):
    """Party-aware contest-totals reconciliation (3-keyed)."""
    from collections import defaultdict
    agg = defaultdict(int)
    for r in R.rows:
        m = PARTY_MARKER_RE.match(r[1])
        key = (r[1], r[2], m.group(1) if m else "")
        if r[4] in ("Overvotes", "Undervotes"):
            continue  # the source's "Total Votes Cast" excludes over/under
        try:
            agg[key] += int(r[5])
        except (TypeError, ValueError):
            pass
    bad = []
    for key, tot in R.totals.items():
        if tot is not None and agg.get(key, 0) != tot:
            bad.append((key, agg.get(key, 0), tot))
    return bad


def write_summary_csv(R, path):
    rows = []
    for r in R.rows:
        r = list(r)
        m = PARTY_MARKER_RE.match(r[1])
        if m:
            party, office = m.group(1), m.group(2)
            cand = r[4]
            r[1] = office
            # Every row of the section carries the contest party. The
            # DEM/REP sections of one contest print identical party-empty
            # Write-ins/Overvotes/Undervotes rows, which the repo's
            # duplicate_entries test (which ignores the vote columns)
            # would reject -- the 2024 primary files in this repo carry
            # the party on aggregate rows for the same reason.
            r[3] = party
            if cand not in NOPARTY_CANDIDATES:
                # Juniata's 2023 general file keeps candidates ALL-CAPS
                # (printed case); undo the shared CANON_NAMES title-casing.
                cand = cand.upper()
                if cand == "HARRY F. SMAIL JR.":
                    cand = "HARRY F. SMAIL, JR."  # as printed in the source
                r[4] = cand
        rows.append(r)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(FIELDNAMES)
        w.writerows(rows)
    return len(rows)


# ---------------------------------------------------------------------------
# Precinct report (Electionware per-precinct, wrapped statistics).
# ---------------------------------------------------------------------------

# Precinct name mapping to the county's 2023 general precinct naming.
_BOROUGH_PRECINCTS = {"PORT ROYAL", "MIFFLIN", "MIFFLINTOWN", "THOMPSONTOWN"}


def prettify_precinct(raw: str) -> str:
    name = re.sub(r"\s+", " ", raw.strip())
    if name.upper().startswith("TUSCARORA_"):
        tail = name.split("_", 1)[1]
        if tail.upper() == "TUSC":  # 2023 general file spells it out
            tail = "TUSCARORA"
        return "Tuscarora Twp_" + re.sub(r"[A-Z]+",
                                         lambda m: m.group(0).capitalize(), tail)
    if name.upper() in _BOROUGH_PRECINCTS:
        return name.title() + " Borough"
    return name.title() + " Township"


CFG = TxtConfig(
    county=COUNTY,
    normalize_office=normalize_office_primary,   # unused by the row loop
    prettify_precinct=prettify_precinct,
    extra_junk=(r"(?i)\bprimary election\b",),
)

META_SKIP_PREFIXES = (
    "Registered Voters -",
    "Ballots Cast -",
    "Voter Turnout",
    "Election Day Precincts Reporting",
    "Precincts Complete",
    "Precincts Partially Reported",
    "Absentee/",
)


def parse_precinct_segment(name, seg, warnings, all_names):
    lines = [ln.strip() for ln in seg]
    n = len(lines)

    # Office headers: next non-empty line is "Vote For N".
    office_header_idx = set()
    for i, ln in enumerate(lines):
        if not ln:
            continue
        for j in range(i + 1, n):
            nxt = lines[j]
            if not nxt:
                continue
            if VOTE_FOR_RE.match(nxt):
                office_header_idx.add(i)
            break

    rows = []
    current = None        # (office, district, party)
    last_numeric = None

    for idx, raw in enumerate(lines):
        line = raw.strip()
        if not line:
            continue
        if re.match(r"^STATISTICS\b", line, re.IGNORECASE):
            current = None
            last_numeric = None
            continue
        if idx in office_header_idx:
            office, district, party = normalize_office_primary(line)
            if party == "":
                sys.stderr.write(
                    f"WARNING {name}: unmapped contest header {line!r}\n")
            current = (office.split("|", 1)[-1], district, party)
            last_numeric = None
            continue
        if VOTE_FOR_RE.match(line):
            continue
        if is_junk(line, CFG):
            continue
        if line in all_names:
            continue  # continuation-page precinct banner
        if line.startswith(META_SKIP_PREFIXES):
            if line.startswith("Registered Voters - Total"):
                nums = [t.replace(",", "")
                        for t in strip_percent(_tail(line))]
                if len(nums) == 1:
                    rows.append(_row(name, "Registered Voters", "", "", "",
                                     nums[0], "", "", ""))
                else:
                    sys.stderr.write(
                        f"WARNING {name}: Registered Voters unparsed: "
                        f"{line!r}")
            elif line.startswith("Ballots Cast - Total"):
                nums = _nums4(line, last_numeric, name, "Ballots Cast")
                if nums:
                    rows.append(_row(name, "Ballots Cast", "", "", "",
                                     *nums))
            elif line.startswith("Ballots Cast - Blank"):
                nums = _nums4(line, last_numeric, name, "Ballots Cast - Blank")
                if nums:
                    rows.append(_row(name, "Ballots Cast - Blank", "", "",
                                     "", *nums))
            last_numeric = None
            continue
        if line.split() and all(re.match(r"^[\d,.]+%?$", t)
                                for t in line.split()):
            last_numeric = _tail(line)
            continue

        raw_tail = _tail(line)
        nums = [t.replace(",", "") for t in strip_percent(raw_tail)]
        head = " ".join(line.split()[:-len(raw_tail)]) if raw_tail else line
        if not nums:
            continue  # column-header junk / wrapped text
        if head.upper().startswith("WRITE-IN:"):
            continue

        if len(nums) == 4:
            total, ed, mail, prov = nums
        elif len(nums) == 1:
            total, ed, mail, prov = nums[0], "", "", ""
        else:
            sys.stderr.write(f"WARNING {name}: odd token count {nums} "
                             f"for {head!r}")
            continue

        upper = head.upper()
        office, district, party = current if current else ("", "", "")
        if upper == "WRITE-IN TOTALS":
            # Party is carried through the split-aggregate merge, then
            # blanked (repo convention keeps aggregate rows party-empty).
            rows.append(_row(name, office, district, "P:" + party,
                             "Write-ins", total, ed, mail, prov))
        elif upper == "OVERVOTES":
            rows.append(_row(name, office, district, "P:" + party,
                             "Overvotes", total, ed, mail, prov))
        elif upper == "UNDERVOTES":
            rows.append(_row(name, office, district, "P:" + party,
                             "Undervotes", total, ed, mail, prov))
        elif upper in ("NOT ASSIGNED", "TOTAL VOTES CAST", "CONTEST TOTALS"):
            continue
        elif upper in ("YES", "NO"):
            sys.stderr.write(f"WARNING {name}: unexpected {upper} row")
        elif upper == "NO CANDIDATE FILED":
            continue
        elif current is None:
            sys.stderr.write(
                f"WARNING {name}: candidate row with no office: {line!r}")
        else:
            rows.append(_row(name, office, district, party, head,
                             total, ed, mail, prov))

    merged = _merge_split_aggregates(rows)
    # Aggregate rows keep the contest party (repo primary convention; the
    # DEM/REP sections of one contest would otherwise print identical
    # party-empty Write-ins/Overvotes/Undervotes rows and fail the repo's
    # duplicate_entries test, which ignores the vote columns).
    for r in merged:
        if r["party"].startswith("P:"):
            r["party"] = r["party"][2:]
    return merged


def _tail(line, limit=8):
    out = []
    for tok in reversed(line.split()):
        if re.match(r"^[\d,.]+%?$", tok):
            out.append(tok)
        else:
            break
        if len(out) >= limit:
            break
    out.reverse()
    return out


def _nums4(line, last_numeric, name, label):
    nums = [t.replace(",", "") for t in strip_percent(_tail(line))]
    if not nums and last_numeric:
        nums = [t.replace(",", "") for t in strip_percent(last_numeric)]
    if len(nums) == 4:
        return nums
    if len(nums) == 1:
        return [nums[0], "", "", ""]
    sys.stderr.write(f"WARNING {name}: {label} tokens={nums}")
    return None


def _row(precinct, office, district, party, candidate, votes, ed, mail, prov):
    return {
        "county": COUNTY,
        "precinct": precinct,
        "office": office,
        "district": district,
        "party": party,
        "candidate": candidate,
        "votes": votes,
        "election_day": ed,
        "mail": mail,
        "provisional": prov,
    }


def parse_precinct(text_path: str, output_path: str) -> None:
    with open(text_path, encoding="utf-8") as fh:
        lines = fh.read().split("\n")
    segs = list(precinct_segments(lines, CFG))
    all_names = {nm.strip() for nm, _ in segs if nm}
    all_names |= {prettify_precinct(nm).strip() for nm, _ in segs if nm}
    rows = []
    warnings = 0
    for name, seg in segs:
        if name is None:
            warnings += 1
            sys.stderr.write("WARNING unnamed Statistics segment skipped\n")
            continue
        pretty = prettify_precinct(name)
        rows.extend(parse_precinct_segment(pretty, seg, None, all_names))
    precincts = sorted({r["precinct"] for r in rows})
    with open(output_path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(
            fh, fieldnames=["county", "precinct", "office", "district",
                            "party", "candidate", "votes", "election_day",
                            "mail", "provisional"])
        w.writeheader()
        w.writerows(rows)
    print(f"wrote {len(rows)} rows across {len(precincts)} precincts "
          f"-> {output_path}")
    if warnings:
        sys.exit(f"{warnings} skipped segment(s)")


# ---------------------------------------------------------------------------

def _as_text(path: str) -> str:
    """pdftotext -layout conversion for PDF inputs; passes .txt through."""
    if not path.lower().endswith(".pdf"):
        return path
    import subprocess
    tf = tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False)
    tf.close()
    subprocess.run(["pdftotext", "-layout", path, tf.name], check=True)
    return tf.name


def main(argv):
    if len(argv) != 3:
        sys.exit(f"Usage: {os.path.basename(argv[0])} "
                 f"<summary.pdf|txt|precinct.pdf|txt> <output.csv>")
    # mode is picked from the ORIGINAL file name, before PDF conversion
    src, dst = argv[1], argv[2]
    if "precinct" in os.path.basename(src).lower():
        parse_precinct(_as_text(src), dst)
    else:
        parse_summary(_as_text(src), dst)


if __name__ == "__main__":
    main(sys.argv)