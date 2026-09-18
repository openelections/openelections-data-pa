#!/usr/bin/env python3
"""
Parser for Chester County, PA 2023 Municipal Primary (May 16, 2023).

Source: Electionware reports (same family as the county's 2023 general):
  - "Summary Results Report" (county summary, 230 precincts consolidated)
        -> county-level 9-column CSV
        county,office,district,party,candidate,votes,election_day,mail,provisional
  - "Precinct Summary Results Report" (per-precinct, 2240 pages, 230 precincts)
        -> precinct 10-column CSV (precinct inserted as 2nd column)

Input mode is auto-detected from the report title line; both .txt
(pdftotext -layout extract) and .pdf inputs are accepted.

Primary-specific handling (vs. pa_chester_general_2023_results_parser):
  - Office headers carry the party: "DEM Justice of the Supreme Court" /
    "REP Township Supervisor Westtown Township". The prefix is stripped and
    the party (DEM/REP) is applied to every candidate row of the contest
    (candidate rows in this report carry no party token). Unprefixed
    headers (the two borough referenda) are non-partisan.
  - Statistics blocks list per-party "Registered Voters - <party>" and
    "Ballots Cast - <party>" rows; per-party ballots-cast rows are not
    recorded (repo convention). Only Registered Voters - Total,
    Ballots Cast - Total and Ballots Cast - Blank are emitted.
  - The "Absentee/Mail-In" column maps to `mail` (same as the general's
    "Mail Votes" column).
  - Office/district normalization reuses the county's 2023 general parser
    ("Member of Council" -> "Borough Council", "Auditor <muni>" ->
    "Township Auditor", "Unexpired N Year Term" -> office suffix, school
    director regions, magisterial district judges, township district
    supervisors).
  - The shared county-summary engine (pa_2023_summary_common.parse_esr)
    parses the county summary; the per-precinct report is parsed with the
    electionware_txt segment machinery extended for header-carried party.

Usage:
    python parsers/pa_chester_primary_2023_results_parser.py \
        "<input.txt|input.pdf>" "<output.csv>"
"""

import csv
import re
import subprocess
import sys
import tempfile
from pathlib import Path

from pa_chester_general_2023_results_parser import normalize_office
from electionware_precinct_np import VOTE_FOR_RE
from electionware_txt import (
    TxtConfig,
    is_junk as txt_is_junk,
    numbers_only,
    precinct_segments,
    strip_percent,
    trailing_numbers,
)

COUNTY = "Chester"

FIELDNAMES = ["county", "office", "district", "party", "candidate",
              "votes", "election_day", "mail", "provisional"]

# ---------------------------------------------------------------------------
# Header mapping: strip the "DEM "/"REP " contest-party prefix, then apply the
# county's general-2023 office/district normalization to the remainder.
# ---------------------------------------------------------------------------

PARTY_PREFIX_RE = re.compile(r"^(DEM|REP)\s+(.+)$", re.IGNORECASE)


def map_header(line: str):
    """(office, district, party) for a contest header line."""
    h = re.sub(r"\s+", " ", line.strip())
    m = PARTY_PREFIX_RE.match(h)
    if not m:
        office, district = normalize_office(h)
        return office, district, ""
    party, rest = m.group(1).upper(), m.group(2).strip()
    office, district = normalize_office(rest)
    return office, district, party


# ---------------------------------------------------------------------------
# County summary: parse via the shared parse_esr engine. The contest party is
# smuggled into the office string (unit-separator marker) because parse_esr
# takes party from candidate-row tokens only; a post-pass moves it into the
# party column and blanks it for metadata rows. Write-ins/Overvotes/Undervotes
# rows carry the CONTEST's party (repo primary convention); Registered
# Voters / Ballots Cast rows stay party-empty.
# ---------------------------------------------------------------------------

