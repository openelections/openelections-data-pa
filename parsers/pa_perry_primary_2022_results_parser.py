#!/usr/bin/env python3
"""
Parse Perry County PA 2022 Primary Election precinct results.

Source: "Perry PA 2022 Primary Election Results w Precinct Detail.pdf"
(Dominion "Statement of Votes Cast by Geography - Official" report,
PA_Perry_2022P, May 17, 2022; same Electionware SOVC-by-Geography family as
the 2022 general report). 31 precincts in "Precinct <NAME>" blocks; contest
headers carry a ballot-party suffix:

    UNITED STATES SENATOR-R (REP) (Vote for 1)
    REPRESENTATIVE IN CONGRESS-D (DEM) (Vote for 1)
    DEMOCRATIC COUNTY COMMITTEE-BLAIN (DEM) (Vote for 2)
    GREENWOOD TOWNSHIP REFERENDUM (Vote for 1)

Candidate rows are "NAME  TOTAL  ED  MI  PR" (no percent column, unlike the
2022 general report). Multi-slot write-ins (Write-in1, Write-in2) are merged
into a single "Write Ins" row per contest. The report carries no
per-precinct registered-voters/ballots-cast lines.

NOTE: this report is dated 2022-05-20, i.e. BEFORE the June Republican
U.S. Senate recount (Oz/McCormick); no separate Perry recount/amended
precinct source exists for this county, so the GOP U.S. Senate rows stay
pre-recount (residual vs the certified anchor is documented separately).

Usage:
    uv run python parsers/pa_perry_primary_2022_results_parser.py \
        "<input.pdf>" "<output.csv>"

Validation: each contest block's printed "Total" row is compared against the
sum of that block's candidate rows; mismatches are reported on stderr and
abort the run. The county-summary SOVC PDF (All Precincts geography) can be
parsed with the same code for cross-checking.
"""

import csv
import re
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

PRECRE = re.compile(r"^Precinct\s+(.+)$")
CONTEST_RE = re.compile(r"^(.+?)\s*(?:\((DEM|REP)\)\s*)?\(Vote for (\d+)\)\s*$")
# Candidate data row: "NAME  TOTAL  ED  MI  PR" (4 numbers, no percent column).
CAND_RE = re.compile(r"^(.+?)\s+(\d[\d,]*)\s+(\d[\d,]*)\s+(\d[\d,]*)\s+(\d[\d,]*)\s*$")

COUNTY_COMMITTEE_RE = re.compile(
    r"^(DEMOCRATIC|REPUBLICAN)\s+COUNTY\s+COMMITTEE\s*-?\s*(.+)$", re.IGNORECASE
)

# Ballot-party suffix on contest titles: "UNITED STATES SENATOR-D", "-R".
BALLOT_SUFFIX_RE = re.compile(r"\s*-\s*[DR]$", re.IGNORECASE)

# Districts are NOT printed in the primary contest headers (headers end with
# the ballot-party suffix). Perry County lies wholly within a single district
# for each race: CD-13, SD-34, HD-86 (printed in the county's 2022-general
# SOVC report and confirmed by the certified ENR anchor).
LEGISLATIVE_DISTRICTS = {
    "U.S. House": "13",
    "State Senate": "34",
    "State House": "86",
}

SKIP_PREFIXES = (
    "Statement of Votes Cast by Geography",
    "Perry County, PA, PA_Perry_",
    "All Precincts, All Districts",
    "Total Ballots Cast:",
    "Choice",
)


def normalize_office(raw):
    """Return (office, district) from a cleaned contest title."""
    name = raw.strip()
    up = name.upper()
    if up.startswith("UNITED STATES SENATOR"):
        return "U.S. Senate", ""
    if up.startswith("REPRESENTATIVE IN CONGRESS"):
        return "U.S. House", LEGISLATIVE_DISTRICTS["U.S. House"]
    if up.startswith("SENATOR IN THE GENERAL ASSEMBLY"):
        return "State Senate", LEGISLATIVE_DISTRICTS["State Senate"]
    if up.startswith("REPRESENTATIVE IN THE GENERAL ASSEMBLY"):
        return "State House", LEGISLATIVE_DISTRICTS["State House"]
    if up.startswith("LIEUTENANT GOVERNOR"):
        return "Lieutenant Governor", ""
    if up.startswith("GOVERNOR"):
        return "Governor", ""
    if up.startswith("MEMBER OF THE DEMOCRATIC STATE COMMITTEE"):
        return "Member of Democratic State Committee", ""
    if up.startswith("MEMBER OF THE REPUBLICAN STATE COMMITTEE"):
        return "Member of Republican State Committee", ""
    m = COUNTY_COMMITTEE_RE.match(up)
    if m:
        # "DEMOCRATIC COUNTY COMMITTEE-BLAIN" -> "Democratic County Committee Blain"
        return f"{m.group(1).title()} County Committee {m.group(2).title()}", ""
    # Local offices (referendums etc.) as printed, title-cased.
    return name.title(), ""


