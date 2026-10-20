#!/usr/bin/env python3
"""
Parse Lehigh County PA 2022 General Primary Election precinct results.

Source: "Lehigh County Precinct Results 2022 Primary.pdf" (unofficial,
generated 6/02/2022 3:15 PM -- PRE-recount for the Republican U.S. Senate
race; no Lehigh recount/amended per-precinct file exists in the sources).

Format ("Summary Report Contest by Precinct", same vendor report family as
the Lehigh 2022 General export): a bare precinct-name line, then per
contest a party-coded office line ("DEM United States Senator"), a
"Candidate Name / Party / Total Vote" header, and rows
"Alex Khalil    Democrat    15" or "Write-in    Democrat    1". The page
header ("Page N of" + date line) and contest column header repeat on every
page. Only a Total Vote column is present (no election-day/mail/provisional
breakdown at precinct level), so output uses the 7-column schema.

U.S. House: the source's office line carries no district number; Lehigh's
only 2022 primary congressional contest (Wild/Scheller/Dellicker totals
match the 7th District contest) is the 7th Congressional District, so
district "7" is assigned and documented.

State Committee: "Member of the Democratic State Committee" (statewide,
district empty), "Member of the Republican State Committee - At Large"
(district empty), and "Member of the Republican State Committee District
1/2" (district 1/2).

Write-in rows carry the contest's party code (DEM/REP), per primary
format rules.

Usage:
    python parsers/pa_lehigh_primary_2022_results_parser.py \
        "<input.pdf>" "<output.csv>"
"""

import csv
import re
import subprocess
import sys
import warnings
from collections import Counter
from pathlib import Path

PARTY_WORDS = {
    "Democrat": "DEM",
    "Republican": "REP",
    "Libertarian": "LIB",
    "Green": "GRN",
    "Independent": "IND",
    "Nonpartisan": "NON",
}
PALT = "|".join(PARTY_WORDS)

# "Alex Khalil    Democrat    15" / "Conor Lamb Democrat 9" / "Write-in Republican 0"
CAND_ROW_RE = re.compile(
    r"^(.+?)\s+(%s)\s+([\d,]+)\s*$" % PALT
)
CAND_HDR_RE = re.compile(r"^Candidate Name\s+Party\s+Total\s*Vote\s*$")
# Page footer date line, e.g. "6/02/2022 3:15 PM" (day may be 1-2 digits).
PAGE_DATE_RE = re.compile(r"^\d{1,2}/\d{2}/\d{4} \d{1,2}:\d{2} (AM|PM)")
# Office titles seen before the "DEM "/"REP " prefix.
STATE_SEN_RE = re.compile(
    r"^Senator [Ii]n the General Assembly (\d+)", re.I)
STATE_HOUSE_RE = re.compile(
    r"^Representative in the General Assembly (\d+)", re.I)
STATE_CTTE_DIST_RE = re.compile(
    r"^Member of the Republican State Committee District (\d+)$")
OFFICE_EXACT = {
    "United States Senator": ("U.S. Senate", ""),
    "Governor": ("Governor", ""),
    "Lieutenant Governor": ("Lieutenant Governor", ""),
    "Representative in Congress": ("U.S. House", "7"),
    "Member of the Democratic State Committee": (
        "Member of Democratic State Committee", ""),
    "Member of the Republican State Committee - At Large": (
        "Member of Republican State Committee", ""),
}


def normalize_office(titles):
    """Map office title text (after the DEM/REP prefix) to (office, district)."""
    u = " ".join(titles.split())
    m = STATE_SEN_RE.match(u)
    if m:
        return "State Senate", m.group(1)
    m = STATE_HOUSE_RE.match(u)
    if m:
        return "State House", m.group(1)
    if u in OFFICE_EXACT:
        return OFFICE_EXACT[u]
    m = STATE_CTTE_DIST_RE.match(u)
    if m:
        return "Member of Republican State Committee", m.group(1)
    return None