def run_county(src: str, dst: str):
    """County summary: same row grammar as the precinct report, so the whole
    file is parsed as a single segment with the party-aware engine.

    (The shared parse_esr engine is not used for this file: its page-junk
    filter treats any line starting with "page " as page furniture, which
    silently drops candidate rows whose first name is "Page" -- e.g.
    "Page Allinson" (DEM Auditor Willistown Township, 1,280 votes) is
    deleted by parse_esr. The electionware_txt junk rules do not have this
    collision, so both reports go through the same row engine here.)"""
    lines = read_lines(src)
    rows = parse_segment_primary("", lines, COUNTY, [], set())
    rows = merge_split_aggregates_party(rows)
    n = 0
    with open(dst, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(FIELDNAMES)
        for r in rows:
            w.writerow([r["county"], r["office"], r["district"], r["party"],
                        r["candidate"], r["votes"], r["election_day"],
                        r["mail"], r["provisional"]])
            n += 1
    print(f"wrote {n} rows -> {dst}")
    print("note: source has no 'Total Votes Cast' lines; contest totals "
          "reconciled against the precinct report aggregation in "
          "work2023p/validate/chester.md")


# ---------------------------------------------------------------------------
# Precinct report.
# ---------------------------------------------------------------------------

PRECINCT_FIELDNAMES = [
    "county", "precinct", "office", "district", "party", "candidate",
    "votes", "election_day", "mail", "provisional",
]

STAT_RE = re.compile(r"^STATISTICS\b", re.IGNORECASE)

# statistics labels that are parsed as data / deliberately skipped
SKIP_STATS_PREFIXES = (
    "Registered Voters - Democratic", "Registered Voters - Republican",
    "Registered Voters - Non Partisan", "Registered Voters - NonPartisan",
    "Ballots Cast - Democratic", "Ballots Cast - Republican",
    "Ballots Cast - Non Partisan", "Ballots Cast - NonPartisan",
    "Voter Turnout",
)

AGG_CANDIDATES = ("Write-ins", "Overvotes", "Undervotes")


def merge_split_aggregates_party(rows):
    """Same as the shared engine's _merge_split_aggregates, but the
    "same office" run key includes the contest party so DEM/REP sections of
    one office are never merged together."""
    merged = []
    agg_index = {}
    current_key = None
    for row in rows:
        office_key = (row["office"], row["district"], row["party"])
        if office_key != current_key:
            current_key = office_key
            agg_index = {}
        key = office_key + (row["candidate"],)
        if row["candidate"] in AGG_CANDIDATES and key in agg_index:
            prev = merged[agg_index[key]]
            for field in ("votes", "election_day", "mail", "provisional"):
                prev[field] = str(int(prev.get(field) or 0) +
                                  int(row.get(field) or 0))
        else:
            merged.append(row)
            if row["candidate"] in AGG_CANDIDATES:
                agg_index[key] = len(merged) - 1
    return merged


def parse_segment_primary(name, seg, county, warnings, precinct_names):
    """Parse one precinct's segment (Statistics block + contest sections)."""
    rows = []
    office = district = party = None
    last_numeric = None

    lines = [ln.strip() for ln in seg]
    n = len(lines)

    # office headers: line whose next non-empty line is "Vote For N"
    office_header_idx = set()
    for i, ln in enumerate(lines):
        if not ln:
            continue
        for j in range(i + 1, n):
            if lines[j]:
                if VOTE_FOR_RE.match(lines[j]):
                    office_header_idx.add(i)
                break

    for idx, line in enumerate(lines):
        if not line:
            continue

        if STAT_RE.match(line):
            office = district = party = None
            last_numeric = None
            continue

        if idx in office_header_idx:
            office, district, party = map_header(line)
            continue

        if VOTE_FOR_RE.match(line):
            continue

        if txt_is_junk(line, CFG):
            continue

        # continuation pages repeat the precinct name
        if line in precinct_names:
            continue

        # statistics block
        if line.startswith(("Registered Voters", "Ballots Cast")):
            if line.startswith(SKIP_STATS_PREFIXES):
                continue  # per-party / turnout rows: not recorded
            if line.startswith(("Registered Voters - Total",
                                "Registered Voters-Total")):
                label = "Registered Voters"
            elif line.startswith(("Ballots Cast - Total",
                                  "Ballots Cast-Total")):
                label = "Ballots Cast"
            elif line.startswith(("Ballots Cast - Blank",
                                  "Ballots Cast-Blank")):
                label = "Ballots Cast - Blank"
            else:
                warnings.append(f"{name}: unparsed stats line: {line!r}")
                continue
            nums = [t.replace(",", "") for t in
                    strip_percent(trailing_numbers(line))]
            if not nums and last_numeric:
                nums = [t.replace(",", "") for t in
                        strip_percent(last_numeric)]
            if nums and len(nums) == 4:
                rows.append(make_row(name, label, "", "", "", nums[0],
                                     nums[1], nums[2], nums[3]))
            elif nums and len(nums) == 1:
                rows.append(make_row(name, label, "", "", "", nums[0],
                                     "", "", ""))
            elif nums:
                warnings.append(f"{name}: {line!r}: unexpected tokens {nums}")
            continue

        if numbers_only(line):
            last_numeric = trailing_numbers(line)
            continue

        raw_tail = trailing_numbers(line)
        nums = strip_percent(raw_tail)
        head = " ".join(line.split()[: -len(raw_tail)]) if raw_tail else line

        if not nums:
            continue  # column-header junk / wrapped text

        nums = [t.replace(",", "") for t in nums]

        if office is None:
            warnings.append(f"{name}: candidate row with no office: {line!r}")
            continue

        if head.upper().startswith("WRITE-IN:"):
            continue  # detail rows fold into the "Write-In Totals" aggregate

        if len(nums) == 4:
            total, ed, mail, prov = nums
        elif len(nums) == 1:
            total, ed, mail, prov = nums[0], "", "", ""
        else:
            warnings.append(f"{name}: odd token count {nums} for {head!r}")
            continue

        upper = head.upper()
        if upper == "WRITE-IN TOTALS":
            rows.append(make_row(name, office, district, party or "",
                                 "Write-ins", total, ed, mail, prov))
            continue
        if upper == "OVERVOTES":
            rows.append(make_row(name, office, district, party or "",
                                 "Overvotes", total, ed, mail, prov))
            continue
        if upper == "UNDERVOTES":
            rows.append(make_row(name, office, district, party or "",
                                 "Undervotes", total, ed, mail, prov))
            continue
        if upper in ("YES", "NO"):
            rows.append(make_row(name, office, district, "",
                                 upper.capitalize(), total, ed, mail, prov))
            continue
        if upper in ("NOT ASSIGNED", "TOTAL VOTES CAST", "CONTEST TOTALS",
                     "NO CANDIDATE FILED"):
            continue

        rows.append(make_row(name, office, district, party or "", head,
                             total, ed, mail, prov))

    return rows


def make_row(precinct, office, district, party, candidate,
             votes, ed, mail, prov):
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


PRECINCT_CODE_RE = re.compile(r"^\d{3}\s+")


def prettify_precinct(name: str) -> str:
    return PRECINCT_CODE_RE.sub("", name.strip(), count=1)


CFG = TxtConfig(county=COUNTY, normalize_office=lambda l: ("", ""),
                prettify_precinct=prettify_precinct)


def run_precinct(src: str, dst: str):
    lines = read_lines(src)
    rows, warnings, precinct_count = [], [], 0
    segs = list(precinct_segments(lines, CFG))
    all_names = {re.sub(r"\s{2,}", " ", prettify_precinct(nm)).strip()
                 for nm, _ in segs if nm}
    all_names |= {nm.strip() for nm, _ in segs if nm}
    for name, seg in segs:
        if name is None:
            warnings.append("unnamed Statistics segment skipped")
            continue
        precinct_count += 1
        pretty = re.sub(r"\s{2,}", " ", prettify_precinct(name)).strip()
        rows.extend(parse_segment_primary(pretty, seg, COUNTY, warnings,
                                          all_names))
    rows = merge_split_aggregates_party(rows)

    out = Path(dst)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=PRECINCT_FIELDNAMES)
        w.writeheader()
        w.writerows(rows)
    print(f"wrote {len(rows)} rows across {precinct_count} precincts -> {dst}")
    if warnings:
        print(f"WARNING: {len(warnings)} problems:")
        for wmsg in warnings[:40]:
            print("  " + wmsg)
        if len(warnings) > 40:
            print("  ...")


# ---------------------------------------------------------------------------
# Top level.
# ---------------------------------------------------------------------------

def read_lines(src: str):
    path = Path(src)
    if path.suffix.lower() == ".pdf":
        tmp = tempfile.NamedTemporaryFile(suffix=".txt", delete=False)
        tmp.close()
        subprocess.run(["pdftotext", "-layout", str(path), tmp.name],
                       check=True)
        return Path(tmp.name).read_text(
            encoding="utf-8", errors="replace").split("\n")
    return path.read_text(encoding="utf-8", errors="replace").split("\n")


def main(argv):
    if len(argv) != 3:
        sys.exit(f"Usage: {Path(argv[0]).name} <input.txt|input.pdf> "
                 f"<output.csv>")
    src, dst = argv[1], argv[2]
    first = next((l.strip() for l in read_lines(src) if l.strip()), "")
    if first.startswith("Precinct Summary Results Report"):
        run_precinct(src, dst)
    else:
        run_county(src, dst)


if __name__ == "__main__":
    main(sys.argv)