def title_cand(c):
    if c.lower().startswith("write-in"):
        return "Write Ins"
    words = []
    for w in c.split():
        if w.lower() in ("&", "/"):
            words.append(w)
        elif len(w) == 1:
            words.append(w.capitalize() + ".")
        elif len(w) >= 3 and w[:2].lower() == "mc" and w.isupper():
            words.append("Mc" + w[2:].capitalize())
        elif w.isupper():
            words.append(w.capitalize())
        else:
            words.append(w)
    return " ".join(words)


def parse(text, county="Perry"):
    """Parse SOVC text. Returns (rows, n_mismatches)."""
    rows = []
    mismatches = []
    geo = None            # "Precinct X" or "All Precincts" (countywide)
    office = district = party = ""
    block = []            # candidate rows of the current contest block
    for raw_line in text.split("\n"):
        s = raw_line.strip()
        if not s:
            continue
        if re.match(r"^Page: \d+ of \d+", s) or re.match(r"^\d{4}-\d{2}-\d{2}|^\d{2}:\d{2}:\d{2}", s):
            continue
        m = PRECRE.match(s)
        if m:
            geo = m.group(1).strip()
            office = district = party = ""
            continue
        if s == "All Precincts" or s.startswith("All Precincts "):
            geo = "All Precincts (county)"
            office = district = party = ""
            continue
        if any(s.startswith(sp) for sp in SKIP_PREFIXES):
            continue
        m = CONTEST_RE.match(s)
        if m:
            party = (m.group(2) or "").upper()
            raw_office = BALLOT_SUFFIX_RE.sub("", m.group(1).strip()).strip()
            office, district = normalize_office(raw_office)
            block = []
            continue
        if geo and office:
            m = CAND_RE.match(s)
            if m:
                name = m.group(1).strip()
                votes = int(m.group(2).replace(",", ""))
                if name == "Total":
                    summed = sum(c["votes"] for c in block)
                    if summed != votes:
                        mismatches.append((geo, office, district, party, votes, summed))
                    # write-ins (Write-in, Write-in1, Write-in2, ...) merged into one row
                    named = [c for c in block if not c["name"].lower().startswith("write-in")]
                    wi = [c for c in block if c["name"].lower().startswith("write-in")]
                    for c in named:
                        rows.append({
                            "county": county, "precinct": geo, "office": office,
                            "district": district, "party": party,
                            "candidate": title_cand(c["name"]),
                            "votes": c["votes"], "election_day": c["ed"],
                            "mail": c["mi"], "provisional": c["pr"],
                        })
                    if wi:
                        rows.append({
                            "county": county, "precinct": geo, "office": office,
                            "district": district, "party": party, "candidate": "Write Ins",
                            "votes": sum(c["votes"] for c in wi),
                            "election_day": sum(c["ed"] for c in wi),
                            "mail": sum(c["mi"] for c in wi),
                            "provisional": sum(c["pr"] for c in wi),
                        })
                    block = []
                    office = district = party = ""
                    continue
                block.append({
                    "name": name, "votes": votes,
                    "ed": int(m.group(3).replace(",", "")),
                    "mi": int(m.group(4).replace(",", "")),
                    "pr": int(m.group(5).replace(",", "")),
                })
                continue
        print(f"WARN: unmatched line: {s[:80]}", file=sys.stderr)
    for (g, o, d, p, total, summed) in mismatches:
        print(f"  MISMATCH {g} | {o} {d} [{p}]: printed {total} vs summed {summed}",
              file=sys.stderr)
    print(f"printed-total check: {len(mismatches)} mismatches", file=sys.stderr)
    return rows, len(mismatches)


def main():
    if len(sys.argv) != 3:
        sys.exit(f"Usage: {Path(sys.argv[0]).name} <input.pdf> <output.csv>")
    text = subprocess.run(
        ["pdftotext", "-layout", sys.argv[1], "-"], capture_output=True, text=True
    ).stdout
    rows, bad = parse(text)
    if bad:
        sys.exit(f"{bad} printed-total mismatches; inspect before writing output")
    with open(sys.argv[2], "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["county", "precinct", "office", "district",
                                           "party", "candidate", "votes", "election_day",
                                           "mail", "provisional"])
        w.writeheader()
        w.writerows(rows)
    print(f"Wrote {len(rows)} rows to {sys.argv[2]}")


if __name__ == "__main__":
    main()