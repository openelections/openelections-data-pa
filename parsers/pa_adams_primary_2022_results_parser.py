#!/usr/bin/env python3
"""
Parser for Adams County, PA 2022 General Primary (2022-05-17) precinct results.

Source: ``Adams PA RecountPrecinctSummaryPrimary2022.pdf``
(Electionware "Summary Results Report", Precinct Summary variant, 350
pages, generated 06/24/2022 -- OFFICIAL RESULTS).

Composition note: this is the ONLY per-precinct file Adams has in the
2022 primary sources (there is no non-recount precinct PDF; the matching
``Adams PA RecountElectionSummaryPrimary2022.pdf`` is the countywide
summary from the same 06/24 run). The "Recount" name notwithstanding,
the file carries every primary contest for all 50 precincts (DEM and
REP U.S. Senate, Governor, Lieutenant Governor, U.S. House, State
House 91/193, State Committee and the local Democratic Committee
Member contests), so it is used wholesale. It is POST-recount
official, which is required for the GOP U.S. Senate race (Oz v
McCormick June recount): its countywide sums match the ENR anchor
exactly (e.g. Oz 5,337 / Barnette 4,369 / McCormick 4,298).

Format notes:
  - Contest headers are party-prefixed: "DEM United States Senator",
    "REP Governor", followed by "Vote For N".
  - Statistics block: "Registered Voters - Total/Democratic/Republican/
    NONPARTISAN" (value on the same line); "Ballots Cast - Total/
    Democratic/Republican/NONPARTISAN/Blank" and turnout percent lines
    are printed with their values on the numbers-only line ABOVE the
    label.
  - Candidate rows: "<NAME> TOTAL PCT ED MAIL PROV" (VOTE % column
    stripped). "Write-In Totals" -> candidate "Write Ins" carrying the
    contest's party (primary convention: party-empty write-in rows fail
    the duplicate_entries test); "Not Assigned" detail row skipped;
    "Total Votes Cast" and "Contest Totals" skipped. Overvotes/
    Undervotes are not printed in this file.
  - The county occupies one congressional district (13th -- Rep. Joyce,
    confirmed by the ENR county anchor), but the primary file prints
    the contest as "Representative in Congress" without a district
    number; district 13 is assigned.
  - "Member of Republican State Committee" matches the ENR anchor's
    office name exactly and is kept; each precinct's local county-
    committee contest (e.g. "Democratic Committee Member Abbottstown")
    is kept as printed, minus the DEM party prefix.
  - Precinct names print as "Cumberland #1"; the "#N" suffix is
    normalized to " N" (same convention as the county's 2022 general
    and 2023 files).

Usage:
    python parsers/pa_adams_primary_2022_results_parser.py \
        "<input.txt|input.pdf>" "<output.csv>"
"""

import csv
import re
import subprocess
import sys
import tempfile
from pathlib import Path

COUNTY = "Adams"

FIELDNAMES = [
    "county", "precinct", "office", "district", "party", "candidate",
    "votes", "election_day", "mail", "provisional",
]

US_HOUSE_DISTRICT = "13"  # Adams == 13th Congressional District in 2022

JUNK_SUBSTR = (
    "Summary Results Report",
    "OFFICIAL RESULTS",
    "Report generated with Electionware",
    "Precinct Summary - ",
    "Election Summary - ",
)
FOOTER_RE = re.compile(r"\bPage \d+ of \d+\b")
HEADER_RE = re.compile(r"(?i)^General Primary$|^May 17, 2022\b|ADAMS COUNTY, PENNSYLVANIA")
COLUMN_HEADER_RE = re.compile(r"(?i)\b(?:VOTE %|Mail Votes|Provisional Votes|Election Day)\b")
STAT_MARKER_RE = re.compile(r"^STATISTICS\b", re.IGNORECASE)
VOTE_FOR_RE = re.compile(r"Vote For (\d+)", re.IGNORECASE)
PARTY_PREFIX_RE = re.compile(r"^(DEM|REP)\s+(\S.*)$")
NUM_TOKEN_RE = re.compile(r"^\d[\d,]*(?:\.\d+)?%?$")

