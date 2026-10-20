#!/usr/bin/env python3
"""
Parser for Butler County, PA 2022 General Primary (2022-05-17) precinct results.

Source: ``Butler PA 2022 Primary Precinct.pdf`` (Electionware
"Precinct Summary Report", 7 pages, 89 precincts, generated 06/22/2022
15:17 PM -- OFFICIAL RESULTS). This run is POST-recount, so the GOP
U.S. Senate contest already carries the June 2022 recount results
(verify: countywide sums must match the ENR anchor, Oz 9,443 /
Barnette 7,432 / McCormick 11,900). One file, every contest, both
parties (DEM and REP), no separate recount file needed.

Format notes:
  - Contest headers are party-prefixed: "DEM UNITED STATES SENATOR
    16TH DISTRICT", "REP Governor", followed by "Vote For N".
  - The U.S. Senate contest prints a spurious "16TH DISTRICT" suffix
    (a county data-entry artifact also attached to the U.S. House
    title); the senator contest is normalized to office "U.S. Senate"
    with NO district, while "Representative in Congress" carries the
    parsed congressional district number.
  - Statistics block: "Registered Voters - Total/Democratic/Republican/
    NONPARTISAN" (value on the same line); "Ballots Cast - Total/
    Democratic/Republican/NONPARTISAN/Blank" and turnout percent lines
    are printed with their values on the numbers-only line ABOVE the
    label (the parser buffers numbers-only lines).
  - Candidate rows: "<NAME> TOTAL PCT ED MAIL PROV" (VOTE % column
    stripped). "Write-In Totals" -> candidate "Write Ins" carrying the
    contest's party (primary convention: party-empty write-in rows fail
    the duplicate_entries test); "Total Votes Cast" and "Contest
    Totals" skipped.
  - "Member of Democratic/Republican State Committee" matches the ENR
    anchor office names exactly (senatorial district 10); the district
    is assigned from the anchor.
  - Local party-office contests (e.g. "REPUBLICAN PRECINCT COMMITTEEMAN
    HARRISVILLE BORO") are kept as printed, minus the DEM/REP prefix,
    with contest party DEM/REP.
  - A local ballot question ("HARRISVILLE BORO QUESTION", YES/NO rows)
    prints without a party prefix; office is kept as printed, party
    empty (same convention as the 2022 Philadelphia primary questions).
  - Precinct names are kept as printed (e.g. "ADAMS TWP 1",
    "BUTLER CITY 4-1"), matching the county's existing files.

Usage:
    python parsers/pa_butler_primary_2022_results_parser.py \
        "<input.txt|input.pdf>" "<output.csv>"
"""

import csv
import re
import subprocess
import sys
import tempfile
from pathlib import Path

COUNTY = "Butler"

FIELDNAMES = [
    "county", "precinct", "office", "district", "party", "candidate",
    "votes", "election_day", "mail", "provisional",
]

JUNK_SUBSTR = (
    "Summary Results Report",
    "Precinct Summary Report",
    "OFFICIAL RESULTS",
    "Official Results",
    "County of Butler",
    "Report generated with Electionware",
    "Precinct Summary - ",
    "Election Summary - ",
)
FOOTER_RE = re.compile(r"\bPage \d+ of \d+\b")
HEADER_RE = re.compile(r"(?i)^General Primary$|^2022 Primary Election$|^May 17, 2022\b")
COLUMN_HEADER_RE = re.compile(
    r"(?i)\b(?:VOTE %|Mail Votes|Provisional Votes|Election Day)\b")
STAT_MARKER_RE = re.compile(r"^STATISTICS\b", re.IGNORECASE)
VOTE_FOR_RE = re.compile(r"Vote For (\d+)", re.IGNORECASE)
PARTY_PREFIX_RE = re.compile(r"^(DEM|REP)\s+(\S.*)$")
NUM_TOKEN_RE = re.compile(r"^\d[\d,]*(?:\.\d+)?%?$")