def tidy_name(name):
    name = " ".join(name.split())
    if name.isupper():
        name = " ".join(w if len(w) <= 1 else w.capitalize()
                        for w in name.split())
    return name


def parse(text, county="Lehigh"):
    rows = []
    counts = Counter()
    precinct = None
    office_obj = None  # (office, district)
    contest_party = None  # DEM/REP of the current contest
    warnings_warned = 0
    unclassified = Counter()
    precinct_names = set()
    for raw in text.split("\n"):
        s = raw.rstrip()
        if not s or not s.strip():
            continue
        st = s.strip()
        # Fixed header/footer lines (repeat every page).
        if st.startswith("Page ") and re.match(r"^Page \d+ of", st):
            continue
        if PAGE_DATE_RE.match(st):
            continue
        if st in ("May 2022 Primary Election (05/17/2022)",
                  "Summary Report Contest by Precinct",
                  "Unofficial Election Results", "LEHIGH"):
            continue
        if CAND_HDR_RE.match(st):
            continue
        # Office header line: "<DEM|REP|...> <office titles>"
        words = st.split()
        if words[0] in ("DEM", "REP", "LIB", "GRN", "KEY", "CON", "IND"):
            candidate_titles = " ".join(words[1:])
            mapped = (
                normalize_office(candidate_titles)
                if precinct is not None else None
            )
            if mapped:
                office_obj, contest_party = mapped, words[0]
                continue
            # Not a recognized office title with a precinct on file: if this
            # is the first office block before any precinct (page 1 header),
            # or an unrecognized title, report and treat as non-office line.
            if precinct is None:
                office_obj = None
                continue
        # Candidate / write-in row.
        m = CAND_ROW_RE.match(st)
        if m:
            if office_obj is None or contest_party is None:
                warnings_warned += 1
                if warnings_warned <= 10:
                    warnings.warn(
                        "candidate row with no active contest: " + st[:60])
                continue
            name_cell = m.group(1).strip()
            party_word = m.group(2)
            votes = int(m.group(3).replace(",", ""))
            if name_cell.lower().startswith("write-in"):
                candidate = "Write Ins"
            else:
                candidate = tidy_name(name_cell)
            expected = PARTY_WORDS.get(party_word, "")
            if expected != contest_party:
                warnings_warned += 1
                if warnings_warned <= 10:
                    warnings.warn(
                        f"row party {party_word} != contest {contest_party}: "
                        + st[:60]
                    )
                continue
            office, district = office_obj
            rows.append({
                "county": county,
                "precinct": precinct,
                "office": office,
                "district": district,
                "party": contest_party,
                "candidate": candidate,
                "votes": votes,
            })
            counts[(office, district, contest_party, candidate)] += votes
            continue
        # Anything else is a precinct-name line.
        precinct_names.add(st)
        precinct, office_obj, contest_party = st, None, None
    if unclassified:
        print("Unclassified lines: " + repr(dict(unclassified)), file=sys.stderr)
    return rows, counts, precinct_names


def main():
    if len(sys.argv) != 3:
        sys.exit(f"Usage: {Path(sys.argv[0]).name} <input.pdf> <output.csv>")
    text = subprocess.run(
        ["pdftotext", "-layout", sys.argv[1], "-"], capture_output=True, text=True
    ).stdout
    rows, counts, precinct_names = parse(text)
    with open(sys.argv[2], "w", newline="") as fh:
        w = csv.DictWriter(
            fh,
            fieldnames=["county", "precinct", "office", "district",
                        "party", "candidate", "votes"],
        )
        w.writeheader()
        w.writerows(rows)
    contests = Counter((r["office"], r["district"], r["party"]) for r in rows)
    print(f"Wrote {len(rows)} rows to {sys.argv[2]}")
    print(f"Distinct precincts: {len(precinct_names)}; contests: {len(contests)}")
    bad = {c: n for c, n in contests.items() if n != len(precinct_names)}
    if bad:
        print(f"ConTESTS with row count != precinct count: {bad}", file=sys.stderr)


if __name__ == "__main__":
    main()