US_SENATE_RE = re.compile(r"^United States Senator$", re.IGNORECASE)
GOVERNOR_RE = re.compile(r"^Governor$", re.IGNORECASE)
LT_GOVERNOR_RE = re.compile(r"^Lieutenant Governor$", re.IGNORECASE)
CONGRESS_RE = re.compile(r"^Representative in Congress$", re.IGNORECASE)
ASSEMBLY_RE = re.compile(
    r"^Representative in the General Assembly\s+(\d+)(?:st|nd|rd|th)?\s+District$",
    re.IGNORECASE,
)


def normalize_office(title: str):
    """(office title minus DEM/REP prefix) -> (office, district)."""
    title = re.sub(r"\s+", " ", title).strip()
    if US_SENATE_RE.match(title):
        return ("U.S. Senate", "")
    if GOVERNOR_RE.match(title):
        return ("Governor", "")
    if LT_GOVERNOR_RE.match(title):
        return ("Lieutenant Governor", "")
    if CONGRESS_RE.match(title):
        return ("U.S. House", US_HOUSE_DISTRICT)
    m = ASSEMBLY_RE.match(title)
    if m:
        return ("State House", m.group(1))
    # "Member of Republican State Committee" and local offices
    # (e.g. "Democratic Committee Member Abbottstown") keep as printed.
    return (title, "")


def prettify_precinct(name: str) -> str:
    # "Cumberland #1" -> "Cumberland 1" (matches the county's other files).
    return re.sub(r"\s*#\s*(\d+)\s*$", r" \1", name.strip())


def trailing_numbers(line: str) -> list:
    tokens = line.split()
    out = []
    for tok in reversed(tokens):
        if NUM_TOKEN_RE.match(tok):
            out.append(tok)
        else:
            break
    out.reverse()
    return out


def is_numbers_only(line: str) -> bool:
    tokens = line.split()
    return bool(tokens) and all(NUM_TOKEN_RE.match(t) for t in tokens)


def clean_num(tok: str) -> str:
    return tok.replace(",", "")


RV_PARTY = {
    "TOTAL": "",
    "DEMOCRATIC": "DEM",
    "REPUBLICAN": "REP",
    "NONPARTISAN": "NONPARTISAN",
}
BC_PARTY = dict(RV_PARTY)


def parse_segments(lines):
    """Yield (precinct_name_raw, segment_lines) per STATISTICS marker."""
    markers = [i for i, ln in enumerate(lines) if STAT_MARKER_RE.match(ln.strip())]
    if not markers:
        raise RuntimeError("No STATISTICS markers found; wrong file format?")
    bounds = markers + [len(lines)]
    for k, start in enumerate(markers):
        name = None
        # Scan up from the marker: the page header's last line before the
        # STATISTICS block is the precinct name.
        for i in range(start - 1, -1, -1):
            s = lines[i].strip()
            if not s or STAT_MARKER_RE.match(s):
                continue
            if any(sub in s for sub in JUNK_SUBSTR):
                continue
            name = s
            break
        yield name, lines[start:bounds[k + 1]]