US_SENATE_RE = re.compile(r"^United States Senator\b", re.IGNORECASE)
GOVERNOR_RE = re.compile(r"^Governor$", re.IGNORECASE)
LT_GOVERNOR_RE = re.compile(r"^Lieutenant Governor$", re.IGNORECASE)
CONGRESS_RE = re.compile(
    r"^Representative in Congress\s+(\d+)(?:st|nd|rd|th)?\s+(?:Congressional\s+)?District$",
    re.IGNORECASE,
)
ASSEMBLY_RE = re.compile(
    r"^Representative in the General Assembly\s+(\d+)(?:st|nd|rd|th)?\s+Legislative District$",
    re.IGNORECASE,
)
STATE_COMMITTEE_RE = re.compile(
    r"^Member of (Democratic|Republican) State Committee$", re.IGNORECASE)
# Spurious congressional-district suffix on the U.S. Senate title.
SENATE_DISTRICT_SUFFIX_RE = re.compile(
    r"^(United States Senator)\s+\d+(?:st|nd|rd|th)?\s+District$",
    re.IGNORECASE,
)
# Ballot question contests print without a party prefix.
QUESTION_RE = re.compile(r".+\bQUESTION$", re.IGNORECASE)

STATE_COMMITTEE_DISTRICT = "10"  # per the ENR county anchor


def normalize_office(title: str):
    """(office title minus DEM/REP prefix) -> (office, district)."""
    title = re.sub(r"\s+", " ", title).strip()
    if US_SENATE_RE.match(title) or SENATE_DISTRICT_SUFFIX_RE.match(title):
        return ("U.S. Senate", "")
    if GOVERNOR_RE.match(title):
        return ("Governor", "")
    if LT_GOVERNOR_RE.match(title):
        return ("Lieutenant Governor", "")
    m = CONGRESS_RE.match(title)
    if m:
        return ("U.S. House", m.group(1))
    m = ASSEMBLY_RE.match(title)
    if m:
        return ("State House", m.group(1))
    m = STATE_COMMITTEE_RE.match(title)
    if m:
        return (f"Member of {m.group(1).title()} State Committee",
                STATE_COMMITTEE_DISTRICT)
    if QUESTION_RE.match(title):
        return (title, "")
    # Local party offices (e.g. "Republican Precinct Committeeman
    # Harrisville Boro") keep as printed.
    return (title, "")


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
            if HEADER_RE.match(s) or FOOTER_RE.search(s) or COLUMN_HEADER_RE.match(s):
                continue
            name = s
            break
        yield name, lines[start:bounds[k + 1]]


def parse_segment(name, seg, warnings, all_names=frozenset()):
    seg = [ln.strip() for ln in seg]
    n = len(seg)

    # Office headers: lines whose next non-empty line is "Vote For N".
    # (A numbers-only line between header and "Vote For N" is a
    # buffered statistics value, not a header -- scan past them.)
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
            elif QUESTION_RE.match(line):
                office, district = normalize_office(line)
                contest = (office, district, "")
                continue
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
        if line == name.strip() or (all_names and line in all_names):
            # Repeated page header (continuation page or the next
            # precinct's name line at this segment's end).
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
    # All precinct names; a segment ends with the NEXT precinct's name
    # line (page-break layout), which must not be parsed as a candidate
    # row when that name ends in a number (e.g. "ADAMS TWP 2").
    all_names = set()
    for i, ln in enumerate(lines):
        if STAT_MARKER_RE.match(ln.strip()):
            for j in range(i - 1, -1, -1):
                s = lines[j].strip()
                if not s or STAT_MARKER_RE.match(s):
                    continue
                if any(sub in s for sub in JUNK_SUBSTR) or FOOTER_RE.search(s) \
                        or HEADER_RE.match(s) or COLUMN_HEADER_RE.match(s):
                    continue
                all_names.add(s)
                break
    for raw_name, seg in parse_segments(lines):
        if raw_name is None:
            warnings.append("unnamed statistics segment skipped")
            continue
        rows.extend(parse_segment(raw_name, seg, warnings, all_names))
        counts[raw_name] = counts.get(raw_name, 0) + 1
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