def parse_segment(name, seg, warnings):
    seg = [ln.strip() for ln in seg]
    n = len(seg)

    # Office headers: lines whose next non-empty line is "Vote For N".
    office_header_idx = set()
    for i, ln in enumerate(seg):
        if not ln:
            continue
        for j in range(i + 1, n):
            if not seg[j]:
                continue
            if VOTE_FOR_RE.match(seg[j]):
                office_header_idx.add(i)
            break

    rows = []
    contest = None  # (office, district, party)
    buf = None  # numeric tokens read from a numbers-only line

    def emit(office, district, party, candidate, nums):
        if len(nums) == 4:
            total, ed, mail, prov = nums
        else:
            total, ed, mail, prov = nums[0], "", "", ""
        rows.append({
            "county": COUNTY, "precinct": name,
            "office": office, "district": district, "party": party,
            "candidate": candidate,
            "votes": total, "election_day": ed, "mail": mail,
            "provisional": prov,
        })

    for idx, raw in enumerate(seg):
        line = raw.strip()
        if not line:
            continue
        if STAT_MARKER_RE.match(line):
            contest = None
            buf = None
            continue
        if idx in office_header_idx:
            buf = None
            pm = PARTY_PREFIX_RE.match(line)
            if pm:
                party = pm.group(1)
                title = pm.group(2)
            else:
                warnings.append(f"{name}: contest header without party prefix: {line!r}")
                party, title = "", line
            office, district = normalize_office(title)
            contest = (office, district, party)
            continue
        if VOTE_FOR_RE.match(line):
            buf = None
            continue
        if any(sub in line for sub in JUNK_SUBSTR) or FOOTER_RE.search(line):
            continue
        if HEADER_RE.match(line) or COLUMN_HEADER_RE.match(line):
            continue
        if line == name.strip():
            # Repeated page header (continuation page of this precinct).
            continue

        # Statistics labels.
        m = re.match(r"^Registered Voters\s*-\s*(\w+)", line)
        if m:
            nums = [clean_num(t) for t in trailing_numbers(line) if not t.endswith("%")]
            if len(nums) == 1:
                emit("Registered Voters", "", RV_PARTY.get(m.group(1).upper(), ""), "", nums)
            else:
                warnings.append(f"{name}: RV unparsed: {line!r}")
            buf = None
            continue
        m = re.match(r"^Ballots Cast\s*-\s*(\w+)", line)
        if m:
            nums = [clean_num(t) for t in trailing_numbers(line) if not t.endswith("%")]
            if not nums and buf and len(buf) == 4:
                nums = buf
            if len(nums) == 4:
                office = "Ballots Cast - Blank" if m.group(1).upper() == "BLANK" else "Ballots Cast"
                emit(office, "", BC_PARTY.get(m.group(1).upper(), ""), "", nums)
            else:
                warnings.append(f"{name}: Ballots Cast unparsed: {line!r} buf={buf}")
            buf = None
            continue
        if re.match(r"^Voter Turnout", line):
            buf = None
            continue

        # Numbers-only lines: values for the statistics label that follows.
        if is_numbers_only(line):
            raw_tail = trailing_numbers(line)
            if not (len(raw_tail) == 1 and raw_tail[0].endswith("%")):
                buf = [clean_num(t) for t in raw_tail]
            continue

        # Candidate / total rows.
        raw_tail = trailing_numbers(line)
        head = " ".join(line.split()[: -len(raw_tail)]) if raw_tail else line
        nums = [clean_num(t) for t in raw_tail if not t.endswith("%")]
        if not nums:
            continue
        buf = None
        upper = head.upper()
        if upper in ("TOTAL VOTES CAST", "CONTEST TOTALS", "NOT ASSIGNED"):
            continue
        if contest is None:
            warnings.append(f"{name}: row with no contest: {line!r}")
            continue
        if upper == "WRITE-IN TOTALS" or upper.startswith("WRITE-IN:"):
            office, district, party = contest
            emit(office, district, party, "Write Ins", nums)
            continue
        office, district, party = contest
        emit(office, district, party, head, nums)
    return rows


def parse_txt(path: Path):
    lines = path.read_text(encoding="utf-8", errors="replace").split("\n")
    warnings = []
    rows = []
    counts = {}
    for raw_name, seg in parse_segments(lines):
        if raw_name is None:
            warnings.append("unnamed statistics segment skipped")
            continue
        pretty = prettify_precinct(raw_name)
        rows.extend(parse_segment(pretty, seg, warnings))
        counts[pretty] = counts.get(pretty, 0) + 1
    return rows, counts, warnings


def main(argv):
    if len(argv) != 3:
        sys.exit(f"Usage: {Path(argv[0]).name} <input.txt|input.pdf> <output.csv>")
    src = Path(argv[1])
    if src.suffix.lower() == ".pdf":
        with tempfile.NamedTemporaryFile(suffix=".txt", delete=False) as tf:
            tmp = Path(tf.name)
        subprocess.run(["pdftotext", "-layout", str(src), str(tmp)], check=True)
        src = tmp
    rows, counts, warnings = parse_txt(src)
    out_path = Path(argv[2])
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)
    print(f"Wrote {len(rows)} rows across {len(counts)} precincts to {out_path}")
    for name, c in sorted(counts.items()):
        if c != 1:
            print(f"  NOTE: {name}: {c} segments")
    if warnings:
        print(f"WARNING: {len(warnings)} unmatched lines:")
        for w in warnings[:40]:
            print("  " + w)
        if len(warnings) > 40:
            print("  ...")


if __name__ == "__main__":
    main(sys.argv)